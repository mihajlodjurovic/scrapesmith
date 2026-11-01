"""Shared Jinja environment."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.templating import Jinja2Templates

from .config import BASE_DIR

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


def asset_url(path: str) -> str:
    """Cache-bust static assets by file mtime."""
    relative = path.lstrip("/")
    if relative.startswith("static/"):
        relative = relative[len("static/") :]
    file = BASE_DIR / "app" / "static" / relative
    try:
        version = int(file.stat().st_mtime)
    except OSError:
        version = 0
    return f"/static/{relative}?v={version}"


def time_ago(value: float | None) -> str:
    if not value:
        return ""
    seconds = int(datetime.now(UTC).timestamp() - value)
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86_400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86_400}d ago"


def money(value: float | None) -> str:
    if not value:
        return "$0.0000"
    return f"${value:.4f}"


templates.env.filters["time_ago"] = time_ago
templates.env.filters["money"] = money
templates.env.globals["asset_url"] = asset_url
