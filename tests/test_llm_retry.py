"""A thinking model that spends the whole budget reasoning gets one bigger retry."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app import llm


def _install(monkeypatch, replies: list[dict]) -> list[dict]:
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=replies[len(sent) - 1])

    original = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs)
    )
    return sent


def _reply(content: str, finish: str, cost: float) -> dict:
    return {
        "choices": [
            {"message": {"content": content, "reasoning_content": "x" * 500},
             "finish_reason": finish}
        ],
        "usage": {"cost": cost},
    }


def _chat(max_tokens: int = 16000):
    return asyncio.run(
        llm.chat(base_url="https://llm.test/v1", api_key="k", model="m",
                 messages=[{"role": "user", "content": "hi"}], max_tokens=max_tokens)
    )


def test_empty_reply_cut_by_length_is_retried_with_a_bigger_budget(monkeypatch) -> None:
    sent = _install(monkeypatch, [_reply("", "length", 0.01), _reply("### FILE: x", "stop", 0.02)])
    text, usage = _chat()
    assert text == "### FILE: x"
    assert [payload["max_tokens"] for payload in sent] == [16000, llm.LLM_RETRY_MAX_TOKENS]
    assert llm.usage_cost(usage) == pytest.approx(0.03)


def test_still_empty_after_the_retry_raises_a_readable_error(monkeypatch) -> None:
    _install(monkeypatch, [_reply("", "length", 0.0), _reply("", "length", 0.0)])
    with pytest.raises(llm.LLMError, match="finish_reason=length"):
        _chat()
