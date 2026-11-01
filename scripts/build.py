"""Non-interactive build for coding agents.

    python scripts/build.py <url> --json --out ./bundles/site

JSON goes to stdout, logs go to stderr, exit code is 0 on success and 2 on
failure. The api key is read from SCRAPESMITH_API_KEY unless --api-key is given.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.cli import build  # noqa: E402


def proxy_input(value: str) -> str:
    path = Path(value)
    if path.exists() and path.is_file():
        return path.read_text(encoding="utf-8")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a scraper bundle for one URL")
    parser.add_argument("url")
    parser.add_argument("--fields", default="", help="comma separated fields you want")
    parser.add_argument("--instructions", default="")
    parser.add_argument("--mode", default="polite", choices=["polite", "unrestricted"])
    parser.add_argument("--proxies", default="", help="path, JSON array, or newline list")
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--out", default="", help="copy the finished bundle here")
    parser.add_argument("--json", action="store_true", help="machine-readable stdout")
    args = parser.parse_args()

    with contextlib.redirect_stdout(sys.stderr):
        result = asyncio.run(
            build(
                args.url,
                fields=args.fields,
                instructions=args.instructions,
                mode=args.mode,
                proxies=proxy_input(args.proxies) if args.proxies else None,
                provider=args.provider,
                model=args.model,
                base_url=args.base_url,
                api_key=args.api_key,
                output_dir=args.out or None,
            )
        )

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"status : {result['status']}")
        print(f"records: {result['records']['mapped']} mapped / {result['records']['raw']} raw")
        print(f"source : {result['source']}")
        print(f"cost   : ${result['cost_usd']:.4f}")
        print(f"bundle : {result['output_dir']}")
        if result.get("copied_to"):
            print(f"copied : {result['copied_to']}")
        if result.get("error"):
            print(f"error  : {result['error']}")

    return 0 if result["status"] == "done" else 2


if __name__ == "__main__":
    raise SystemExit(main())
