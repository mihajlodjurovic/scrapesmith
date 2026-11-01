"""Discovery: polite probe of a page before any code is generated.

Everything here is deterministic and reported as JSON. The model never sees raw
HTML beyond a trimmed skeleton, so the prompt stays small and reproducible.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from .config import (
    ALLOW_PRIVATE_NETWORKS,
    FETCH_TIMEOUT_SECONDS,
    MAX_HTML_BYTES,
    SKELETON_MAX_CHARS,
    USER_AGENT,
)
from .proxies import mask_proxy

INTERESTING_HEADERS = (
    "content-type",
    "content-language",
    "server",
    "x-powered-by",
    "x-request-id",
    "x-api-version",
    "x-frame-options",
    "x-nextjs-cache",
    "cf-cache-status",
    "vary",
    "cache-control",
)

URL_IN_SCRIPT = re.compile(r"""["']((?:https?:)?//[^"'<>\s]{10,400}|/[^"'<>\s]{4,400})["']""")
API_HINT = re.compile(r"(/api/|/api\?|\.json(\?|$)|graphql|/v[12]/|/search|/rest/|hades\.)", re.I)
BLOCK_MARKERS = (
    "captcha",
    "just a moment",
    "attention required",
    "access denied",
    "cf-challenge",
    "enable javascript",
)


class DiscoveryError(RuntimeError):
    pass


def _is_public_host(host: str) -> bool:
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except socket.gaierror:
        return False
    if not addresses:
        return False
    for raw in addresses:
        ip = ipaddress.ip_address(raw)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


async def _check_target(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise DiscoveryError("Only http(s) URLs are supported.")
    if not parsed.hostname:
        raise DiscoveryError("That URL has no hostname.")
    if ALLOW_PRIVATE_NETWORKS:
        return
    is_public = await asyncio.to_thread(_is_public_host, parsed.hostname)
    if not is_public:
        raise DiscoveryError(
            "Refusing to fetch a private, loopback or unresolvable host "
            "(SSRF guard). Set SCRAPESMITH_ALLOW_PRIVATE=1 for local testing."
        )


async def _fetch_once(
    url: str, proxy: str | None
) -> tuple[httpx.Response, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    kwargs: dict[str, Any] = {
        "headers": headers,
        "timeout": FETCH_TIMEOUT_SECONDS,
        "follow_redirects": True,
        "max_redirects": 5,
    }
    if proxy:
        kwargs["proxy"] = proxy
    async with httpx.AsyncClient(**kwargs) as client:
        response = await client.get(url)
    return response, ("direct" if not proxy else mask_proxy(proxy))


async def _fetch(url: str, proxies: list[str]) -> tuple[httpx.Response, str]:
    attempts = list(proxies[:3]) + [None]
    last_error: Exception | None = None
    for proxy in attempts:
        try:
            response, used = await _fetch_once(url, proxy)
            if response.status_code < 500:
                return response, used
            last_error = DiscoveryError(f"proxy returned {response.status_code}")
        except Exception as exc:  # network, proxy auth, DNS
            last_error = exc
    raise DiscoveryError(f"Could not fetch the page. Last error: {last_error}")


async def _robots(url: str, proxies: list[str]) -> dict[str, Any]:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        response, _ = await _fetch(robots_url, proxies)
    except Exception:
        return {"found": False, "allowed": True, "crawl_delay": None, "note": "unreachable"}

    if response.status_code != 200:
        return {"found": False, "allowed": True, "crawl_delay": None}

    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    agent = USER_AGENT.split("/")[0]
    try:
        delay = parser.crawl_delay(agent)
    except Exception:
        delay = None
    return {
        "found": True,
        "allowed": parser.can_fetch(agent, url),
        "crawl_delay": delay,
        "sitemaps": parser.site_maps() or [],
    }


def _text(el) -> str:
    return " ".join(el.get_text(" ", strip=True).split())


def _json_ld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    found = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text()
        try:
            data = json.loads(raw)
        except Exception:
            continue
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict):
                found.append(
                    {
                        "type": item.get("@type"),
                        "keys": sorted(item.keys())[:25],
                    }
                )
    return found[:10]


def _embedded_state(html: str, soup: BeautifulSoup) -> list[dict[str, Any]]:
    checks = {
        "__NEXT_DATA__": soup.find("script", id="__NEXT_DATA__") is not None,
        "__NUXT__": "__NUXT__" in html,
        "__INITIAL_STATE__": "__INITIAL_STATE__" in html,
        "RSC flight (self.__next_f)": "self.__next_f.push" in html,
        "Apollo state": "__APOLLO_STATE__" in html,
        "Redux state": "window.__PRELOADED_STATE__" in html or "window.__INITIAL_STATE__" in html,
    }
    return [{"kind": kind, "present": present} for kind, present in checks.items() if present]


def _api_candidates(html: str, base_url: str) -> list[dict[str, Any]]:
    origin = urlparse(base_url).netloc
    seen: set[str] = set()
    results = []
    for match in URL_IN_SCRIPT.finditer(html):
        candidate = match.group(1)
        if not API_HINT.search(candidate):
            continue
        absolute = urljoin(base_url, candidate)
        if absolute in seen or not absolute.startswith(("http://", "https://")):
            continue
        seen.add(absolute)
        results.append(
            {
                "url": absolute[:400],
                "same_origin": urlparse(absolute).netloc == origin,
                "hint": "graphql" if "graphql" in absolute else "json-api",
            }
        )
        if len(results) >= 25:
            break
    return results


def _repeat_blocks(soup: BeautifulSoup) -> list[dict[str, Any]]:
    skip = {"script", "style", "link", "meta", "noscript", "template", "br", "hr"}
    blocks: list[dict[str, Any]] = []
    for parent in soup.find_all(True)[:2500]:
        groups: dict[tuple[str, tuple[str, ...]], list] = {}
        for child in parent.find_all(recursive=False):
            if child.name in skip:
                continue
            key = (child.name, tuple(child.get("class") or []))
            groups.setdefault(key, []).append(child)
        for (tag, classes), items in groups.items():
            if len(items) < 5:
                continue
            texts = [_text(item) for item in items]
            if sum(len(text) for text in texts) < 15 * len(items):
                continue
            link_ratio = sum(1 for item in items if item.find("a")) / len(items)
            blocks.append(
                {
                    "selector": tag + "".join(f".{name}" for name in classes),
                    "parent_selector": parent.name
                    + "".join(f".{name}" for name in (parent.get("class") or [])),
                    "count": len(items),
                    "link_ratio": round(link_ratio, 2),
                    "sample": [text[:120] for text in texts[:3] if text],
                }
            )
    blocks.sort(key=lambda block: block["count"] * (1 + block["link_ratio"]), reverse=True)
    unique: dict[str, dict[str, Any]] = {}
    for block in blocks:
        unique.setdefault(block["selector"], block)
        if len(unique) >= 8:
            break
    return list(unique.values())


def _skeleton(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "link", "meta"]):
        tag.decompose()
    keep = {"class", "id", "href", "src", "type", "name", "placeholder", "data-testid"}
    for tag in soup.find_all(True):
        tag.attrs = {key: value for key, value in tag.attrs.items() if key in keep}
    text = soup.prettify()
    return text[:SKELETON_MAX_CHARS]


def _headers(response: httpx.Response) -> dict[str, str]:
    captured = {
        key: value
        for key, value in response.headers.items()
        if key.lower() in INTERESTING_HEADERS
    }
    for key in response.headers.keys():
        if key.lower().startswith("x-") and key.lower() not in captured:
            captured[key.lower()] = "***"
    cookies = response.headers.get("set-cookie", "")
    if cookies:
        captured["set-cookie-names"] = ", ".join(
            part.split("=")[0].strip() for part in cookies.split(";") if "=" in part
        )[:200]
    return captured


async def probe(url: str, proxies: list[str]) -> dict[str, Any]:
    """Fetch and describe a page. Returns a JSON-serialisable report."""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"
    await _check_target(url)

    response, used = await _fetch(url, proxies)
    body = response.content[:MAX_HTML_BYTES]
    html = body.decode(response.encoding or "utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")

    lowered = html[:200_000].lower()
    blocks = [marker for marker in BLOCK_MARKERS if marker in lowered]
    json_ld = _json_ld(soup)
    title = _text(soup.title) if soup.title else ""
    description_tag = soup.find("meta", attrs={"name": "description"})
    canonical_tag = soup.find("link", attrs={"rel": "canonical"})

    report: dict[str, Any] = {
        "url": url,
        "final_url": str(response.url),
        "status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
        "fetched_bytes": len(body),
        "proxy_used": used,
        "headers": _headers(response),
        "robots": await _robots(url, proxies),
        "title": title[:200],
        "description": (description_tag.get("content", "") if description_tag else "")[:300],
        "canonical": urljoin(url, canonical_tag["href"]) if canonical_tag else None,
        "lang": (soup.html.get("lang") if soup.html else None),
        "counts": {
            "links": len(soup.find_all("a")),
            "scripts": len(soup.find_all("script")),
            "tables": len(soup.find_all("table")),
            "forms": len(soup.find_all("form")),
            "images": len(soup.find_all("img")),
        },
        "json_ld": json_ld,
        "embedded_state": _embedded_state(html, soup),
        "api_candidates": _api_candidates(html, str(response.url)),
        "repeat_blocks": _repeat_blocks(soup),
        "block_markers": blocks,
        "skeleton": _skeleton(soup),
    }
    if response.status_code >= 400:
        report["warning"] = f"Page answered {response.status_code}."
    if blocks:
        report["warning"] = "Page looks like an anti-bot challenge; generated code may not work."
    return report


def compact_probe(report: dict[str, Any], max_skeleton: int = 24000) -> dict[str, Any]:
    """Smaller probe for the prompt: no headers we do not need, shorter skeleton."""
    slim = {key: value for key, value in report.items() if key != "skeleton"}
    skeleton = report.get("skeleton", "")
    slim["skeleton"] = skeleton[:max_skeleton]
    if len(skeleton) > max_skeleton:
        slim["skeleton_truncated"] = True
    return slim
