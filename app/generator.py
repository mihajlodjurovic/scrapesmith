"""Turn model output into a file bundle, then repair it with error feedback."""

from __future__ import annotations

import re
from typing import Any

from . import llm
from .prompt import SYSTEM, user_prompt

FILE_HEADER = re.compile(r"^[#*>\s]*FILE:\s*`?([A-Za-z0-9_.\-]+)`?\s*$", re.M)
FENCE_START = re.compile(r"^```[a-zA-Z0-9_+-]*\s*\n")
FENCE_END = re.compile(r"\n```\s*$")

REQUIRED_FILES = ("scraper.py", "mapper.py", "runner.sh", "README.md")
FILE_ORDER = REQUIRED_FILES + ("NOTES",)


class GenerationError(RuntimeError):
    pass


def parse_files(text: str) -> dict[str, str]:
    matches = list(FILE_HEADER.finditer(text))
    if not matches:
        raise GenerationError(
            "Model reply did not contain '### FILE: name' sections. "
            f"First 300 characters: {text[:300]!r}"
        )

    files: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group(1).strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[match.end() : end].strip()
        chunk = FENCE_START.sub("", chunk)
        chunk = FENCE_END.sub("", chunk)
        files[name] = chunk.strip() + "\n"

    missing = [name for name in REQUIRED_FILES if name not in files]
    if missing:
        raise GenerationError(f"Model reply is missing files: {', '.join(missing)}")
    return files


async def generate(
    *,
    probe: dict[str, Any],
    options: dict[str, Any],
    base_url: str,
    api_key: str,
    model: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user_prompt(probe, options)},
    ]
    return await _chat_parse(messages, base_url, api_key, model)


async def repair(
    *,
    files: dict[str, str],
    errors: list[str],
    base_url: str,
    api_key: str,
    model: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    rendered = "\n\n".join(
        f"### FILE: {name}\n```{_language(name)}\n{files[name]}```"
        for name in REQUIRED_FILES
        if name in files
    )
    messages = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": (
                "Your previous bundle failed validation or the sandbox run. "
                "Fix it and return the COMPLETE bundle again — all four files, "
                "never a diff or a single file.\n\n"
                "## Errors\n"
                + "\n".join(f"- {error}" for error in errors)
                + "\n\n## Previous bundle\n"
                + rendered
            ),
        },
    ]
    return await _chat_parse(messages, base_url, api_key, model)


async def _chat_parse(
    messages: list[dict[str, str]],
    base_url: str,
    api_key: str,
    model: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    """One model call; if the bundle is incomplete, ask once more for all files."""
    text, usage = await llm.chat(
        base_url=base_url,
        api_key=api_key,
        model=model,
        messages=messages,
    )
    try:
        return parse_files(text), usage
    except GenerationError as exc:
        followup = messages + [
            {"role": "assistant", "content": text},
            {
                "role": "user",
                "content": (
                    f"That reply was incomplete: {exc}. Return ALL four files now — "
                    "scraper.py, mapper.py, runner.sh, README.md — each in its own "
                    "### FILE: section with a fenced code block. No diffs."
                ),
            },
        ]
        text2, usage2 = await llm.chat(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=followup,
        )
        merged = {"cost": llm.usage_cost(usage) + llm.usage_cost(usage2)}
        return parse_files(text2), merged


def _language(name: str) -> str:
    if name.endswith(".py"):
        return "python"
    if name.endswith(".sh"):
        return "bash"
    if name.endswith(".md"):
        return "markdown"
    return "text"
