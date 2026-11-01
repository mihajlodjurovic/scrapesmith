"""Smoke-test the configured model key without printing it.

Usage:
    python scripts/smoke_llm.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import llm  # noqa: E402
from app.config import DEFAULT_BASE_URL, DEFAULT_MODEL, ENV_API_KEY  # noqa: E402


def mask(key: str) -> str:
    if len(key) <= 8:
        return "***"
    return f"***{key[-4:]}"


async def main() -> int:
    if not ENV_API_KEY:
        print("No OPENCODE_API_KEY found. Put it in .env and try again.")
        return 1

    print(f"base_url = {DEFAULT_BASE_URL}")
    print(f"model    = {DEFAULT_MODEL}")
    print(f"api_key  = {mask(ENV_API_KEY)}")

    try:
        text, usage = await llm.chat(
            base_url=DEFAULT_BASE_URL,
            api_key=ENV_API_KEY,
            model=DEFAULT_MODEL,
            messages=[{"role": "user", "content": "Reply with exactly: ok"}],
            max_tokens=32,
            temperature=0,
        )
    except llm.LLMError as exc:
        print(f"FAILED: {exc}")
        return 2

    print(f"response = {text[:120]!r}")
    print(f"usage    = {json.dumps(usage)[:600]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
