"""Copy the reference bundle so it can be edited by hand.

    python scripts/scaffold_bundle.py ./bundles/site

Then edit scraper.py and mapper.py, and validate with:

    python scripts/validate_bundle.py ./bundles/site
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.cli import scaffold_bundle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Scaffold an empty scraper bundle")
    parser.add_argument("path")
    args = parser.parse_args()

    files = scaffold_bundle(args.path)
    print(f"scaffolded {args.path}")
    for name in files:
        print(f"  {name}")
    print("\nNext: edit scraper.py and mapper.py, then run scripts/validate_bundle.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
