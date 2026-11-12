"""Runtime configuration, loaded from the environment (and .env in development)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.getenv("SCRAPESMITH_DATA_DIR", BASE_DIR / "data"))
JOBS_DIR = DATA_DIR / "jobs"
DB_PATH = DATA_DIR / "scrapesmith.db"

APP_NAME = "ScrapeSmith"
APP_VERSION = "0.2.0"

# LLM defaults. Any OpenAI-compatible endpoint works: set the base URL, model
# and key, or pick a preset in the UI.
DEFAULT_PROVIDER = os.getenv("SCRAPESMITH_PROVIDER", "openai")
DEFAULT_BASE_URL = os.getenv(
    "SCRAPESMITH_BASE_URL", "https://api.openai.com/v1"
).rstrip("/")
DEFAULT_MODEL = os.getenv("SCRAPESMITH_MODEL", "gpt-4.1-mini")
ENV_API_KEY = (
    os.getenv("SCRAPESMITH_API_KEY") or os.getenv("LLM_API_KEY") or ""
).strip()

LLM_TIMEOUT_SECONDS = float(os.getenv("SCRAPESMITH_LLM_TIMEOUT", "300"))
LLM_MAX_TOKENS = int(os.getenv("SCRAPESMITH_LLM_MAX_TOKENS", "16000"))
# One retry with this budget when a thinking model spends everything on reasoning.
LLM_RETRY_MAX_TOKENS = int(os.getenv("SCRAPESMITH_LLM_RETRY_MAX_TOKENS", "32000"))
MAX_REPAIR_ATTEMPTS = int(os.getenv("SCRAPESMITH_MAX_REPAIRS", "2"))

# Discovery limits
FETCH_TIMEOUT_SECONDS = float(os.getenv("SCRAPESMITH_FETCH_TIMEOUT", "25"))
MAX_HTML_BYTES = int(os.getenv("SCRAPESMITH_MAX_HTML_BYTES", "4_000_000"))
SKELETON_MAX_CHARS = int(os.getenv("SCRAPESMITH_SKELETON_CHARS", "45000"))
ALLOW_PRIVATE_NETWORKS = os.getenv("SCRAPESMITH_ALLOW_PRIVATE", "0") == "1"

# Sandbox run limits
SANDBOX_TIMEOUT_SECONDS = int(os.getenv("SCRAPESMITH_SANDBOX_TIMEOUT", "180"))
SANDBOX_MAX_PAGES = int(os.getenv("SCRAPESMITH_SANDBOX_MAX_PAGES", "1"))
SANDBOX_MAX_OUTPUT_BYTES = int(os.getenv("SCRAPESMITH_SANDBOX_MAX_OUTPUT", "5_000_000"))

# Headless browser fallback for client-side rendered pages (needs the optional
# `playwright` package and `playwright install chromium`).
#   auto   render when the probe looks like a JavaScript shell, and fall back to a
#          browser-based scraper when the plain HTTP bundle fails (default)
#   always render every page during discovery, same fallback rules
#   off    never start a browser
# Anti-bot wall + proxies: how many proxies to test before the model is called
# (0 = every proxy in the list), how many at once, and the per-proxy timeout.
PROXY_CHECK_LIMIT = int(os.getenv("SCRAPESMITH_PROXY_CHECK_LIMIT", "0"))
PROXY_CHECK_CONCURRENCY = int(os.getenv("SCRAPESMITH_PROXY_CHECK_CONCURRENCY", "8"))
PROXY_CHECK_TIMEOUT_SECONDS = float(os.getenv("SCRAPESMITH_PROXY_CHECK_TIMEOUT", "12"))

BROWSER_MODE = os.getenv("SCRAPESMITH_BROWSER", "auto").strip().lower()
BROWSER_MODES = {"auto", "always", "off"}
RENDER_TIMEOUT_SECONDS = float(os.getenv("SCRAPESMITH_RENDER_TIMEOUT", "45"))
BROWSER_SANDBOX_TIMEOUT_SECONDS = int(os.getenv("SCRAPESMITH_BROWSER_SANDBOX_TIMEOUT", "300"))

# Politeness mode. "polite" respects robots.txt; "unrestricted" ignores it.
DEFAULT_MODE = os.getenv("SCRAPESMITH_MODE", "polite")

# Thinking models spend tokens on reasoning; keep that budget small for codegen.
LLM_REASONING_EFFORT = os.getenv("SCRAPESMITH_REASONING_EFFORT", "low")


def _extra_headers() -> dict[str, str]:
    raw = os.getenv("SCRAPESMITH_LLM_HEADERS", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


# Optional headers for gateways that require routing or version headers.
LLM_EXTRA_HEADERS = _extra_headers()

USER_AGENT = os.getenv(
    "SCRAPESMITH_USER_AGENT",
    "ScrapeSmith/0.1 (+https://github.com/mihajlodjurovic/scrapesmith; polite crawler)",
)

PROVIDER_PRESETS = {
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4.1-mini", "gpt-4.1", "gpt-4o"],
        "requires_key": True,
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "models": ["anthropic/claude-sonnet-4", "google/gemini-2.5-pro"],
        "requires_key": True,
    },
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "models": ["llama-3.3-70b-versatile", "qwen-2.5-32b"],
        "requires_key": True,
    },
    "together": {
        "label": "Together AI",
        "base_url": "https://api.together.xyz/v1",
        "models": ["meta-llama/Llama-3.3-70B-Instruct-Turbo"],
        "requires_key": True,
    },
    "ollama": {
        "label": "Ollama (local)",
        "base_url": "http://127.0.0.1:11434/v1",
        "models": ["qwen2.5-coder:14b", "llama3.1:8b"],
        "requires_key": False,
    },
    "custom": {
        "label": "Custom OpenAI-compatible",
        "base_url": "",
        "models": [],
        "requires_key": False,
    },
}
