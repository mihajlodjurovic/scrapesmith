"""Prompt construction. The reference bundle and schema travel with every call."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import SKELETON_MAX_CHARS

REFERENCE_DIR = Path(__file__).resolve().parent / "reference"


def _read(name: str) -> str:
    return (REFERENCE_DIR / name).read_text(encoding="utf-8")


def schema_text() -> str:
    return _read("schema.json")


SYSTEM = """You are a senior Python scraping engineer working inside ScrapeSmith.

Your job: given a discovery report about ONE public web page, produce a small,
production-shaped scraping bundle that follows the provided reference files
exactly. The bundle is validated, executed and repaired automatically, so the
code has to actually run — not just look plausible.

Hard rules
1. API first. If the discovery report shows json api candidates or embedded
   state (__NEXT_DATA__, RSC flight payload, __NUXT__, Apollo/Redux state),
   parse that before touching HTML. Only fall back to HTML parsing when no
   structured payload exists.
2. Proxies are mandatory when a proxy list is provided. Read proxies.json at
   runtime, rotate through the pool, cooldown on 401/403/407/429, blacklist
   after repeated failures. Never hardcode proxy credentials.
3. Be polite: DELAY defaults to 0.2 seconds with jitter, respect Retry-After
   when present, cap pages, send the ScrapeSmith user agent or a plain browser
   UA. Never bypass logins, paywalls or CAPTCHAs.
4. No secrets in code. No Mongo URIs, API keys or passwords. Read them from
   environment variables or CLI flags.
5. Keep the plumbing from the reference scraper: load_proxies, ProxyPool,
   request, run, main, and the CLI flags --max-pages, --out, --proxies, --mongo.
   Site-specific code goes only into page_url / parse_items / extract_payload
   and, in the mapper, into map_listing and the enum maps.
6. The mapper must emit every key from schema.json (CANONICAL_KEYS must stay
   complete). Normalise money, mileage and postal codes to plain integers.
   Translate enum values into English.
7. Standard library plus requests, beautifulsoup4 and pymongo only. No
   Playwright, no Selenium, no headless browsers.
8. If the discovery report is ambiguous or the page is blocked, still produce
   the most defensible scraper and say what you assumed in NOTES. Do not invent
   API endpoints that are not in the report.

Output format — exactly these sections, each with a fenced code block:

### FILE: scraper.py
```python
...
```

### FILE: mapper.py
```python
...
```

### FILE: runner.sh
```bash
...
```

### FILE: README.md
```markdown
...
```

### NOTES
Three to six bullet points: what data source you chose and why, what you would
verify first, and what is likely to break when the site changes.
"""


def user_prompt(probe: dict[str, Any], options: dict[str, Any]) -> str:
    country = options.get("country") or "unknown"
    site_name = options.get("site_name") or ""
    extra = options.get("instructions") or ""
    skeleton = probe.get("skeleton", "")[:SKELETON_MAX_CHARS]
    slim = {key: value for key, value in probe.items() if key != "skeleton"}
    slim["skeleton"] = skeleton

    return f"""## Reference scraper (keep this plumbing)
```python
{_read("scraper.py")}
```

## Reference mapper (keep this plumbing)
```python
{_read("mapper.py")}
```

## Canonical schema (must be fully covered)
```json
{schema_text()}
```

## Site metadata
- country value for the mapper: {country!r}
- site name: {site_name or probe.get("final_url", "")}
- extra instructions: {extra or "(none)"}

## Discovery report
```json
{json.dumps(slim, indent=2, ensure_ascii=False)}
```

Produce the four files now. The scraper must write one JSON object per line to
the path passed with --out, and the mapper must read that file with --in.
"""
