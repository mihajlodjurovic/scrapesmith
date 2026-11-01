"""Validate a bundle directory against the ScrapeSmith contract.

    python scripts/validate_bundle.py ./bundles/site [--json]

Exit code 0 = valid, 1 = invalid. No network, no model call.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.cli import validate_bundle_dir  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a scraper bundle")
    parser.add_argument("path")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    errors = validate_bundle_dir(args.path)
    if args.json:
        print(json.dumps({"ok": not errors, "errors": errors}, indent=2))
    elif errors:
        for error in errors:
            print(f"- {error}")
    else:
        print(f"OK: {args.path} satisfies the bundle contract")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
