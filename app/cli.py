"""Agent-facing helpers: non-interactive build, validate and scaffold.

These functions are what the CLI scripts wrap. Keeping them here makes them
testable without spawning subprocesses or calling a model.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from . import db, validate
from .config import DEFAULT_BASE_URL, DEFAULT_MODEL, DEFAULT_PROVIDER
from .pipeline import run_job
from .proxies import parse_proxies
from .render import network_hosts

BUNDLE_FILES = (
    "scraper.py",
    "mapper.py",
    "runner.sh",
    "README.md",
    "requirements.txt",
    "probe.json",
    "agent_trace.json",
    "raw.jsonl",
    "mapped.jsonl",
)
REQUIRED_FILES = ("scraper.py", "mapper.py", "runner.sh", "README.md", "requirements.txt")


def validate_bundle_dir(path: str | Path) -> list[str]:
    """Validate a hand-written or generated bundle on disk."""
    directory = Path(path)
    errors: list[str] = []
    files: dict[str, str] = {}
    for name in REQUIRED_FILES:
        file = directory / name
        if not file.exists():
            errors.append(f"{name}: missing from {directory}")
            continue
        files[name] = file.read_text(encoding="utf-8", errors="replace")
    if files:
        browser = "playwright" in files.get("scraper.py", "")
        target_url, allowed = None, set()
        probe_file = directory / "probe.json"
        if probe_file.exists():
            try:
                probe = json.loads(probe_file.read_text(encoding="utf-8"))
            except ValueError:
                probe = {}
            target_url = probe.get("final_url") or probe.get("url")
            allowed = network_hosts(probe)
        errors.extend(
            validate.validate_bundle(
                files, browser=browser, target_url=target_url, allowed_hosts=allowed
            )
        )
    return errors


def scaffold_bundle(target: str | Path) -> list[str]:
    """Copy the reference bundle into a directory so an agent can fill it in."""
    directory = Path(target)
    directory.mkdir(parents=True, exist_ok=True)
    reference = Path(__file__).resolve().parent / "reference"

    for name in ("scraper.py", "mapper.py", "runner.sh", "requirements.txt"):
        shutil.copyfile(reference / name, directory / name)

    readme = directory / "README.md"
    if not readme.exists():
        readme.write_text(
            "# <site> scraper\n\n"
            "Install dependencies with `pip install -r requirements.txt`, then run "
            "`bash runner.sh`. Describe the data source, the fields you mapped, and "
            "what is most likely to break when the site changes. Keep the "
            "proxy/retry plumbing from the reference scraper and document every "
            "assumption in NOTES.\n",
            encoding="utf-8",
        )

    proxies = directory / "proxies.json"
    if not proxies.exists():
        proxies.write_text("[]\n", encoding="utf-8")

    return sorted(file.name for file in directory.iterdir() if file.is_file())


def copy_bundle(job_id: str, target: str | Path) -> list[str]:
    """Copy a finished bundle to another directory. Never copies proxies.json."""
    source = db.job_dir(job_id)
    destination = Path(target)
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in BUNDLE_FILES:
        file = source / name
        if file.exists():
            shutil.copyfile(file, destination / name)
            copied.append(name)
    return copied


def _trace_summary(job_dir: Path) -> list[dict[str, Any]]:
    trace_file = job_dir / "agent_trace.json"
    if not trace_file.exists():
        return []
    try:
        trace = json.loads(trace_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    summary = []
    for entry in trace:
        observation = entry.get("observation") or {}
        summary.append(
            {
                "url": entry.get("url"),
                "status": observation.get("status"),
                "content_type": observation.get("content_type"),
            }
        )
    return summary


async def build(
    url: str,
    *,
    fields: str = "",
    instructions: str = "",
    mode: str = "polite",
    proxies: str | list | None = None,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    output_dir: str | Path | None = None,
    browser: str | None = None,
) -> dict[str, Any]:
    """Run one build end-to-end and return a machine-readable result."""
    db.init_db()
    options = {
        "proxies": parse_proxies(proxies),
        "mode": mode,
        "fields": fields,
        "instructions": instructions,
    }
    if browser:
        options["browser"] = browser
    job = db.create_job(
        url,
        provider or DEFAULT_PROVIDER,
        (base_url or DEFAULT_BASE_URL).rstrip("/"),
        model or DEFAULT_MODEL,
        options,
    )
    await run_job(job["id"], api_key)

    final = db.get_job(job["id"]) or job
    job_dir = db.job_dir(job["id"])
    preview = final.get("preview") or {}
    summary = final.get("summary") or {}
    result: dict[str, Any] = {
        "job_id": final["id"],
        "status": final["status"],
        "url": final["url"],
        "mode": mode,
        "provider": final["provider"],
        "model": final["model"],
        "output_dir": str(job_dir),
        "files": [name for name in BUNDLE_FILES if (job_dir / name).exists()],
        "records": {
            "raw": preview.get("raw_count", 0),
            "mapped": preview.get("mapped_count", 0),
        },
        "fill_rates": preview.get("fill_rates") or {},
        "preview": preview.get("rows") or [],
        "cost_usd": final.get("cost_usd") or 0.0,
        "seconds": summary.get("seconds"),
        "source": summary.get("source"),
        "strategy": summary.get("strategy"),
        "agent_trace": _trace_summary(job_dir),
        "error": final.get("error"),
    }

    if output_dir:
        result["copied_to"] = str(Path(output_dir))
        result["copied_files"] = copy_bundle(job["id"], output_dir)

    result_path = job_dir / "result.json"
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    result["result_path"] = str(result_path)
    return result
