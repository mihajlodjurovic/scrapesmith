"""Proxy list parsing — accepts every shape people actually paste."""

from __future__ import annotations

import json
from urllib.parse import quote


def _from_object(item: dict) -> str | None:
    server = item.get("server") or item.get("proxy") or item.get("url")
    if not server and item.get("host"):
        scheme = item.get("scheme", "http")
        port = item.get("port")
        server = f"{scheme}://{item['host']}:{port}" if port else f"{scheme}://{item['host']}"
    if not server:
        return None
    if "://" not in server:
        server = f"http://{server}"
    username = item.get("username") or item.get("user")
    password = item.get("password") or item.get("pass")
    if username and password and "@" not in server:
        scheme, _, rest = server.partition("://")
        return f"{scheme}://{quote(str(username))}:{quote(str(password))}@{rest}"
    return server


def _from_line(line: str) -> str | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "://" in line:
        return line
    parts = line.split(":")
    if len(parts) == 4:
        host, port, user, password = parts
        return f"http://{quote(user)}:{quote(password)}@{host}:{port}"
    if len(parts) == 2:
        host, port = parts
        return f"http://{host}:{port}"
    return None


def parse_proxies(raw: str | list | None) -> list[str]:
    """Normalise a proxy list from JSON, newline text, or a Python list."""
    if not raw:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return [url for line in text.splitlines() if (url := _from_line(line))]

    if isinstance(raw, dict):
        raw = raw.get("proxies") or raw.get("list") or []

    proxies: list[str] = []
    for item in raw:
        url = _from_object(item) if isinstance(item, dict) else _from_line(str(item))
        if url and url not in proxies:
            proxies.append(url)
    return proxies


def mask_proxy(url: str) -> str:
    """Hide credentials when logging."""
    if "@" not in url:
        return url
    scheme, _, rest = url.partition("://")
    _creds, _, host = rest.rpartition("@")
    return f"{scheme}://***@{host}"
