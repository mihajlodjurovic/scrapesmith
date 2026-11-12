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
# Browser automation other than Playwright, and anti-detection add-ons, are never
# allowed: ScrapeSmith does not do bot-evasion.
ALWAYS_BANNED_IMPORTS = {
    "pyppeteer",
    "playwright_stealth",
    "selenium",
    "seleniumbase",
    "undetected_chromedriver",
}
# Playwright is allowed only in the browser fallback build.
BROWSER_IMPORTS = {"playwright"}
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

CREDENTIAL_LABELS = {"hardcoded Mongo credentials", "hardcoded proxy credentials"}
# Documentation examples such as mongodb://user:pass@host are not secrets.
PLACEHOLDER = re.compile(
    r"^(user(name)?|usr|login|pass(word)?|pwd|secret|changeme|example|"
    r"<[^>]*>|\{[^}]*\}|\$\{?\w+\}?|%\w+%|x{2,}|\*{2,}|\.{3})$",
    re.I,
)

MAX_FILE_BYTES = 120_000

# Third parties that serve someone else's copy of a page. Using them as a data
# source turns a blocked site into a "successful" build with stale data, which is
# exactly the ban-evasion ScrapeSmith refuses to do.
CACHE_AND_PROXY_SERVICES = (
    "archive.org",
    "archive.ph",
    "archive.today",
    "archive.is",
    "cachedview",
    "webcache.googleusercontent.com",
    "translate.goog",
    "duckduckgo.com",
    "google.com/search",
    "bing.com",
    "yandex.",
    "startpage.com",
    "r.jina.ai",
    "scraperapi",
    "scrapingbee",
    "zenrows",
    "scrapfly",
    "scrapingant",
    "crawlbase",
    "brightdata",
    "zyte.com",
    "oxylabs",
)
# URLs that show up in scraping code without being fetched (schema ids, docs).
NEUTRAL_HOSTS = {"schema.org", "www.schema.org", "w3.org", "www.w3.org", "ogp.me", "example.com"}
URL_LITERAL = re.compile(r"https?://([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def _is_placeholder_credential(match: str) -> bool:
    userinfo = match.split("://", 1)[-1].rsplit("@", 1)[0]
    user, _, password = userinfo.partition(":")
    return bool(PLACEHOLDER.match(user) and PLACEHOLDER.match(password))


def _has_secret(pattern: re.Pattern, label: str, content: str) -> bool:
    for match in pattern.finditer(content):
        if label in CREDENTIAL_LABELS and _is_placeholder_credential(match.group(0)):
            continue
        return True
    return False

# The only files a bundle may contain. Anything else (helper.py, sitecustomize.py,
# requests.py, ...) would sit on the sandbox PYTHONPATH without passing the AST
# checks, so it is rejected and never written to disk.
ALLOWED_FILES = frozenset(
    {"scraper.py", "mapper.py", "runner.sh", "README.md", "requirements.txt", "NOTES"}
)


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


def _import_error(name: str, module: str, label: str, browser: bool) -> str | None:
    root = module.split(".")[0]
    if root in BANNED_IMPORTS or root in ALWAYS_BANNED_IMPORTS:
        return f"{name}: forbidden import '{label}'"
    if root in BROWSER_IMPORTS and not browser:
        return (
            f"{name}: '{label}' — browser automation is only allowed in the browser "
            "fallback build; use requests against the page or its JSON API"
        )
    return None


def _check_ast(tree: ast.Module, name: str, browser: bool = False) -> list[str]:
    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                error = _import_error(name, alias.name, alias.name, browser)
                if error:
                    errors.append(error)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            error = _import_error(name, module, f"from {module}", browser)
            if error:
                errors.append(error)
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


def _string_constants(tree: ast.Module) -> list[str]:
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def source_errors(
    name: str,
    tree: ast.Module,
    target_url: str | None = None,
    allowed_hosts: set[str] | None = None,
) -> list[str]:
    """Data must come from the target site, or an endpoint the page itself calls."""
    from urllib.parse import urlparse

    from .agent import registrable_domain

    errors: list[str] = []
    strings = _string_constants(tree)
    seen: set[str] = set()
    for text in strings:
        lowered = text.lower()
        for service in CACHE_AND_PROXY_SERVICES:
            if service in lowered and service not in seen:
                seen.add(service)
                errors.append(
                    f"{name}: uses '{service}' — data must come from the target site, "
                    "never from archives, search engines, caches or scraping services. "
                    "If the site blocks you, let the scraper fail and say so in NOTES."
                )

    target_host = (urlparse(target_url or "").hostname or "").lower()
    if not target_host:
        return errors
    target_domain = registrable_domain(target_host)
    allowed = {host.lower() for host in (allowed_hosts or set())} | NEUTRAL_HOSTS
    foreign: list[str] = []
    for text in strings:
        for host in URL_LITERAL.findall(text):
            host = host.lower().rstrip(".")
            if host in allowed or registrable_domain(host) == target_domain:
                continue
            if any(service in host for service in CACHE_AND_PROXY_SERVICES):
                continue  # already reported above
            if host not in foreign:
                foreign.append(host)
    if foreign:
        errors.append(
            f"{name}: fetches hosts outside {target_domain}: {', '.join(foreign[:5])} — "
            "only the target site and endpoints the page itself calls are allowed"
        )
    return errors


def validate_bundle(
    files: dict[str, str],
    browser: bool = False,
    target_url: str | None = None,
    allowed_hosts: set[str] | None = None,
) -> list[str]:
    """Static checks. `browser=True` is the headless-browser fallback build.

    `target_url` enables the data-source check: URLs in the code must belong to
    the target's registrable domain or to `allowed_hosts` (hosts the rendered page
    called, or that the agent verified).
    """
    errors: list[str] = []

    unexpected = sorted(name for name in files if name not in ALLOWED_FILES)
    if unexpected:
        errors.append(
            f"unexpected files {', '.join(unexpected)} — put all code in scraper.py "
            "and mapper.py; only scraper.py, mapper.py, runner.sh, README.md and "
            "requirements.txt are allowed"
        )

    for name, content in files.items():
        if len(content.encode("utf-8")) > MAX_FILE_BYTES:
            errors.append(f"{name}: file is larger than {MAX_FILE_BYTES} bytes")
        for pattern, label in SECRET_PATTERNS:
            if _has_secret(pattern, label, content):
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
        errors.extend(_check_ast(tree, name, browser))
        errors.extend(source_errors(name, tree, target_url, allowed_hosts))

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

    requirements = files.get("requirements.txt")
    if not requirements:
        errors.append(
            "requirements.txt: missing — ship the dependencies the scraper imports"
        )
    else:
        declared = requirements.lower()
        scraper_source = files.get("scraper.py", "")
        if ("import requests" in scraper_source or "from requests" in scraper_source) and (
            "requests" not in declared
        ):
            errors.append("requirements.txt: missing 'requests'")
        if ("BeautifulSoup" in scraper_source or "bs4" in scraper_source) and (
            "beautifulsoup4" not in declared and "bs4" not in declared
        ):
            errors.append("requirements.txt: missing 'beautifulsoup4'")
        if "playwright" in scraper_source and "playwright" not in declared:
            errors.append("requirements.txt: missing 'playwright'")

    runner = files.get("runner.sh", "")
    if runner:
        if "scraper.py" not in runner or "mapper.py" not in runner:
            errors.append("runner.sh: must run both scraper.py and mapper.py")

    readme = files.get("README.md", "")
    if len(readme) < 150:
        errors.append("README.md: too short — document source, fields and caveats")

    return errors
