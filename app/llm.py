"""Provider-neutral OpenAI-compatible chat client."""

from __future__ import annotations

import httpx

from .config import (
    DEFAULT_BASE_URL,
    LLM_EXTRA_HEADERS,
    LLM_MAX_TOKENS,
    LLM_REASONING_EFFORT,
    LLM_RETRY_MAX_TOKENS,
    LLM_TIMEOUT_SECONDS,
)


class LLMError(RuntimeError):
    """A model call failed in a way the user can act on."""


def usage_cost(usage: dict) -> float:
    """USD cost reported by the provider, when present."""
    if not isinstance(usage, dict):
        return 0.0
    values: list[float] = []
    direct = usage.get("cost")
    if isinstance(direct, (int, float)):
        values.append(float(direct))
    for value in usage.values():
        if not isinstance(value, dict):
            continue
        for key in ("cost", "list_cost"):
            nested = value.get(key)
            if isinstance(nested, (int, float)):
                values.append(float(nested))
    return max(values) if values else 0.0


def _explain(status: int, body: str) -> str:
    snippet = body.strip().replace("\n", " ")[:400]
    if status == 401:
        return "Model rejected the API key (401). Check the key in .env or the form."
    if status == 403:
        return (
            "Provider returned 403. The key may lack access to this model, or the "
            "model may not be available for your account or region."
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
) -> tuple[str, dict]:
    """Return (assistant_text, usage_info). Raises LLMError with a readable message."""
    url = f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if LLM_REASONING_EFFORT:
        payload["reasoning_effort"] = LLM_REASONING_EFFORT
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ScrapeSmith/0.1",
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    headers.update(LLM_EXTRA_HEADERS)
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
    if not text and finish_reason == "length" and max_tokens < LLM_RETRY_MAX_TOKENS:
        # Thinking models sometimes spend the whole budget reasoning. One retry with
        # a larger budget usually leaves room for the answer.
        retry_text, retry_usage = await chat(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=messages,
            max_tokens=LLM_RETRY_MAX_TOKENS,
            temperature=temperature,
        )
        merged = dict(retry_usage)
        merged["cost"] = usage_cost(usage) + usage_cost(retry_usage)
        return retry_text, merged
    if not text:
        raise LLMError(
            "Model returned no text "
            f"(finish_reason={finish_reason}, reasoning_chars={reasoning_chars}). "
            "Raise max tokens, lower reasoning effort, or pick a non-thinking model."
        )
    return text, usage
