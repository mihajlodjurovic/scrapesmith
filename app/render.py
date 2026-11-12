"""Headless-browser discovery for client-side rendered pages.

The plain probe in `discovery.py` sees what the server sends. A React/Vue/Angular
shell sends almost nothing; the data arrives later through XHR/fetch calls. This
module loads the page in headless Chromium, records those JSON responses and the
rendered DOM, and reports them in the same shape as the plain probe.

The most useful output is `network`: the JSON endpoints the page itself called.
Most client-side sites can then be scraped with plain `requests` against that
endpoint, so the browser is only needed once, at build time.

Playwright is optional. `available()` says whether it is installed; nothing here
is imported at module load.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from . import discovery
from .config import RENDER_TIMEOUT_SECONDS
from .proxies import ProxyPool, mask_proxy

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

# Resource types that never carry item data; blocking them makes renders fast.
BLOCKED_RESOURCES = {"image", "media", "font"}
# Request types whose redirects are followed manually so every hop is SSRF-checked.
GUARDED_RESOURCES = {"document", "xhr", "fetch", "script", "other"}
CAPTURED_RESOURCES = {"xhr", "fetch"}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 5

NOISE_HOSTS = re.compile(
    r"(google-analytics|googletagmanager|doubleclick|googlesyndication|facebook\.|"
    r"hotjar|segment\.|sentry|newrelic|nr-data|datadoghq|clarity\.ms|optimizely|"
    r"amplitude|mixpanel|intercom|cookielaw|onetrust|cookiebot|consent|termly|usercentrics|"
    r"didomi|quantcast|trustarc|iubenda)",
    re.I,
)
SAFE_REQUEST_HEADERS = {
    "accept",
    "accept-language",
    "content-type",
    "x-requested-with",
    "origin",
    "referer",
}
MAX_CAPTURED = 60
MAX_BODY_BYTES = 300_000
MAX_NETWORK_ENTRIES = 12
SHELL_ROOT = re.compile(r'id="(root|app|__next|__nuxt|svelte|main-app)"')


class RenderError(RuntimeError):
    pass


def available() -> bool:
    return importlib.util.find_spec("playwright") is not None


# ─────────────────────────── shell detection ───────────────────────────
def visible_text_length(skeleton: str) -> int:
    text = re.sub(r"<[^>]+>", " ", skeleton or "")
    return len(" ".join(text.split()))


def looks_like_shell(probe: dict[str, Any]) -> bool:
    """True when the server HTML is unlikely to contain the data itself."""
    if "enable javascript" in (probe.get("block_markers") or []):
        return True
    if probe.get("repeat_blocks") or probe.get("embedded_state"):
        return False
    skeleton = probe.get("skeleton") or ""
    visible = visible_text_length(skeleton)
    if visible < 2000:
        return True
    return bool(SHELL_ROOT.search(skeleton)) and visible < 6000


# ─────────────────────────── analysis (pure) ───────────────────────────
def find_item_list(data: Any, max_depth: int = 5) -> tuple[str, list[dict]]:
    """Largest list of objects inside a JSON value, with a dotted path to it."""
    best_path, best = "", []
    queue: list[tuple[str, Any, int]] = [("", data, 0)]
    while queue:
        path, value, depth = queue.pop(0)
        if isinstance(value, list):
            objects = [item for item in value if isinstance(item, dict)]
            if len(objects) > len(best):
                best_path, best = path or "$", objects
            if depth < max_depth:
                for index, item in enumerate(value[:3]):
                    if isinstance(item, (dict, list)):
                        queue.append((f"{path}[{index}]", item, depth + 1))
        elif isinstance(value, dict) and depth < max_depth:
            for key, item in list(value.items())[:80]:
                if isinstance(item, (dict, list)):
                    queue.append((f"{path}.{key}" if path else key, item, depth + 1))
    return best_path, best


def _network_entry(item: dict[str, Any]) -> dict[str, Any] | None:
    url = str(item.get("url") or "")
    if not url.startswith(("http://", "https://")) or NOISE_HOSTS.search(url):
        return None
    body = item.get("body") or ""
    content_type = str(item.get("content_type") or "")
    stripped = body.lstrip()[:1]
    if "json" not in content_type and stripped not in ("{", "["):
        return None
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        return None

    path, items = find_item_list(data)
    headers = {str(k).lower(): str(v) for k, v in (item.get("request_headers") or {}).items()}
    entry: dict[str, Any] = {
        "url": url[:600],
        "method": str(item.get("method") or "GET").upper(),
        "status": item.get("status"),
        "content_type": content_type.split(";")[0],
        "bytes": len(body),
        "item_count": len(items),
        "list_path": path if items else None,
        "item_keys": sorted(items[0].keys())[:30] if items else [],
        "request_headers": {k: v[:200] for k, v in headers.items() if k in SAFE_REQUEST_HEADERS},
        # Names only: cookies, tokens and auth headers are never shown to the model.
        "other_header_names": sorted(k for k in headers if k not in SAFE_REQUEST_HEADERS)[:20],
        "preview": body[:1500],
    }
    post_data = item.get("post_data")
    if post_data:
        entry["post_data"] = str(post_data)[:1500]
    return entry


def analyze(html: str, final_url: str, captured: list[dict[str, Any]]) -> dict[str, Any]:
    """Describe a rendered page. No browser needed, so this is unit-testable."""
    soup = BeautifulSoup(html or "", "html.parser")
    lowered = (html or "")[:200_000].lower()
    network = [entry for entry in map(_network_entry, captured) if entry]
    network.sort(key=lambda entry: (entry["item_count"], entry["bytes"]), reverse=True)
    title = discovery._text(soup.title) if soup.title else ""
    report = {
        "final_url": final_url,
        "title": title[:300],
        "dom_bytes": len(html or ""),
        "block_markers": [
            m for m in discovery.BLOCK_MARKERS if m in lowered and m != "enable javascript"
        ],
        "json_ld": discovery._json_ld(soup),
        "repeat_blocks": discovery._repeat_blocks(soup),
        "network": network[:MAX_NETWORK_ENTRIES],
        "network_total": len(network),
    }
    report["skeleton"] = discovery._skeleton(soup)
    report["visible_text"] = visible_text_length(report["skeleton"])
    return report


def network_hosts(probe: dict[str, Any]) -> set[str]:
    rendered = probe.get("rendered") or {}
    return {
        (urlparse(entry["url"]).hostname or "").lower()
        for entry in rendered.get("network") or []
        if entry.get("item_count")
    } - {""}


def has_api(probe: dict[str, Any]) -> bool:
    rendered = probe.get("rendered") or {}
    return any(entry.get("item_count", 0) >= 2 for entry in rendered.get("network") or [])


def describe(rendered: dict[str, Any]) -> list[str]:
    lines = [
        f"Rendered {rendered.get('dom_bytes', 0) / 1024:.0f} KB DOM in "
        f"{rendered.get('seconds')}s via {rendered.get('proxy_used')} · "
        f"{rendered.get('visible_text', 0)} chars of text"
    ]
    network = rendered.get("network") or []
    lines.append(f"Captured JSON responses: {rendered.get('network_total', len(network))}")
    for entry in network[:3]:
        if entry.get("item_count"):
            lines.append(
                f"  {entry['method']} {entry['url'][:120]} -> {entry['item_count']} items "
                + (
                    "at the top level"
                    if entry["list_path"] == "$"
                    else f"at {entry['list_path']}"
                )
            )
    blocks = rendered.get("repeat_blocks") or []
    if blocks:
        best = blocks[0]
        lines.append(f"Rendered repeated blocks: best {best['selector']} × {best['count']}")
    return lines


# ─────────────────────────── browser ───────────────────────────
def playwright_proxy(proxy: str | None) -> dict[str, str] | None:
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


async def _guard(route, request) -> None:  # pragma: no cover - needs a browser
    """Abort private hosts and follow redirects manually so each hop is checked."""
    url = request.url
    if not url.startswith(("http://", "https://")):
        await route.continue_()
        return
    if request.resource_type in BLOCKED_RESOURCES:
        await route.abort()
        return
    try:
        await discovery._check_target(url)
    except discovery.DiscoveryError:
        await route.abort("blockedbyclient")
        return
    if discovery.ALLOW_PRIVATE_NETWORKS or request.resource_type not in GUARDED_RESOURCES:
        await route.continue_()
        return
    # Chromium does not send redirect hops back through the route handler, so a
    # 3xx fulfilled here would be followed unchecked. Follow the chain ourselves,
    # check every hop, and hand the browser only the final response.
    current, method, post_data = url, request.method, request.post_data
    try:
        for _ in range(MAX_REDIRECTS + 1):
            response = await route.fetch(
                url=current, method=method, post_data=post_data, max_redirects=0
            )
            location = response.headers.get("location")
            if response.status not in REDIRECT_STATUSES or not location:
                await route.fulfill(response=response)
                return
            current = urljoin(current, location)
            await discovery._check_target(current)
            if response.status in (301, 302, 303) and method != "GET":
                method, post_data = "GET", None
    except discovery.DiscoveryError:
        await route.abort("blockedbyclient")
        return
    except Exception:  # noqa: BLE001 - network errors surface as a failed request
        await route.abort()
        return
    await route.abort()  # too many redirects


async def _render_once(browser, url: str, proxy: str | None) -> dict[str, Any]:  # pragma: no cover
    context = await browser.new_context(
        proxy=playwright_proxy(proxy),
        user_agent=BROWSER_USER_AGENT,
        locale="en-US",
        service_workers="block",
        viewport={"width": 1366, "height": 900},
    )
    responses: list[Any] = []
    try:
        await context.route("**/*", _guard)
        page = await context.new_page()
        page.on(
            "response",
            lambda response: responses.append(response)
            if response.request.resource_type in CAPTURED_RESOURCES
            and len(responses) < MAX_CAPTURED
            else None,
        )
        main = await page.goto(url, wait_until="domcontentloaded", timeout=25_000)
        status = main.status if main else 0
        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:  # noqa: BLE001 - long-polling pages never go idle
            pass
        for _ in range(2):  # trigger lazy loading / infinite scroll once or twice
            await page.mouse.wheel(0, 4000)
            await page.wait_for_timeout(800)
        html = await page.content()

        captured: list[dict[str, Any]] = []
        for response in responses:
            content_type = response.headers.get("content-type", "")
            try:
                body = (await response.body())[:MAX_BODY_BYTES].decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - redirects and aborted requests have no body
                continue
            request = response.request
            captured.append(
                {
                    "url": response.url,
                    "method": request.method,
                    "status": response.status,
                    "content_type": content_type,
                    "body": body,
                    "post_data": request.post_data,
                    "request_headers": await request.all_headers(),
                }
            )
        return {"status": status, "final_url": page.url, "html": html, "captured": captured}
    finally:
        await context.close()


async def render(url: str, pool: ProxyPool | None = None) -> dict[str, Any]:  # pragma: no cover
    """Load the page in headless Chromium and describe what it rendered and fetched."""
    if not available():
        raise RenderError(
            "Playwright is not installed: pip install -r requirements-browser.txt "
            "&& python -m playwright install chromium"
        )
    await discovery._check_target(url)
    from playwright.async_api import async_playwright

    pool = pool or ProxyPool([])
    started = time.perf_counter()

    async def _go() -> dict[str, Any]:
        failures: list[str] = []
        async with async_playwright() as playwright:
            launch: dict[str, Any] = {"headless": True}
            if len(pool):
                launch["proxy"] = {"server": "http://per-context"}
            browser = await playwright.chromium.launch(**launch)
            try:
                for proxy in pool.attempts(2):
                    used = "direct" if proxy is None else mask_proxy(proxy)
                    try:
                        result = await _render_once(browser, url, proxy)
                    except Exception as exc:  # noqa: BLE001 - try the next proxy
                        pool.report(proxy, False)
                        failures.append(f"{used}: {type(exc).__name__}: {str(exc)[:160]}")
                        continue
                    if result["status"] in (401, 403, 407, 429) or result["status"] >= 500:
                        pool.report(proxy, False, result["status"])
                        failures.append(f"{used}: HTTP {result['status']}")
                        continue
                    pool.report(proxy, True, result["status"])
                    result["proxy_used"] = used
                    return result
            finally:
                await browser.close()
        raise RenderError(
            f"Headless render failed on all {len(failures)} routes: " + "; ".join(failures)
        )

    try:
        raw = await asyncio.wait_for(_go(), timeout=RENDER_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise RenderError(f"Headless render timed out after {RENDER_TIMEOUT_SECONDS:.0f}s") from exc

    report = analyze(raw["html"], raw["final_url"], raw["captured"])
    report["status"] = raw["status"]
    report["proxy_used"] = raw["proxy_used"]
    report["seconds"] = round(time.perf_counter() - started, 1)
    return report
