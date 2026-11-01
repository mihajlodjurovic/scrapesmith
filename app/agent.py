"""Bounded agent loop: the model may verify data sources before writing code.

The model gets one tool, `fetch`, restricted to the target's registrable domain
and capped by a small budget. It can discover a JSON API, an RSS/Atom feed or a
server-rendered variant of a JavaScript shell, then emit the bundle.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from . import llm
from .config import FETCH_TIMEOUT_SECONDS, LLM_MAX_TOKENS
from .discovery import html_signals
from .generator import GenerationError, parse_files
from .prompt import SYSTEM as SCRAPER_SYSTEM
from .prompt import user_prompt
from .proxies import ProxyPool, mask_proxy

TOOL_LIMIT = 6
SAME_SITE_MIN_DELAY = 0.4

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "application/json, application/atom+xml, application/rss+xml, "
        "text/html;q=0.9, */*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

TOOL_PROTOCOL = """## Verification tools (max 6 calls)

Before writing code you may inspect candidate data sources.

Reply with exactly one line in this format:

TOOL: fetch <absolute-url>

The tool answers with a JSON observation:

{"url": "...", "status": 200, "content_type": "...", "bytes": 12345,
 "entry_count": 12, "signals": [{"kind": "Next.js data", "marker": "__NEXT_DATA__",
 "snippet": "..."}], "preview": "first 3000 characters"}

Rules
- Only URLs on the same site as the target (subdomains are allowed). Anything
  else is refused.
- The discovery report already describes the target page. Do not re-fetch that
  exact URL unless you need a different variant (language, page, format).
- The page may be a JavaScript shell with no data. Before giving up, verify at
  least two candidates from the list above (feed/JSON siblings, API paths,
  subdomains). A 403 on one route does not mean the site is closed.
- `signals` lists embedded payloads found in the HTML; use the snippet to decide
  which one holds the data.
- Respect the site: no logins, no CAPTCHA solving, no private data, at most six
  requests with a small delay.
- Stop calling tools as soon as you know where the data comes from, then output
  the four files exactly as specified.
"""


class AgentError(RuntimeError):
    pass


def registrable_domain(host: str) -> str:
    parts = [part for part in host.lower().strip(".").split(".") if part]
    if len(parts) <= 2:
        return ".".join(parts)
    return ".".join(parts[-2:])


@dataclass
class FetchTool:
    target_url: str
    proxies: list[str] = field(default_factory=list)
    pool: ProxyPool | None = None
    calls: int = 0
    limit: int = TOOL_LIMIT

    def __post_init__(self) -> None:
        self.host = registrable_domain(urlparse(self.target_url).hostname or "")
        if self.pool is None:
            self.pool = ProxyPool(self.proxies)

    def allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        return registrable_domain(parsed.hostname) == self.host

    async def fetch(self, url: str) -> dict[str, Any]:
        if self.calls >= self.limit:
            return {"error": f"tool budget exhausted after {self.limit} calls"}
        if not self.allowed(url):
            return {
                "error": "refused: this tool only fetches the target's own site",
                "url": url,
            }

        self.calls += 1
        attempts: list[str | None] = self.pool.attempts(2) if self.pool else [None]
        last_error = "no attempt made"
        response: httpx.Response | None = None
        used = "direct"

        for proxy in attempts:
            try:
                async with httpx.AsyncClient(
                    headers=BROWSER_HEADERS,
                    timeout=FETCH_TIMEOUT_SECONDS,
                    follow_redirects=True,
                    max_redirects=5,
                    proxy=proxy,
                ) as client:
                    response = await client.get(url)
                used = "direct" if proxy is None else mask_proxy(proxy)
                if response.status_code < 500:
                    if self.pool:
                        healthy = response.status_code not in (401, 403, 407, 429)
                        self.pool.report(proxy, healthy, response.status_code)
                    break
                if self.pool:
                    self.pool.report(proxy, False, response.status_code)
                last_error = f"HTTP {response.status_code} via {used}"
            except Exception as exc:  # noqa: BLE001 - reported to the model
                if self.pool:
                    self.pool.report(proxy, False)
                last_error = f"{type(exc).__name__}: {exc}"
                response = None

        if response is None:
            return {"url": url, "error": last_error}

        body = response.content[:1_000_000]
        text = body.decode(response.encoding or "utf-8", errors="replace")
        content_type = response.headers.get("content-type", "")
        entry_count = text.count("<entry") if "xml" in content_type else None

        observation: dict[str, Any] = {
            "url": str(response.url),
            "requested_url": url,
            "status": response.status_code,
            "content_type": content_type,
            "bytes": len(body),
            "proxy": used,
            "preview": text[:3000],
        }
        if entry_count is not None:
            observation["entry_count"] = entry_count
        if "html" in content_type:
            signals = html_signals(text)
            if signals:
                observation["signals"] = signals
        return observation


def parse_tool_call(text: str) -> str | None:
    for line in text.splitlines():
        line = line.strip().strip("`")
        if line.upper().startswith("TOOL:"):
            rest = line.split(":", 1)[1].strip()
            if rest.lower().startswith("fetch"):
                return rest[5:].strip()
    return None


def _cost(usage: dict[str, Any]) -> float:
    return llm.usage_cost(usage)


async def build_bundle(
    *,
    probe: dict[str, Any],
    options: dict[str, Any],
    base_url: str,
    api_key: str,
    model: str,
    session_id: str,
    proxies: list[str],
    pool: ProxyPool | None = None,
) -> tuple[dict[str, str], float, list[dict[str, Any]]]:
    target_url = probe.get("final_url") or probe.get("url") or ""
    tool = FetchTool(target_url=target_url, proxies=proxies, pool=pool)
    messages = [
        {"role": "system", "content": SCRAPER_SYSTEM + "\n" + TOOL_PROTOCOL},
        {"role": "user", "content": user_prompt(probe, options)},
    ]
    trace: list[dict[str, Any]] = []
    cost = 0.0

    for step in range(TOOL_LIMIT):
        text, usage = await llm.chat(
            base_url=base_url,
            api_key=api_key,
            model=model,
            messages=messages,
            max_tokens=LLM_MAX_TOKENS,
            session_id=session_id,
        )
        cost += _cost(usage)
        messages.append({"role": "assistant", "content": text})

        if "### FILE:" in text:
            try:
                return parse_files(text), cost, trace
            except GenerationError as exc:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"That bundle was incomplete: {exc}. Return all four files — "
                            "scraper.py, mapper.py, runner.sh, README.md."
                        ),
                    }
                )
                continue

        url = parse_tool_call(text)
        if url is None:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "No tool call and no files were found. Either reply with "
                        "'TOOL: fetch <url>' or output the four files now."
                    ),
                }
            )
            continue

        observation = await tool.fetch(url)
        trace.append({"step": step + 1, "url": url, "observation": observation})
        messages.append(
            {
                "role": "user",
                "content": "OBSERVATION: " + json.dumps(observation, ensure_ascii=False)[:6000],
            }
        )

    messages.append(
        {
            "role": "user",
            "content": "Stop probing. Output the four files now, using what you learned.",
        }
    )
    text, usage = await llm.chat(
        base_url=base_url,
        api_key=api_key,
        model=model,
        messages=messages,
        max_tokens=LLM_MAX_TOKENS,
        session_id=session_id,
    )
    cost += _cost(usage)
    if "### FILE:" not in text:
        raise AgentError("Model did not produce a bundle after the tool budget ran out.")
    try:
        return parse_files(text), cost, trace
    except GenerationError as exc:
        raise AgentError(f"Model returned an incomplete bundle: {exc}") from exc
