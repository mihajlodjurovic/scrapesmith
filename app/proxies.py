"""Proxy parsing plus the health-aware rotation pool used by the whole job."""

from __future__ import annotations

import json
import time
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


# Characters left over when a list is pasted as "[a, b]" or '"a",' without valid JSON.
_LIST_PUNCTUATION = "[](),;\"' \t"


def _from_line(line: str) -> str | None:
    line = line.strip().strip(_LIST_PUNCTUATION)
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
            # Not JSON: one proxy per line, or several separated by commas.
            raw = [chunk for line in text.splitlines() for chunk in line.split(",")]

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


class ProxyPool:
    """Shared health-aware pool for discovery and the agent fetch tool.

    Mirrors the rules used by the generated scrapers: prefer proxies with the
    fewest failures and the oldest last use, cool down on auth/limit responses,
    blacklist after repeated consecutive failures, and fall back to direct only
    when nothing healthy is left.
    """

    def __init__(
        self,
        proxies: list[str] | None = None,
        *,
        cooldown_seconds: int = 90,
        blacklist_after: int = 5,
        blacklist_seconds: int = 900,
    ) -> None:
        self._proxies = list(dict.fromkeys(proxies or []))
        self.cooldown_seconds = cooldown_seconds
        self.blacklist_after = blacklist_after
        self.blacklist_seconds = blacklist_seconds
        self._failures: dict[str, int] = {proxy: 0 for proxy in self._proxies}
        self._consecutive: dict[str, int] = {proxy: 0 for proxy in self._proxies}
        self._cooldown_until: dict[str, float] = {}
        self._blacklist_until: dict[str, float] = {}
        self._last_used: dict[str, float] = {}
        self.requests = 0
        self.failures = 0

    def __len__(self) -> int:
        return len(self._proxies)

    def __bool__(self) -> bool:
        return bool(self._proxies)

    @property
    def proxies(self) -> list[str]:
        return list(self._proxies)

    def _available(self, now: float) -> list[str]:
        return [
            proxy
            for proxy in self._proxies
            if self._blacklist_until.get(proxy, 0) <= now
            and self._cooldown_until.get(proxy, 0) <= now
        ]

    def pick(self) -> str | None:
        """Least-failing, least-recently-used healthy proxy, or None for direct."""
        now = time.time()
        usable = self._available(now)
        if not usable:
            return None
        usable.sort(
            key=lambda proxy: (
                self._failures.get(proxy, 0),
                self._last_used.get(proxy, 0),
            )
        )
        return usable[0]

    def attempts(self, limit: int = 3) -> list[str | None]:
        """Ordered candidates for one logical request, ending with direct."""
        now = time.time()
        usable = self._available(now)
        usable.sort(
            key=lambda proxy: (
                self._failures.get(proxy, 0),
                self._last_used.get(proxy, 0),
            )
        )
        picked: list[str | None] = usable[: max(1, limit)]
        picked.append(None)
        return picked

    def report(self, proxy: str | None, ok: bool, status: int | None = None) -> None:
        if not proxy:
            return
        now = time.time()
        self._last_used[proxy] = now
        self.requests += 1

        if ok:
            self._failures[proxy] = max(0, self._failures.get(proxy, 0) - 1)
            self._consecutive[proxy] = 0
            return

        self.failures += 1
        self._failures[proxy] = self._failures.get(proxy, 0) + 1
        self._consecutive[proxy] = self._consecutive.get(proxy, 0) + 1

        if status in (401, 403, 407, 429, 503):
            self._cooldown_until[proxy] = now + self.cooldown_seconds
        if self._consecutive[proxy] >= self.blacklist_after:
            self._blacklist_until[proxy] = now + self.blacklist_seconds

    def stats(self) -> dict[str, int]:
        now = time.time()
        available = len(self._available(now))
        blacklisted = sum(1 for until in self._blacklist_until.values() if until > now)
        cooling = sum(
            1
            for proxy in self._proxies
            if self._cooldown_until.get(proxy, 0) > now
            and self._blacklist_until.get(proxy, 0) <= now
        )
        return {
            "total": len(self._proxies),
            "available": available,
            "cooling": cooling,
            "blacklisted": blacklisted,
            "requests": self.requests,
            "failures": self.failures,
        }
