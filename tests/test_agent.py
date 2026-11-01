"""Agent loop: tool parsing, site guard, and one full verify-then-write pass."""

from __future__ import annotations

import asyncio

import pytest

from app import agent
from app.agent import FetchTool, build_bundle, parse_tool_call, registrable_domain

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
            session_id="job-1",
            proxies=[],
        )
    )

    assert set(files) == {"scraper.py", "mapper.py", "runner.sh", "README.md"}
    assert cost == pytest.approx(0.003)
    assert len(trace) == 1
    assert trace[0]["url"] == "https://example.com/list.rss"
    assert trace[0]["observation"]["entry_count"] == 20
