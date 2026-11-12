#!/usr/bin/env python3
"""Reference BROWSER scraper — for client-side rendered sites only.

Use this shape only when plain HTTP cannot reach the data (the page is a
JavaScript shell and its API cannot be replayed with requests). It keeps the same
contract as the HTTP reference: ProxyPool with cooldown/blacklist, retries with
backoff, DELAY, request / fetch_page / parse_items / run / main, the same CLI and
JSONL output. Each page is loaded in headless Chromium (sync Playwright).

Site-specific parts are marked ADAPT. Prefer the JSON responses the page fetches
(API_PATTERN) over parsing the rendered DOM.

Setup once:  pip install -r requirements.txt && python -m playwright install chromium
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
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

BASE_URL = "https://example.com/search"
SITE = "example.com"

# ADAPT: JSON responses whose URL matches this pattern are treated as item data.
API_PATTERN = re.compile(r"/api/")
# ADAPT: the rendered item selector; the scraper waits for it before reading the DOM.
WAIT_SELECTOR = "article, li.product, div.card"
CARD_SELECTOR = WAIT_SELECTOR

DELAY = float(os.getenv("SCRAPESMITH_DELAY", "0.2"))
TIMEOUT = float(os.getenv("SCRAPESMITH_TIMEOUT", "30"))
RETRIES = int(os.getenv("SCRAPESMITH_RETRIES", "3"))
MAX_PAGES = int(os.getenv("SCRAPESMITH_MAX_PAGES", "5"))
PROXY_COOLDOWN_SECONDS = int(os.getenv("SCRAPESMITH_PROXY_COOLDOWN", "90"))
PROXY_BLACKLIST_AFTER = int(os.getenv("SCRAPESMITH_PROXY_BLACKLIST_AFTER", "5"))
NETWORK_IDLE_MS = int(os.getenv("SCRAPESMITH_NETWORK_IDLE_MS", "8000"))
# ADAPT: True for infinite-scroll / "load more on scroll" lists. The scroll budget
# is per page load and independent of --max-pages.
INFINITE_SCROLL = os.getenv("SCRAPESMITH_INFINITE_SCROLL", "1") == "1"
MAX_SCROLLS = int(os.getenv("SCRAPESMITH_MAX_SCROLLS", "60"))
SCROLL_PAUSE = float(os.getenv("SCRAPESMITH_SCROLL_PAUSE", "0.8"))
SCROLL_STALLS = int(os.getenv("SCRAPESMITH_SCROLL_STALLS", "3"))
MAX_ITEMS = int(os.getenv("SCRAPESMITH_MAX_ITEMS", "1000"))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
BLOCKED_RESOURCES = {"image", "media", "font"}


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


def playwright_proxy(proxy: str | None) -> dict[str, str] | None:
    """http://user:pass@host:port -> the dict Playwright expects."""
    if not proxy:
        return None
    parsed = urlparse(proxy)
    server = f"{parsed.scheme or 'http'}://{parsed.hostname}"
    if parsed.port:
        server += f":{parsed.port}"
    result = {"server": server}
    if parsed.username:
        result["username"] = parsed.username
        result["password"] = parsed.password or ""
    return result


def retry_delay(attempt: int) -> float:
    return min(2 ** (attempt - 1), 20.0) + random.uniform(0, 0.3)


def _block_heavy(route) -> None:
    if route.request.resource_type in BLOCKED_RESOURCES:
        route.abort()
    else:
        route.continue_()


def scroll_until_stable(page, selector: str = CARD_SELECTOR) -> int:
    """Scroll to the bottom until the item count stops growing. Returns the count.

    Stops after SCROLL_STALLS scrolls without new items, at MAX_ITEMS, or after
    MAX_SCROLLS scrolls. Captured API responses keep arriving while it scrolls.
    """
    last, stalls = -1, 0
    for _ in range(MAX_SCROLLS):
        try:
            count = page.eval_on_selector_all(selector, "els => els.length")
        except PlaywrightError:
            count = 0
        if count >= MAX_ITEMS:
            return count
        if count == last:
            stalls += 1
            if stalls >= SCROLL_STALLS:
                return count
        else:
            stalls = 0
        last = count
        page.mouse.wheel(0, 20000)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(int(SCROLL_PAUSE * 1000))
    print(f"[warn] scroll budget of {MAX_SCROLLS} used up at {last} items")
    return max(last, 0)


# ─────────────────────────── Fetching ───────────────────────────
def request(browser, pool: ProxyPool, url: str) -> dict | None:
    """Render one URL. Returns {"url", "html", "json": [captured payloads]} or None."""
    for attempt in range(1, RETRIES + 1):
        proxy = pool.pick()
        context = browser.new_context(
            proxy=playwright_proxy(proxy),
            user_agent=USER_AGENT,
            locale="en-US",
            service_workers="block",
        )
        captured: list = []
        try:
            page = context.new_page()
            page.route("**/*", _block_heavy)
            page.on(
                "response",
                lambda response, sink=captured: sink.append(response)
                if response.request.resource_type in ("xhr", "fetch")
                and API_PATTERN.search(response.url)
                else None,
            )
            response = page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT * 1000)
            status = response.status if response else 0
            if status in (401, 403, 407, 429, 503) or status >= 500:
                pool.report(proxy, False, status)
                print(f"[warn] attempt {attempt}/{RETRIES} status {status}")
                time.sleep(retry_delay(attempt))
                continue
            try:
                page.wait_for_load_state("networkidle", timeout=NETWORK_IDLE_MS)
            except PlaywrightTimeout:
                pass
            if WAIT_SELECTOR:
                try:
                    page.wait_for_selector(WAIT_SELECTOR, timeout=5000)
                except PlaywrightTimeout:
                    print(f"[warn] selector {WAIT_SELECTOR!r} did not appear on {url}")
            if INFINITE_SCROLL:
                found = scroll_until_stable(page)
                print(f"[scroll] {found} items after scrolling")
            payloads = []
            for item in captured:
                if "json" not in item.headers.get("content-type", ""):
                    continue
                try:
                    payloads.append(item.json())
                except (PlaywrightError, ValueError):
                    continue
            html = page.content()
            pool.report(proxy, True)
            return {"url": page.url, "html": html, "json": payloads}
        except PlaywrightError as exc:
            pool.report(proxy, False)
            print(f"[warn] attempt {attempt}/{RETRIES} {url}: {str(exc).splitlines()[0]}")
            time.sleep(retry_delay(attempt))
        finally:
            context.close()
    return None


# ─────────────────────────── SITE LOGIC ───────────────────────────
def page_url(page: int) -> str:
    return f"{BASE_URL}?page={page}"


def as_items(value, depth: int = 0) -> list[dict]:
    """Normalise any API shape to a list of dicts, or an empty list."""
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
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or tag.get_text())
        except (TypeError, json.JSONDecodeError):
            continue
        for block in data if isinstance(data, list) else [data]:
            if not isinstance(block, dict) or block.get("@type") != "ItemList":
                continue
            for element in block.get("itemListElement") or []:
                item = element.get("item") if isinstance(element, dict) else None
                if isinstance(item, dict):
                    items.append(
                        {
                            "title": item.get("name"),
                            "url": urljoin(base_url, item.get("url") or ""),
                            "description": item.get("description"),
                        }
                    )
    return items


def extract_cards(html: str, base_url: str) -> list[dict]:
    """Rendered-DOM fallback. ADAPT CARD_SELECTOR and the field mapping."""
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    for card in soup.select(CARD_SELECTOR)[:100]:
        link = card.find("a", href=True)
        if not link:
            continue
        items.append(
            {
                "title": " ".join(card.get_text(" ", strip=True).split())[:300],
                "url": urljoin(base_url, link["href"]),
            }
        )
    return items


def extract_payload(rendered: dict) -> dict:
    """Captured JSON first, then JSON-LD, then rendered cards."""
    for data in rendered.get("json") or []:
        items = as_items(data)
        if items:
            return {"items": items}
    base_url = rendered.get("url") or BASE_URL
    html = rendered.get("html") or ""
    json_ld_items = extract_json_ld_items(html, base_url)
    if json_ld_items:
        return {"items": json_ld_items}
    return {"items": extract_cards(html, base_url)}


def fetch_page(browser, pool: ProxyPool, page: int) -> list[dict]:
    rendered = request(browser, pool, page_url(page))
    if rendered is None:
        return []
    try:
        items = parse_items(extract_payload(rendered))
    except Exception as exc:
        print(f"[warn] parse failed on page {page}: {exc}")
        return []
    return [item for item in items if isinstance(item, dict)]


def item_key(item: dict) -> str:
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
    pool = ProxyPool(load_proxies(proxies_file))
    seen: set[str] = set()
    written = 0
    written_items: list[dict] = []
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    launch: dict = {"headless": True}
    if pool.pick() is not None:
        launch["proxy"] = {"server": "http://per-context"}  # real proxies are set per context

    with sync_playwright() as playwright, out.open("w", encoding="utf-8") as handle:
        browser = playwright.chromium.launch(**launch)
        try:
            for page in range(1, max_pages + 1):
                items = fetch_page(browser, pool, page)
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
        finally:
            browser.close()

    print(f"[done] wrote {written} raw items to {out}")
    if mongo:
        save_mongo(mongo, "scraped", SITE.replace(".", "_"), written_items)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape example.com with a headless browser")
    parser.add_argument("--max-pages", type=int, default=MAX_PAGES)
    parser.add_argument("--out", default="raw.jsonl")
    parser.add_argument("--proxies", default=os.getenv("PROXIES_FILE", "proxies.json"))
    parser.add_argument("--mongo", default=os.getenv("MONGO_URI"))
    args = parser.parse_args()
    run(args.max_pages, args.out, args.proxies, args.mongo)


if __name__ == "__main__":
    main()
