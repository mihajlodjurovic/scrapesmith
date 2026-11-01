"""Runtime configuration, loaded from the environment (and .env in development)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.getenv("SCRAPESMITH_DATA_DIR", BASE_DIR / "data"))
JOBS_DIR = DATA_DIR / "jobs"
DB_PATH = DATA_DIR / "scrapesmith.db"

APP_NAME = "ScrapeSmith"
APP_VERSION = "0.1.0"

# LLM defaults — OpenCode Go uses a dedicated endpoint so usage counts toward Go.
DEFAULT_PROVIDER = os.getenv("SCRAPESMITH_PROVIDER", "opencode-go")
DEFAULT_BASE_URL = os.getenv(
    "SCRAPESMITH_BASE_URL", "https://opencode.ai/inference/go/openai/v1"
).rstrip("/")
DEFAULT_MODEL = os.getenv("SCRAPESMITH_MODEL", "deepseek-v4.1-flash")
ENV_API_KEY = os.getenv("OPENCODE_API_KEY", "").strip()

LLM_TIMEOUT_SECONDS = float(os.getenv("SCRAPESMITH_LLM_TIMEOUT", "300"))
LLM_MAX_TOKENS = int(os.getenv("SCRAPESMITH_LLM_MAX_TOKENS", "16000"))
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

# Politeness mode. "polite" respects robots.txt; "unrestricted" ignores it.
DEFAULT_MODE = os.getenv("SCRAPESMITH_MODE", "polite")

# Thinking models spend tokens on reasoning; keep that budget small for codegen.
LLM_REASONING_EFFORT = os.getenv("SCRAPESMITH_REASONING_EFFORT", "low")

USER_AGENT = os.getenv(
    "SCRAPESMITH_USER_AGENT",
    "ScrapeSmith/0.1 (+https://github.com/; polite research crawler)",
)

PROVIDER_PRESETS = {
    "opencode-go": {
        "label": "OpenCode Go (subscription)",
        "base_url": "https://opencode.ai/inference/go/openai/v1",
        "models": [
            "deepseek-v4.1-flash",
            "glm-5.3-flash",
            "minimax-m3.1-flash",
            "glm-5.3",
            "kimi-k3",
        ],
    },
    "opencode": {
        "label": "OpenCode (pay-as-you-go)",
        "base_url": "https://opencode.ai/inference/openai/v1",
        "models": ["minimax-m3", "kimi-k3", "mistral-large-4", "glm-5.3"],
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4.1", "gpt-4.1-mini", "gpt-4o"],
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "models": ["anthropic/claude-sonnet-4", "google/gemini-2.5-pro"],
    },
    "custom": {
        "label": "Custom OpenAI-compatible",
        "base_url": "",
        "models": [],
    },
}
