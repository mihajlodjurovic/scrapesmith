"""OpenAI-compatible chat client (OpenCode Console, OpenAI, OpenRouter, custom)."""

from __future__ import annotations

import uuid

import httpx

from .config import (
    DEFAULT_BASE_URL,
    LLM_MAX_TOKENS,
    LLM_REASONING_EFFORT,
    LLM_TIMEOUT_SECONDS,
)

# Go requires a stable session id on every request. Jobs pass their own id;
# anything else in this process shares this one.
_PROCESS_SESSION = f"scrapesmith-{uuid.uuid4().hex[:12]}"


class LLMError(RuntimeError):
    """A model call failed in a way the user can act on."""


def usage_cost(usage: dict) -> float:
    """USD cost reported by the provider, when present."""
    if not isinstance(usage, dict):
        return 0.0
    direct = usage.get("cost")
    nested_block = usage.get("opencode")
    nested = nested_block.get("list_cost") if isinstance(nested_block, dict) else None
    values = [
        float(value)
        for value in (direct, nested)
        if isinstance(value, (int, float))
    ]
    return max(values) if values else 0.0


def _explain(status: int, body: str) -> str:
    snippet = body.strip().replace("\n", " ")[:400]
    if status == 401:
        return "Model rejected the API key (401). Check the key in .env or the form."
    if status == 403:
        return (
            "Model returned 403. With OpenCode, free-tier models only work inside "
            "OpenCode itself — use a paid model or a Go subscription for external clients."
        )
    if status == 404:
        return f"Model endpoint not found (404). Check base URL and model id. {snippet}"
    if status == 429:
        return f"Rate limited (429). Wait a moment or switch model. {snippet}"
    return f"Model call failed ({status}). {snippet}"


async def chat(
    *,
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int = LLM_MAX_TOKENS,
    temperature: float = 0.2,
    session_id: str = "",
) -> tuple[str, dict]:
    """Return (assistant_text, usage_info). Raises LLMError with a readable message."""
    if not api_key:
        raise LLMError(
            "No API key. Put OPENCODE_API_KEY in .env or paste a key in the form."
        )
    url = f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if LLM_REASONING_EFFORT and "opencode.ai" in url:
        payload["reasoning_effort"] = LLM_REASONING_EFFORT
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "ScrapeSmith/0.1",
        "x-opencode-session": session_id or _PROCESS_SESSION,
    }
    text_parts: list[str] = []
    usage: dict = {}
    try:
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code == 400 and "reasoning_effort" in response.text:
                # Some models do not accept the parameter; retry once without it.
                payload.pop("reasoning_effort", None)
                response = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise LLMError(f"Network error talking to the model: {exc}") from exc

    if response.status_code >= 400:
        raise LLMError(_explain(response.status_code, response.text))

    try:
        data = response.json()
    except ValueError as exc:
        raise LLMError("Model returned a non-JSON response.") from exc

    usage = data.get("usage") or {}
    finish_reason = None
    reasoning_chars = 0
    for choice in data.get("choices") or []:
        message = choice.get("message") or {}
        finish_reason = choice.get("finish_reason") or finish_reason
        reasoning_chars += len(message.get("reasoning_content") or "")
        content = message.get("content")
        if isinstance(content, str):
            text_parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("text"):
                    text_parts.append(str(part["text"]))

    text = "".join(text_parts).strip()
    if not text:
        raise LLMError(
            "Model returned no text "
            f"(finish_reason={finish_reason}, reasoning_chars={reasoning_chars}). "
            "Raise max tokens, lower reasoning effort, or pick a non-thinking model."
        )
    return text, usage
