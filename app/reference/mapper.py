#!/usr/bin/env python3
"""Reference mapper — raw site items into the generic record schema.

Keep the helpers and the CLI. Replace only `map_listing` (and add site-specific
attribute extraction) to match the target site.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin

SOURCE = "example.com"

CANONICAL_KEYS = [
    "id",
    "url",
    "title",
    "price",
    "currency",
    "description",
    "location",
    "images",
    "attributes",
    "source",
    "scraped_at",
]

# Fields that get their own column; everything else lands in `attributes`.
RESERVED = set(CANONICAL_KEYS) | {"_meta", "raw"}

CURRENCY_SYMBOLS = {"€": "EUR", "£": "GBP", "$": "USD", "zł": "PLN"}
CURRENCY_CODES = re.compile(r"\b(EUR|USD|GBP|CHF|PLN|CZK|SEK|NOK|DKK|RON|BGN|HUF)\b", re.I)


def clean_text(value) -> str | None:
    if value is None:
        return None
    text = re.sub(r"<[^>]+>", " ", str(value))
    text = " ".join(text.split())
    return text or None


def parse_number(value) -> float | int | None:
    """Parse '1 234,56 EUR', '1,234.56', '89.99' into a number."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    text = re.sub(r"[^\d,.\-]", "", text)
    if not text or text in {"-", ".", ","}:
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        head, _, tail = text.partition(",")
        text = f"{head}.{tail}" if len(tail) == 2 else text.replace(",", "")
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number.is_integer() else round(number, 2)


def detect_currency(value, text: str = "") -> str | None:
    if value:
        candidate = clean_text(value)
        if candidate:
            return candidate.upper() if len(candidate) <= 3 else candidate
    source = f"{text or ''}"
    code = CURRENCY_CODES.search(source)
    if code:
        return code.group(1).upper()
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in source:
            return code
    return None


def absolute_url(base_url: str, value: str | None) -> str | None:
    if not value:
        return None
    value = str(value).strip()
    if value.startswith(("http://", "https://")):
        return value
    if not base_url:
        return value
    return urljoin(base_url, value)


def image_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    images = []
    for item in value:
        if isinstance(item, dict):
            item = item.get("url") or item.get("src") or item.get("original_url")
        if item:
            images.append(str(item))
    return images[:20]


def stable_id(raw: dict, url: str | None) -> str:
    for key in ("id", "sku", "listing_id", "slug"):
        if raw.get(key):
            return str(raw[key])
    if url:
        return url
    digest = hashlib.sha1(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()
    return digest[:16]


# ─────────────────────────── SITE LOGIC ───────────────────────────
def map_listing(raw: dict, base_url: str = "") -> dict:
    """Translate one raw item into the generic record schema."""
    meta = raw.get("_meta") if isinstance(raw.get("_meta"), dict) else {}
    url = absolute_url(base_url, raw.get("url") or raw.get("link"))

    title = clean_text(
        raw.get("title") or raw.get("name") or raw.get("headline") or raw.get("subject")
    )
    price = parse_number(raw.get("price") or raw.get("amount"))
    currency = detect_currency(
        raw.get("currency"), " ".join(str(raw.get(k) or "") for k in ("price", "price_text"))
    )

    attributes = {}
    for key, value in raw.items():
        if key in RESERVED or key.startswith("_"):
            continue
        cleaned = clean_text(value) if isinstance(value, str) else value
        if cleaned not in (None, "", [], {}):
            attributes[key] = cleaned

    return {
        "id": stable_id(raw, url),
        "url": url,
        "title": title,
        "price": price,
        "currency": currency,
        "description": clean_text(raw.get("description") or raw.get("summary") or raw.get("body")),
        "location": clean_text(raw.get("location") or raw.get("city") or raw.get("region")),
        "images": image_list(raw.get("images") or raw.get("image") or raw.get("thumbnail")),
        "attributes": attributes,
        "source": clean_text(meta.get("source")) or SOURCE,
        "scraped_at": clean_text(meta.get("scraped_at"))
        or datetime.now(UTC).isoformat(),
    }


def run(in_path: str, out_path: str, base_url: str = "") -> int:
    source = Path(in_path)
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    written = 0
    with source.open(encoding="utf-8") as reader, target.open("w", encoding="utf-8") as writer:
        for line in reader:
            line = line.strip()
            if not line:
                continue
            try:
                mapped = map_listing(json.loads(line), base_url)
            except Exception as exc:
                print(f"[warn] skipped bad record: {exc}")
                continue
            key = mapped["id"]
            if key in seen:
                continue
            seen.add(key)
            writer.write(json.dumps(mapped, ensure_ascii=False) + "\n")
            written += 1

    print(f"[done] mapped {written} records into {target}")
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Map raw items to the generic record schema")
    parser.add_argument("--in", dest="in_path", default="raw.jsonl")
    parser.add_argument("--out", default="mapped.jsonl")
    parser.add_argument("--base-url", default="")
    args = parser.parse_args()
    run(args.in_path, args.out, args.base_url)


if __name__ == "__main__":
    main()
