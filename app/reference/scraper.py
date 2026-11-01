#!/usr/bin/env python3
"""Reference scraper — the shape every generated scraper must keep.

Site-specific parts are clearly marked. Replace only those, keep the plumbing:
proxy pool with cooldown/blacklist, retries with backoff, 0.2s delay, API-first
fetching with HTML fallback, and JSONL output.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://example.com/search"
SITE = "example.com"

# ADAPT: the selector for the repeated item block on the target site.
CARD_SELECTOR = "article, li.product, div.card"

DELAY = float(os.getenv("SCRAPESMITH_DELAY", "0.2"))
TIMEOUT = float(os.getenv("SCRAPESMITH_TIMEOUT", "20"))
RETRIES = int(os.getenv("SCRAPESMITH_RETRIES", "3"))
MAX_PAGES = int(os.getenv("SCRAPESMITH_MAX_PAGES", "5"))
PROXY_COOLDOWN_SECONDS = int(os.getenv("SCRAPESMITH_PROXY_COOLDOWN", "90"))
PROXY_BLACKLIST_AFTER = int(os.getenv("SCRAPESMITH_PROXY_BLACKLIST_AFTER", "5"))

HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
    ),
}


# ─────────────────────────── Proxy pool ───────────────────────────
def load_proxies(path: str | Path) -> list[str]:
    """Accept http://user:pass@host:port, host:port:user:pass and host:port."""
    file = Path(path)
    if not file.exists():
        return []
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = file.read_text(encoding="utf-8").splitlines()

    proxies: list[str] = []
    for item in data:
        if isinstance(item, dict):
            server = item.get("server") or item.get("url")
            if not server and item.get("host"):
                server = f"http://{item['host']}:{item.get('port', '')}".rstrip(":")
            if server:
                user, password = item.get("username"), item.get("password")
                if user and password and "@" not in server:
                    server = server.replace("://", f"://{user}:{password}@", 1)
                proxies.append(server)
            continue
        line = str(item).strip()
        if not line or line.startswith("#"):
            continue
        if "://" in line:
            proxies.append(line)
            continue
        parts = line.split(":")
        if len(parts) == 4:
            host, port, user, password = parts
            proxies.append(f"http://{user}:{password}@{host}:{port}")
        elif len(parts) == 2:
            proxies.append(f"http://{parts[0]}:{parts[1]}")
    return proxies


class ProxyPool:
    def __init__(self, proxies: list[str]) -> None:
        self._proxies = list(proxies)
        self._failures: dict[str, int] = {proxy: 0 for proxy in self._proxies}
        self._consecutive: dict[str, int] = {proxy: 0 for proxy in self._proxies}
        self._cooldown: dict[str, float] = {}
        self._blacklist: dict[str, float] = {}
        self._last_used: dict[str, float] = {}
        print(f"[proxy] pool size: {len(self._proxies)}")

    def pick(self) -> str | None:
        now = time.time()
        usable = [
            proxy
            for proxy in self._proxies
            if self._cooldown.get(proxy, 0) <= now and self._blacklist.get(proxy, 0) <= now
        ]
        if not usable:
            return None
        usable.sort(key=lambda proxy: (self._failures.get(proxy, 0), self._last_used.get(proxy, 0)))
        return usable[0]

    def report(self, proxy: str | None, ok: bool, status: int | None = None) -> None:
        if not proxy:
            return
        now = time.time()
        self._last_used[proxy] = now
        if ok:
            self._failures[proxy] = max(0, self._failures.get(proxy, 0) - 1)
            self._consecutive[proxy] = 0
            return

        self._failures[proxy] = self._failures.get(proxy, 0) + 1
        self._consecutive[proxy] = self._consecutive.get(proxy, 0) + 1
        if status in (401, 403, 407, 429, 503):
            self._cooldown[proxy] = now + PROXY_COOLDOWN_SECONDS
        if self._consecutive[proxy] >= PROXY_BLACKLIST_AFTER:
            self._blacklist[proxy] = now + PROXY_COOLDOWN_SECONDS * 10
            print(f"[proxy] blacklisted {proxy.split('@')[-1]}")


def proxy_dict(proxy: str | None) -> dict[str, str] | None:
    return {"http": proxy, "https": proxy} if proxy else None


# ─────────────────────────── Fetching ───────────────────────────
def retry_delay(response: requests.Response | None, attempt: int) -> float:
    """Honour Retry-After, otherwise exponential backoff with jitter."""
    if response is not None:
        header = response.headers.get("retry-after", "")
        if header:
            try:
                return min(float(header), 30.0)
            except ValueError:
                pass
    return min(2 ** (attempt - 1), 20.0) + random.uniform(0, 0.3)


def request(session: requests.Session, pool: ProxyPool, url: str) -> requests.Response | None:
    for attempt in range(1, RETRIES + 1):
        proxy = pool.pick()
        try:
            response = session.get(
                url,
                headers=HEADERS,
                proxies=proxy_dict(proxy),
                timeout=TIMEOUT,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            pool.report(proxy, False)
            print(f"[warn] attempt {attempt}/{RETRIES} {url}: {exc}")
            time.sleep(retry_delay(None, attempt))
            continue

        if response.status_code in (401, 403, 407, 429, 503):
            pool.report(proxy, False, response.status_code)
            print(f"[warn] attempt {attempt}/{RETRIES} status {response.status_code}")
            time.sleep(retry_delay(response, attempt))
            continue
        if response.status_code >= 500:
            pool.report(proxy, False, response.status_code)
            time.sleep(retry_delay(response, attempt))
            continue

        pool.report(proxy, True)
        return response
    return None


# ─────────────────────────── SITE LOGIC ───────────────────────────
def page_url(page: int) -> str:
    return f"{BASE_URL}?page={page}"


def as_items(value, depth: int = 0) -> list[dict]:
    """Normalise any API shape to a list of dicts, or an empty list.

    Handles {"items": [...]}, {"data": {"items": [...]}}, {"results": [...]},
    and rejects scalar lists instead of crashing later in run().
    """
    if depth > 3:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("items", "data", "results", "listings", "products", "entries"):
            if key in value:
                nested = as_items(value[key], depth + 1)
                if nested:
                    return nested
    return []


def parse_items(payload: dict) -> list[dict]:
    """Map the site payload to a list of raw item dicts."""
    if isinstance(payload, dict):
        return as_items(payload.get("items"))
    return as_items(payload)


def extract_json_ld_items(html: str, base_url: str) -> list[dict]:
    """Read schema.org ItemList entries before falling back to card selectors."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or tag.get_text())
        except (TypeError, json.JSONDecodeError):
            continue
        blocks = data if isinstance(data, list) else [data]
        for block in blocks:
            if not isinstance(block, dict) or block.get("@type") != "ItemList":
                continue
            for element in block.get("itemListElement") or []:
                item = element.get("item") if isinstance(element, dict) else None
                if not isinstance(item, dict):
                    continue
                items.append(
                    {
                        "title": item.get("name"),
                        "url": urljoin(base_url, item.get("url") or ""),
                        "description": item.get("description"),
                    }
                )
    return items


def extract_cards(html: str, base_url: str) -> list[dict]:
    """Last-resort HTML fallback. ADAPT CARD_SELECTOR and the field mapping."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    for card in soup.select(CARD_SELECTOR)[:100]:
        link = card.find("a", href=True)
        if not link:
            continue
        title = " ".join(card.get_text(" ", strip=True).split())[:300]
        items.append(
            {
                "title": title,
                "url": urljoin(base_url, link["href"]),
            }
        )
    return items


def extract_payload(response: requests.Response) -> dict:
    """Prefer JSON; fall back to embedded state, JSON-LD, then HTML cards."""
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        try:
            data = response.json()
        except ValueError:
            data = {}
        return {"items": as_items(data)}

    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', response.text, re.S)
    if match:
        data = json.loads(match.group(1))
        page_props = data.get("props", {}).get("pageProps", {})
        return {"items": as_items(page_props.get("items"))}

    base_url = str(response.url)
    json_ld_items = extract_json_ld_items(response.text, base_url)
    if json_ld_items:
        return {"items": json_ld_items}
    return {"items": extract_cards(response.text, base_url)}


def fetch_page(session: requests.Session, pool: ProxyPool, page: int) -> list[dict]:
    response = request(session, pool, page_url(page))
    if response is None:
        return []
    try:
        items = parse_items(extract_payload(response))
    except Exception as exc:
        print(f"[warn] parse failed on page {page}: {exc}")
        return []
    return [item for item in items if isinstance(item, dict)]


def item_key(item: dict) -> str:
    if not isinstance(item, dict):
        return str(item)
    for field in ("id", "url", "link", "slug"):
        if item.get(field):
            return str(item[field])
    return json.dumps(item, sort_keys=True)[:200]


def save_mongo(uri: str, database: str, collection: str, items: list[dict]) -> None:
    if not items:
        print("[mongo] nothing to write")
        return
    from pymongo import MongoClient

    client = MongoClient(uri)
    client[database][collection].insert_many(items)
    print(f"[mongo] wrote {len(items)} raw items to {database}.{collection}")


def run(max_pages: int, out_path: str, proxies_file: str, mongo: str | None) -> int:
    session = requests.Session()
    pool = ProxyPool(load_proxies(proxies_file))
    seen: set[str] = set()
    written = 0
    written_items: list[dict] = []
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    with out.open("w", encoding="utf-8") as handle:
        for page in range(1, max_pages + 1):
            items = fetch_page(session, pool, page)
            print(f"[page {page}] items={len(items)}")
            if not items:
                break
            fresh = []
            for item in items:
                key = item_key(item)
                if key in seen:
                    continue
                seen.add(key)
                fresh.append(
                    {
                        **item,
                        "_meta": {
                            "source": SITE,
                            "page": page,
                            "scraped_at": datetime.now(UTC).isoformat(),
                        },
                    }
                )
            if not fresh:
                break
            for item in fresh:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
            written += len(fresh)
            written_items.extend(fresh)
            time.sleep(DELAY * random.uniform(0.8, 1.2))

    print(f"[done] wrote {written} raw items to {out}")
    if mongo:
        save_mongo(mongo, "scraped", SITE.replace(".", "_"), written_items)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape example.com")
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES)
    parser.add_argument("--out", default="raw.jsonl")
    parser.add_argument("--proxies", default=os.getenv("PROXIES_FILE", "proxies.json"))
    parser.add_argument("--mongo", default=os.getenv("MONGO_URI"))
    args = parser.parse_args()
    run(args.max_pages, args.out, args.proxies, args.mongo)


if __name__ == "__main__":
    main()
