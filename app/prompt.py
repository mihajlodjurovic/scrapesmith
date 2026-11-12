"""Prompt construction. The reference bundle and schema travel with every call."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
   and, in the mapper, into map_listing.
6. Do not assume the site's domain. It may be products, cars, books, jobs,
   articles, rentals or a directory. Infer the item boundaries and the semantic
   fields from the discovery report and the HTML skeleton.
7. Every mapped record must contain all CANONICAL_KEYS. Put domain-specific
   fields (author, mileage, bedrooms, rating, seller, ...) into `attributes`
   with snake_case keys, and include the requested fields when the page exposes
   them. Normalise prices to numbers and keep the currency separate.
8. Standard library plus requests, beautifulsoup4 and pymongo only. No
   Playwright, no Selenium, no headless browsers.
9. If the discovery report is ambiguous or the page is blocked, still produce
   the most defensible scraper and say what you assumed in NOTES. Do not invent
   API endpoints that neither the report nor your own verification fetched.
10. When the page has no repeated blocks, no API candidates and a short
   skeleton, treat it as a JavaScript shell. Verify at least two structured
   candidates (the feed/JSON siblings above, an API path, or a subdomain) and
   base the scraper on whatever actually returned 2xx — not on the shell.
11. If the report contains `rendered.network`, those are JSON responses that a
   real headless browser saw the page fetch (XHR/fetch). They are the best data
   source: replay the one with the most items using requests — same method, URL
   and query parameters, the same JSON body for POST, and only the headers listed
   in `request_headers`. Paginate by changing the page/offset/cursor parameter
   you can see in the URL or body. Never copy cookies, authorization headers or
   tokens; if the endpoint only works with them, say so in NOTES.
12. The data must come from the target site itself, or from an endpoint the page
   itself calls (rendered.network). Never use web archives (archive.org), search
   engines, Google cache, translation proxies or scraping APIs as a source or as
   a fallback — that is ban evasion and serves stale data. If every route is
   blocked (403/challenge), write the scraper against the real site anyway, let it
   report the block and write zero records, and explain it in NOTES. A failed
   build is better than a fake one.

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


BROWSER_RULES = """
Browser fallback build
- Plain HTTP already failed for this page (see "Why plain HTTP failed"). This
  build runs a real headless browser, so rule 8 is replaced: use sync Playwright
  exactly like the reference browser scraper (headless Chromium, per-context
  proxies, image/font blocking) plus beautifulsoup4. requirements.txt must list
  playwright and beautifulsoup4, and runner.sh must check for both.
- Keep the reference browser plumbing: load_proxies, ProxyPool, playwright_proxy,
  request(browser, pool, url), fetch_page(browser, pool, page), parse_items, run,
  main and the same CLI flags.
- Prefer captured JSON: set API_PATTERN to match the endpoint in
  `rendered.network`, and map its item list in parse_items/extract_payload. Fall
  back to the rendered DOM: set WAIT_SELECTOR/CARD_SELECTOR to the item selector
  in `rendered.repeat_blocks` and extract real fields, not one text blob.
- Infinite scroll / lazy lists: keep scroll_until_stable and INFINITE_SCROLL=True;
  it scrolls until the item count stops growing, with its own MAX_SCROLLS budget
  (do not tie it to --max-pages). For classic ?page=N pagination set
  INFINITE_SCROLL=False and paginate with page_url.
- No stealth plugins, no fingerprint spoofing, no CAPTCHA solving, no logins.
  If the rendered page is a challenge or login wall, stop and say so in NOTES.
"""


def system_prompt(browser: bool = False) -> str:
    return SYSTEM + BROWSER_RULES if browser else SYSTEM


def _rendered_section(rendered: dict[str, Any]) -> str:
    compact = {
        key: rendered.get(key)
        for key in (
            "final_url",
            "status",
            "title",
            "visible_text",
            "block_markers",
            "json_ld",
            "repeat_blocks",
            "network_total",
        )
    }
    compact["network"] = [
        {**entry, "preview": (entry.get("preview") or "")[:900]}
        for entry in (rendered.get("network") or [])[:8]
    ]
    skeleton = (rendered.get("skeleton") or "")[: SKELETON_MAX_CHARS // 2]
    return f"""## Rendered page (headless Chromium, after JavaScript ran)
`network` lists the JSON responses the page fetched, best first (`item_count`
is the size of the largest list of objects, `list_path` where it sits).
```json
{json.dumps(compact, indent=2, ensure_ascii=False)}
```

Rendered DOM skeleton:
```html
{skeleton}
```
"""


def user_prompt(
    probe: dict[str, Any],
    options: dict[str, Any],
    browser: bool = False,
    failure: list[str] | None = None,
) -> str:
    fields = (options.get("fields") or "").strip()
    extra = options.get("instructions") or ""
    skeleton = probe.get("skeleton", "")[:SKELETON_MAX_CHARS]
    if probe.get("rendered"):
        skeleton = skeleton[: SKELETON_MAX_CHARS // 3]  # the rendered DOM matters more
    slim = {key: value for key, value in probe.items() if key not in ("skeleton", "rendered")}
    slim["skeleton"] = skeleton
    candidates = candidate_urls(probe.get("final_url") or probe.get("url") or "")
    rendered = _rendered_section(probe["rendered"]) if probe.get("rendered") else ""
    why = ""
    if failure:
        why = "## Why plain HTTP failed\n" + "\n".join(
            f"- {error[:600]}" for error in failure[:6]
        )
    scraper_name = "scraper_browser.py" if browser else "scraper.py"
    scraper_title = "Reference browser scraper" if browser else "Reference scraper"

    return f"""## {scraper_title} (keep this plumbing)
```python
{_read(scraper_name)}
```

## Reference mapper (keep this plumbing)
```python
{_read("mapper.py")}
```

## Record schema (must be fully covered)
```json
{schema_text()}
```

## Build metadata
- requested fields: {fields or "(none — infer semantic fields from the page)"}
- extra instructions: {extra or "(none)"}

## Candidate URLs worth verifying
{chr(10).join(f"- {url}" for url in candidates)}

## Discovery report (plain HTTP)
```json
{json.dumps(slim, indent=2, ensure_ascii=False)}
```

{rendered}
{why}

Produce the four files now. The scraper must write one JSON object per line to
the path passed with --out, and the mapper must read that file with --in.
"""


def candidate_urls(url: str) -> list[str]:
    if not url:
        return []
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return []
    base = url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    origin = f"{parsed.scheme}://{parsed.netloc}"
    candidates = [
        f"{base}.json",
        f"{base}.rss",
        f"{base}/feed",
        f"{origin}/feed",
        f"{origin}/sitemap.xml",
    ]
    seen: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.append(candidate)
    return seen
