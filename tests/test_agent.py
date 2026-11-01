"""Agent loop: tool parsing, site guard, and one full verify-then-write pass."""

from __future__ import annotations

import asyncio

import pytest

from app import agent
from app.agent import FetchTool, build_bundle, parse_tool_call, registrable_domain
from app.reference import scraper as reference

BUNDLE_TEXT = """### FILE: scraper.py
```python
print("scraper")
```

### FILE: mapper.py
```python
print("mapper")
```

### FILE: runner.sh
```bash
python3 scraper.py
python3 mapper.py
```

### FILE: README.md
```markdown
# Example bundle
```
"""


def test_parse_tool_call_variants() -> None:
    assert parse_tool_call("TOOL: fetch https://example.com/a.json") == "https://example.com/a.json"
    assert parse_tool_call("`TOOL: fetch https://example.com/a.rss`") == "https://example.com/a.rss"
    assert parse_tool_call("I will fetch it later.") is None
    assert parse_tool_call("TOOL: delete https://example.com") is None


def test_registrable_domain_and_site_guard() -> None:
    assert registrable_domain("api.reddit.com") == "reddit.com"
    assert registrable_domain("www.books.toscrape.com") == "toscrape.com"

    tool = FetchTool(target_url="https://www.reddit.com/r/AskSerbia/comments/abc/")
    assert tool.allowed("https://www.reddit.com/r/AskSerbia/comments/abc.rss")
    assert tool.allowed("https://old.reddit.com/r/AskSerbia/comments/abc/")
    assert not tool.allowed("https://evil.example.com/steal")
    assert not tool.allowed("file:///etc/passwd")


def test_registrable_domain_respects_public_suffixes() -> None:
    assert registrable_domain("shop.foo.co.uk") == "foo.co.uk"
    assert registrable_domain("a.b.com.br") == "b.com.br"
    assert registrable_domain("evil.co.rs") == "evil.co.rs"
    assert registrable_domain("evil.co.uk") != registrable_domain("target.co.uk")
    assert registrable_domain("alice.github.io") == "alice.github.io"
    assert registrable_domain("alice.github.io") != registrable_domain("bob.github.io")


def test_tool_call_accepts_json_as_well_as_text() -> None:
    assert (
        parse_tool_call('{"tool": "fetch", "url": "https://example.com/a.json"}')
        == "https://example.com/a.json"
    )
    fenced = '```json\n{"action": "fetch", "url": "https://example.com/a.rss"}\n```'
    assert parse_tool_call(fenced) == "https://example.com/a.rss"


def test_reference_retry_delay_honours_retry_after() -> None:
    class Response:
        headers = {"retry-after": "7"}

    assert reference.retry_delay(Response(), attempt=1) == 7.0
    assert 1.0 <= reference.retry_delay(None, attempt=3) <= 4.3


def test_reference_html_fallback_extracts_cards() -> None:
    html = (
        '<article><a href="/cars/1">Volkswagen Golf 2.0 TDI</a></article>'
        '<article><a href="/cars/2">Audi A3 35 TFSI</a></article>'
    )
    items = reference.extract_cards(html, "https://example.com/list")
    assert items[0]["url"] == "https://example.com/cars/1"
    assert "Volkswagen" in items[0]["title"]
    assert len(items) == 2


def test_reference_normalises_nested_api_shapes() -> None:
    assert reference.as_items({"data": {"items": [{"id": 1}]}}) == [{"id": 1}]
    assert reference.as_items({"results": [{"id": 2}]}) == [{"id": 2}]
    assert reference.as_items({"items": ["a", 1, {"id": 3}]}) == [{"id": 3}]
    assert reference.as_items(["a", "b"]) == []
    assert reference.as_items({"data": "not a list"}) == []


def test_agent_fetches_then_returns_bundle(monkeypatch) -> None:
    replies = [
        ("TOOL: fetch https://example.com/list.rss", {"cost": 0.001}),
        (BUNDLE_TEXT, {"cost": 0.002}),
    ]

    async def fake_chat(**kwargs):
        return replies.pop(0)

    async def fake_fetch(self, url):
        return {
            "url": url,
            "status": 200,
            "content_type": "application/atom+xml",
            "bytes": 1234,
            "entry_count": 20,
            "preview": "<feed><entry><title>Example</title></entry></feed>",
        }

    monkeypatch.setattr(agent.llm, "chat", fake_chat)
    monkeypatch.setattr(agent.FetchTool, "fetch", fake_fetch)

    files, cost, trace = asyncio.run(
        build_bundle(
            probe={"final_url": "https://example.com/list", "url": "https://example.com/list"},
            options={},
            base_url="https://example.com/v1",
            api_key="test",
            model="test-model",
            proxies=[],
        )
    )

    assert set(files) == {"scraper.py", "mapper.py", "runner.sh", "README.md"}
    assert cost == pytest.approx(0.003)
    assert len(trace) == 1
    assert trace[0]["url"] == "https://example.com/list.rss"
    assert trace[0]["observation"]["entry_count"] == 20
