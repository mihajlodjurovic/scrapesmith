"""find_unblocked: test every proxy (concurrently) until one is not challenged."""

from __future__ import annotations

import asyncio

import httpx

from app import discovery
from app.proxies import ProxyPool

CHALLENGE = "<html><title>Just a moment...</title></html>"


def _fake_fetch(answers: dict[str, tuple[int, str]], calls: list[str] | None = None):
    async def fetch_once(url, proxy, timeout=10):
        if calls is not None:
            calls.append(proxy)
        await asyncio.sleep(0)
        if proxy not in answers:
            raise httpx.ConnectError("dead proxy")
        status, body = answers[proxy]
        return httpx.Response(status, text=body, request=httpx.Request("GET", url)), proxy

    return fetch_once


def test_every_proxy_is_tested_when_none_gets_through(monkeypatch) -> None:
    proxies = [f"http://p{n}:80" for n in range(25)]
    answers = {proxy: (403, CHALLENGE) for proxy in proxies[:20]}  # the last 5 are dead
    calls: list[str] = []
    monkeypatch.setattr(discovery, "_fetch_once", _fake_fetch(answers, calls))

    result = asyncio.run(discovery.find_unblocked("https://x.test/", ProxyPool(proxies)))

    assert result["passed_via"] is None
    assert sorted(calls) == sorted(proxies)
    assert result["total"] == 25
    summary = discovery.summarize_check(result)
    assert summary == "Tested 25/25 proxies: 20 challenged (403), 5 dead (ConnectError)"


def test_stops_scheduling_new_checks_once_a_proxy_gets_through(monkeypatch) -> None:
    proxies = [f"http://p{n}:80" for n in range(40)]
    answers = {proxy: (403, CHALLENGE) for proxy in proxies}
    answers["http://p1:80"] = (200, "<html>cars</html>")
    calls: list[str] = []
    monkeypatch.setattr(discovery, "_fetch_once", _fake_fetch(answers, calls))

    result = asyncio.run(
        discovery.find_unblocked("https://x.test/", ProxyPool(proxies), concurrency=4)
    )

    assert result["passed_via"] == "http://p1:80"
    assert len(calls) < len(proxies)


def test_limit_caps_the_number_of_proxies(monkeypatch) -> None:
    proxies = [f"http://p{n}:80" for n in range(10)]
    calls: list[str] = []
    monkeypatch.setattr(discovery, "_fetch_once", _fake_fetch({}, calls))
    result = asyncio.run(discovery.find_unblocked("https://x.test/", ProxyPool(proxies), limit=3))
    assert len(calls) == 3
    assert result["total"] == 3


def test_challenge_with_200_still_counts_as_blocked(monkeypatch) -> None:
    pool = ProxyPool(["http://a:1"])
    monkeypatch.setattr(discovery, "_fetch_once", _fake_fetch({"http://a:1": (200, CHALLENGE)}))
    result = asyncio.run(discovery.find_unblocked("https://x.test/", pool))
    assert result["passed_via"] is None
    assert result["tested"][0]["markers"] == ["just a moment"]
