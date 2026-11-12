"""Run a generated bundle in a subprocess with limits, then preview the result."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .config import SANDBOX_MAX_OUTPUT_BYTES, SANDBOX_TIMEOUT_SECONDS
from .validate import ALLOWED_FILES, canonical_required


def _safe_env(job_dir: Path) -> dict[str, str]:
    keep = {
        "PATH",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "LANG",
        # Playwright needs these to find its browser build (browser fallback only).
        "LOCALAPPDATA",
        "PLAYWRIGHT_BROWSERS_PATH",
        "XDG_CACHE_HOME",
    }
    env = {key: value for key, value in os.environ.items() if key in keep}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(job_dir)
    env["PROXIES_FILE"] = str(job_dir / "proxies.json")
    return env


def _tail(text: str, limit: int = 4000) -> str:
    return text[-limit:] if len(text) > limit else text


def _read_jsonl(path: Path, limit: int | None = None) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
            if limit and len(rows) >= limit:
                break
    return rows


def _count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        return sum(1 for line in handle if line.strip())


def _is_filled(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return True


def _fill_rates(rows: list[dict]) -> dict[str, float]:
    if not rows:
        return {}
    rates = {}
    for key in canonical_required():
        filled = sum(1 for row in rows if _is_filled(row.get(key)))
        rates[key] = round(filled * 100 / len(rows), 1)
    return rates


def _run(cmd: list[str], cwd: Path, timeout: int) -> tuple[int, str, str, float]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(cwd),
            env=_safe_env(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        elapsed = time.perf_counter() - started
        stdout = _tail(completed.stdout)
        stderr = _tail(completed.stderr)
        return completed.returncode, stdout, stderr, elapsed
    except subprocess.TimeoutExpired as exc:
        elapsed = time.perf_counter() - started
        print(f"[sandbox] timeout after {elapsed:.1f}s: {' '.join(cmd)}")
        stdout = _tail(exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = _tail(exc.stderr or "") if isinstance(exc.stderr, str) else ""
        return 124, stdout, stderr + f"\nTimed out after {timeout}s.", elapsed


def write_bundle(job_dir: Path, files: dict[str, str], proxies: list[str]) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        if name not in ALLOWED_FILES:
            continue  # never put unvalidated modules on the sandbox PYTHONPATH
        if name == "NOTES":
            (job_dir / "NOTES.md").write_text(content, encoding="utf-8")
            continue
        (job_dir / name).write_text(content, encoding="utf-8")
    (job_dir / "proxies.json").write_text(
        json.dumps(proxies, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def run_bundle(
    job_dir: Path,
    files: dict[str, str],
    proxies: list[str],
    max_pages: int = 1,
    timeout: int = SANDBOX_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    write_bundle(job_dir, files, proxies)
    # A previous attempt's output must never count as this attempt's proof.
    for stale in ("raw.jsonl", "mapped.jsonl"):
        (job_dir / stale).unlink(missing_ok=True)
    result: dict[str, Any] = {
        "ok": False,
        "stage": "compile",
        "errors": [],
        "stdout": "",
        "stderr": "",
        "raw_count": 0,
        "mapped_count": 0,
        "preview": [],
        "fill_rates": {},
        "duration_seconds": 0.0,
    }
    started = time.perf_counter()

    for name in ("scraper.py", "mapper.py"):
        code, out, err, _ = _run(
            [sys.executable, "-m", "py_compile", name], job_dir, timeout=60
        )
        if code != 0:
            result["errors"].append(f"{name} does not compile: {_tail(err, 1200)}")
            result["stderr"] = err
            result["duration_seconds"] = round(time.perf_counter() - started, 1)
            return result

    code, out, err, _ = _run(
        [
            sys.executable,
            "scraper.py",
            "--max-pages",
            str(max_pages),
            "--out",
            "raw.jsonl",
            "--proxies",
            "proxies.json",
        ],
        job_dir,
        timeout=timeout,
    )
    result["stdout"] = out
    result["stderr"] = err
    result["stage"] = "scraper"
    raw_count = _count_lines(job_dir / "raw.jsonl")
    result["raw_count"] = raw_count

    if code != 0 or raw_count == 0:
        reason = "scraper exited non-zero" if code != 0 else "scraper wrote no records"
        result["errors"].append(
            f"{reason}. stderr tail:\n{_tail(err, 2500)}\nstdout tail:\n{_tail(out, 1200)}"
        )
        result["duration_seconds"] = round(time.perf_counter() - started, 1)
        return result

    code, out2, err2, _ = _run(
        [sys.executable, "mapper.py", "--in", "raw.jsonl", "--out", "mapped.jsonl"],
        job_dir,
        timeout=timeout,
    )
    result["stage"] = "mapper"
    result["stdout"] = _tail(out + "\n" + out2)
    result["stderr"] = _tail(err + "\n" + err2)
    mapped_count = _count_lines(job_dir / "mapped.jsonl")
    result["mapped_count"] = mapped_count

    if code != 0 or mapped_count == 0:
        reason = "mapper exited non-zero" if code != 0 else "mapper wrote no records"
        result["errors"].append(
            f"{reason}. stderr tail:\n{_tail(err2, 2500)}\nstdout tail:\n{_tail(out2, 1200)}"
        )
        result["duration_seconds"] = round(time.perf_counter() - started, 1)
        return result

    rows = _read_jsonl(job_dir / "mapped.jsonl")
    preview = []
    for row in rows[:5]:
        rendered = dict(row)
        if isinstance(rendered.get("images"), list):
            rendered["images"] = rendered["images"][:2]
        preview.append(rendered)

    result["preview"] = preview
    result["fill_rates"] = _fill_rates(rows)
    result["ok"] = True
    result["stage"] = "done"
    result["duration_seconds"] = round(time.perf_counter() - started, 1)
    result["size_bytes"] = min(
        sum(
            (job_dir / name).stat().st_size
            for name in ("raw.jsonl", "mapped.jsonl")
            if (job_dir / name).exists()
        ),
        SANDBOX_MAX_OUTPUT_BYTES,
    )
    return result
