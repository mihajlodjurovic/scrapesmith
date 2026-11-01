"""Run one build from the command line.

Usage:
    python scripts/run_build.py <url> [--fields "title, author"] [--proxies proxies.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402
from app.config import (  # noqa: E402
    DEFAULT_BASE_URL,
    DEFAULT_MODE,
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    ENV_API_KEY,
)
from app.pipeline import run_job  # noqa: E402
from app.proxies import parse_proxies  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description="Build a scraper bundle for one URL")
    parser.add_argument("url")
    parser.add_argument("--fields", default="")
    parser.add_argument("--instructions", default="")
    parser.add_argument("--mode", default=DEFAULT_MODE, choices=["polite", "unrestricted"])
    parser.add_argument("--proxies", default="", help="path to a JSON or text proxy list")
    args = parser.parse_args()

    if not ENV_API_KEY:
        print("No OPENCODE_API_KEY in .env.")
        return 1

    proxies = []
    if args.proxies:
        proxies = parse_proxies(Path(args.proxies).read_text(encoding="utf-8"))

    db.init_db()
    job = db.create_job(
        args.url,
        DEFAULT_PROVIDER,
        DEFAULT_BASE_URL,
        DEFAULT_MODEL,
        {
            "proxies": proxies,
            "mode": args.mode,
            "fields": args.fields,
            "instructions": args.instructions,
        },
    )
    print(f"job {job['id']} · {DEFAULT_MODEL} · {len(proxies)} proxies")

    await run_job(job["id"], None)
    final = db.get_job(job["id"])
    assert final is not None

    print("\n--- events ---")
    for event in db.events_since(job["id"]):
        message = event["message"].replace("\n", "\n    ")
        print(f"[{event['level']}] {message}")

    print("\n--- result ---")
    print(f"status : {final['status']}")
    print(f"cost   : ${final['cost_usd']:.4f}")
    print(f"dir    : {db.job_dir(job['id'])}")
    if final.get("error"):
        print(f"error  : {final['error']}")
    preview = final.get("preview") or {}
    if preview.get("rows"):
        print(f"records: {preview.get('mapped_count')} mapped / {preview.get('raw_count')} raw")
        print(json.dumps(preview["rows"][0], indent=2, ensure_ascii=False)[:1200])
    return 0 if final["status"] == "done" else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
