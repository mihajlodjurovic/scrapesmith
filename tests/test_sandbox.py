"""Sandbox: runs a bundle in a subprocess and previews mapped output."""

from __future__ import annotations

from pathlib import Path

from app.sandbox import run_bundle

SCRAPER = '''
import argparse
import json

DELAY = 0.2


class ProxyPool:
    def __init__(self, proxies):
        self.proxies = proxies


def request(*args, **kwargs):
    return None


def parse_items(payload):
    return payload


def fetch_page(page):
    return [
        {"id": "1", "title": "VW Golf", "url": "https://example.com/1",
         "price": "12.900 EUR", "location": "Berlin",
         "images": ["https://example.com/1.jpg"], "make": "VW", "mileage": "87.400 km"},
        {"id": "2", "title": "Audi A3", "url": "https://example.com/2",
         "price": "15.500 EUR", "location": "Munich",
         "images": ["https://example.com/2.jpg"], "make": "Audi", "mileage": "60.000 km"},
    ]


def run(max_pages, out_path):
    with open(out_path, "w", encoding="utf-8") as handle:
        for item in fetch_page(1):
            handle.write(json.dumps(item) + "\\n")
    return 2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--out", default="raw.jsonl")
    parser.add_argument("--proxies", default="proxies.json")
    parser.add_argument("--mongo", default=None)
    args = parser.parse_args()
    run(args.max_pages, args.out)


if __name__ == "__main__":
    main()
'''


def bundle() -> dict[str, str]:
    reference = Path(__file__).resolve().parents[1] / "app" / "reference"
    return {
        "scraper.py": SCRAPER,
        "mapper.py": (reference / "mapper.py").read_text(encoding="utf-8"),
        "runner.sh": (reference / "runner.sh").read_text(encoding="utf-8"),
        "README.md": (
            "# Test bundle\n\n"
            "Runs fully offline and maps two records so the sandbox can be tested without "
            "network access. The proxy list is empty, the delay is zero, and the mapper uses "
            "the reference canonical schema end to end.\n"
        ),
    }


def test_bundle_runs_and_previews(tmp_path: Path) -> None:
    result = run_bundle(tmp_path, bundle(), [], max_pages=1, timeout=60)
    assert result["ok"] is True, result["errors"]
    assert result["raw_count"] == 2
    assert result["mapped_count"] == 2
    assert len(result["preview"]) == 2
    assert result["fill_rates"]["title"] == 100.0
    assert result["fill_rates"]["price"] == 100.0
    assert result["fill_rates"]["currency"] == 100.0
    assert result["fill_rates"]["images"] == 100.0
    assert result["fill_rates"]["description"] == 0.0
    assert result["preview"][0]["attributes"]["make"] == "VW"
    assert (tmp_path / "proxies.json").exists()


def test_broken_scraper_reports_stage_and_tail(tmp_path: Path) -> None:
    files = bundle()
    files["scraper.py"] = SCRAPER.replace(
        'handle.write(json.dumps(item) + "\\n")', "raise RuntimeError('boom')"
    )
    result = run_bundle(tmp_path, files, [], max_pages=1, timeout=60)
    assert result["ok"] is False
    assert result["stage"] == "scraper"
    assert any("boom" in error for error in result["errors"])
