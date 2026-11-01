#!/usr/bin/env python3
"""Reference mapper — raw site items into the canonical listing schema.

Keep the helpers and the CLI. Replace only `map_listing` and the enum maps with
the ones that match the target site.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

SITE = "example.com"
COUNTRY = "xx"

CANONICAL_KEYS = [
    "make",
    "model",
    "trim",
    "price",
    "year",
    "mileage",
    "vin",
    "colors",
    "interior_color",
    "exterior_color",
    "link",
    "zip",
    "location",
    "certified",
    "images",
    "id",
    "body_style_id",
    "body_style_name",
    "descriptionLabel",
    "transmission",
    "drive_type",
    "model_name",
    "interior_color_full",
    "exterior_color_full",
    "fuel_type",
    "zip_num",
    "certified_description",
    "country",
    "record_saved",
]

FUEL_MAP = {"petrol": "Petrol", "diesel": "Diesel", "electric": "Electric", "hybrid": "Hybrid"}
TRANSMISSION_MAP = {"manual": "Manual", "automatic": "Automatic"}
BODY_MAP = {"suv": "SUV", "sedan": "Sedan", "hatchback": "Hatchback", "wagon": "Wagon"}


def to_int_or_none(value) -> int | None:
    """Coerce postal codes and numbers to a plain int, never a float."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value == value and abs(value) != float("inf") else None
    text = str(value).strip()
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        return int(float(text))
    digits = re.sub(r"[^0-9]", "", text)
    return int(digits) if digits else None


def parse_price(value) -> int | None:
    if value is None:
        return None
    digits = re.sub(r"[^0-9]", "", str(value))
    return int(digits) if digits else None


def parse_mileage(value) -> int | None:
    if value is None:
        return None
    digits = re.sub(r"[^0-9]", "", str(value))
    return int(digits) if digits else None


def compose_model_name(*parts) -> str:
    """Join make/model/description parts, skipping blanks and duplicates."""
    result = ""
    for part in parts:
        text = " ".join(str(part).split()) if part not in (None, "") else ""
        if not text:
            continue
        lowered = text.lower()
        if not result:
            result = text
        elif lowered in result.lower():
            continue
        elif result.lower() in lowered:
            result = text
        else:
            result = f"{result} {text}"
    return result


def normalize(value) -> str:
    return " ".join(str(value or "").lower().split())


# ─────────────────────────── SITE LOGIC ───────────────────────────
def map_listing(raw: dict) -> dict:
    """Translate one raw item into the canonical schema."""
    images = raw.get("images") or []
    if isinstance(images, str):
        images = [images]

    make = raw.get("make") or raw.get("brand")
    model = raw.get("model")
    title = raw.get("title") or raw.get("name")

    return {
        "make": make,
        "model": model,
        "trim": raw.get("trim") or raw.get("version"),
        "price": parse_price(raw.get("price")),
        "year": to_int_or_none(raw.get("year")),
        "mileage": parse_mileage(raw.get("mileage")),
        "vin": raw.get("vin"),
        "colors": raw.get("color"),
        "interior_color": raw.get("interior_color"),
        "exterior_color": raw.get("color"),
        "link": raw.get("url") or raw.get("link"),
        "zip": raw.get("zip") or raw.get("postcode"),
        "location": raw.get("location") or raw.get("city"),
        "certified": bool(raw.get("certified", False)),
        "images": [image for image in images if image][:20],
        "id": str(raw.get("id") or raw.get("url") or raw.get("link") or hash(json.dumps(raw, sort_keys=True))),
        "body_style_id": raw.get("body_type"),
        "body_style_name": BODY_MAP.get(normalize(raw.get("body_type")), raw.get("body_type")),
        "descriptionLabel": title,
        "transmission": TRANSMISSION_MAP.get(
            normalize(raw.get("transmission")), raw.get("transmission")
        ),
        "drive_type": raw.get("drive_type"),
        "model_name": compose_model_name(make, model, title),
        "interior_color_full": raw.get("interior_color"),
        "exterior_color_full": raw.get("color"),
        "fuel_type": FUEL_MAP.get(normalize(raw.get("fuel_type")), raw.get("fuel_type")),
        "zip_num": to_int_or_none(raw.get("zip") or raw.get("postcode")),
        "certified_description": raw.get("certified_description"),
        "country": COUNTRY,
        "record_saved": date.today().isoformat(),
    }


def run(in_path: str, out_path: str) -> int:
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
                mapped = map_listing(json.loads(line))
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
    parser = argparse.ArgumentParser(description="Map raw items to the canonical schema")
    parser.add_argument("--in", dest="in_path", default="raw.jsonl")
    parser.add_argument("--out", default="mapped.jsonl")
    args = parser.parse_args()
    run(args.in_path, args.out)


if __name__ == "__main__":
    main()
