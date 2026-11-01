"""Static validation of a generated bundle — fast, deterministic, no network."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

BANNED_IMPORTS = {
    "ctypes",
    "multiprocessing",
    "pickle",
    "pty",
    "shutil",
    "socket",
    "subprocess",
}
BANNED_CALLS = {"eval", "exec", "compile", "__import__"}
BANNED_ATTRIBUTES = {("os", "system"), ("os", "popen"), ("os", "remove"), ("os", "rmdir")}

SECRET_PATTERNS = (
    (re.compile(r"mongodb(?:\+srv)?://[^\s\"']+:[^\s\"']+@"), "hardcoded Mongo credentials"),
    (re.compile(r"sk-[A-Za-z0-9]{16,}"), "hardcoded API key"),
    (re.compile(r"gsk_[A-Za-z0-9]{20,}"), "hardcoded API key"),
    (re.compile(r"sk-or-v1-[A-Za-z0-9]{16,}"), "hardcoded API key"),
    (
        re.compile(
            r"https?://[^\s\"'/]+:[^\s\"'/]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?::\d+)?"
        ),
        "hardcoded proxy credentials",
    ),
    (
        re.compile(r"https?://[^\s\"'/]+:[^\s\"'/]+@\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?"),
        "hardcoded proxy credentials",
    ),
)

MAX_FILE_BYTES = 120_000


def canonical_required() -> list[str]:
    schema = json.loads(
        (Path(__file__).resolve().parent / "reference" / "schema.json").read_text(
            encoding="utf-8"
        )
    )
    return list(schema["required"])


def _parse(source: str, name: str) -> tuple[ast.Module | None, list[str]]:
    try:
        return ast.parse(source), []
    except SyntaxError as exc:
        return None, [f"{name}: syntax error on line {exc.lineno}: {exc.msg}"]


def _check_ast(tree: ast.Module, name: str) -> list[str]:
    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in BANNED_IMPORTS:
                    errors.append(f"{name}: forbidden import '{alias.name}'")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in BANNED_IMPORTS:
                errors.append(f"{name}: forbidden import 'from {node.module}'")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in BANNED_CALLS:
                errors.append(f"{name}: forbidden call '{func.id}()'")
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and (func.value.id, func.attr) in BANNED_ATTRIBUTES
            ):
                errors.append(f"{name}: forbidden call '{func.value.id}.{func.attr}()'")
    return errors


def _defined_functions(tree: ast.Module) -> set[str]:
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _canonical_keys(tree: ast.Module) -> list[str] | None:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "CANONICAL_KEYS":
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, SyntaxError):
                    return None
                return [str(item) for item in value]
    return None


def validate_bundle(files: dict[str, str]) -> list[str]:
    errors: list[str] = []

    for name, content in files.items():
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            errors.append(f"{name}: file is larger than {MAX_FILE_BYTES} bytes")
        for pattern, label in SECRET_PATTERNS:
            if pattern.search(content):
                errors.append(f"{name}: {label} found — use environment variables instead")
        if "verify=False" in content.replace(" ", ""):
            errors.append(f"{name}: TLS verification disabled (verify=False)")

    trees: dict[str, ast.Module] = {}
    for name in ("scraper.py", "mapper.py"):
        content = files.get(name)
        if not content:
            errors.append(f"{name}: missing")
            continue
        tree, parse_errors = _parse(content, name)
        errors.extend(parse_errors)
        if tree is None:
            continue
        trees[name] = tree
        errors.extend(_check_ast(tree, name))

    scraper_tree = trees.get("scraper.py")
    if scraper_tree is not None:
        functions = _defined_functions(scraper_tree)
        for required in ("fetch_page", "parse_items", "request", "run", "main"):
            if required not in functions:
                errors.append(f"scraper.py: missing function '{required}()' from the contract")
        source = files.get("scraper.py", "")
        if "ProxyPool" not in source:
            errors.append("scraper.py: keep the ProxyPool class so proxies are always used")
        if "DELAY" not in source:
            errors.append("scraper.py: keep the DELAY constant (0.2s default)")

    mapper_tree = trees.get("mapper.py")
    if mapper_tree is not None:
        functions = _defined_functions(mapper_tree)
        for required in ("map_listing", "run", "main"):
            if required not in functions:
                errors.append(f"mapper.py: missing function '{required}()' from the contract")
        keys = _canonical_keys(mapper_tree)
        required_keys = canonical_required()
        if keys is None:
            errors.append("mapper.py: CANONICAL_KEYS must be a literal list of field names")
        else:
            missing = [key for key in required_keys if key not in keys]
            if missing:
                errors.append(f"mapper.py: CANONICAL_KEYS is missing {', '.join(missing)}")

    runner = files.get("runner.sh", "")
    if runner:
        if "scraper.py" not in runner or "mapper.py" not in runner:
            errors.append("runner.sh: must run both scraper.py and mapper.py")

    readme = files.get("README.md", "")
    if len(readme) < 150:
        errors.append("README.md: too short — document source, fields and caveats")

    return errors
