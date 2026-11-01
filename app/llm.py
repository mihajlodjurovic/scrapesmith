"""OpenAI-compatible chat client (OpenCode Console, OpenAI, OpenRouter, custom)."""

from __future__ import annotations

import httpx

from .config import DEFAULT_BASE_URL, LLM_MAX_TOKENS, LLM_TIMEOUT_SECONDS


class LLMError(RuntimeError):
    """A model call failed in a way the user can act on."""


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
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "ScrapeSmith/0.1",
    }
    if session_id:
        headers["x-opencode-session"] = session_id
    text_parts: list[str] = []
    usage: dict = {}
    try:
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT_SECONDS) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise LLMError(_explain(response.status_code, body))
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if not chunk or chunk == "[DONE]":
                        continue
                    try:
                        data = httpx.Response(200, content=chunk).json()
                    except Exception:
                        continue
                    if data.get("usage"):
                        usage = data["usage"]
                    for choice in data.get("choices", []):
                        delta = choice.get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            text_parts.append(piece)
    except LLMError:
        raise
    except httpx.HTTPError as exc:
        raise LLMError(f"Network error talking to the model: {exc}") from exc

    text = "".join(text_parts).strip()
    if not text:
        raise LLMError("Model returned an empty response.")
    return text, usage
