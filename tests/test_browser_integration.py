"""Real headless Chromium against a local client-side rendered site.

Skipped unless Playwright and its Chromium build are installed:
    pip install -r requirements-browser.txt && python -m playwright install chromium
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from app import db, discovery, pipeline, render
from app.sandbox import run_bundle
from tests.csr_site import CsrSite
from tests.test_render import _browser_bundle
from tests.test_sandbox import bundle as offline_bundle


def _chromium_installed() -> bool:
    if not render.available():
        return False
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            return Path(playwright.chromium.executable_path).exists()
    except Exception:  # noqa: BLE001 - any failure means "not usable here"
        return False


pytestmark = pytest.mark.skipif(
    not _chromium_installed(), reason="Playwright + Chromium are not installed"
)


@pytest.fixture
def site():
    with CsrSite() as running:
        yield running


def _browser_bundle_for(url: str) -> dict[str, str]:
    files = _browser_bundle()
    scraper = files["scraper.py"]
    scraper = scraper.replace('BASE_URL = "https://example.com/search"', f'BASE_URL = "{url}"')
    scraper = scraper.replace(
        'WAIT_SELECTOR = "article, li.product, div.card"', 'WAIT_SELECTOR = "article.card"'
    )
    files["scraper.py"] = scraper
    return files


def test_render_captures_the_api_and_the_rendered_dom(site) -> None:
    report = asyncio.run(render.render(site.url("/")))

    assert report["status"] == 200
    best = report["network"][0]
    assert re.search(r"/api/items\?page=1$", best["url"])
    assert best["item_count"] == 8
    assert best["list_path"] == "data.items"
    assert report["repeat_blocks"][0]["selector"] == "article.card"
    assert report["block_markers"] == []


def test_render_blocks_redirects_to_private_hosts(site, monkeypatch) -> None:
    monkeypatch.setattr(discovery, "ALLOW_PRIVATE_NETWORKS", False)
    monkeypatch.setattr(discovery, "_is_public_host", lambda host: host == "localhost")

    with pytest.raises((render.RenderError, discovery.DiscoveryError)):
        asyncio.run(render.render(site.url("/go-private", host="localhost")))
    assert site.secret_hits == 0


def test_reference_browser_scraper_runs_in_the_sandbox(site, tmp_path) -> None:
    result = run_bundle(tmp_path, _browser_bundle_for(site.url("/")), [], max_pages=2, timeout=180)

    assert result["ok"] is True, result["errors"]
    assert result["raw_count"] == 8  # page 2 is empty, so the crawl stops
    assert result["mapped_count"] == 8
    assert result["fill_rates"]["id"] == 100.0


def test_reference_browser_scraper_collects_an_infinite_scroll_list(site, tmp_path) -> None:
    files = _browser_bundle()
    scraper = files["scraper.py"]
    scraper = scraper.replace(
        'BASE_URL = "https://example.com/search"', f'BASE_URL = "{site.url("/scroll")}"'
    )
    scraper = scraper.replace(
        'WAIT_SELECTOR = "article, li.product, div.card"', 'WAIT_SELECTOR = "article.lamp"'
    )
    scraper = scraper.replace('API_PATTERN = re.compile(r"/api/")', 'API_PATTERN = re.compile(r"/nothing/")')
    files["scraper.py"] = scraper.replace(
        'SCROLL_PAUSE = float(os.getenv("SCRAPESMITH_SCROLL_PAUSE", "0.8"))',
        'SCROLL_PAUSE = float(os.getenv("SCRAPESMITH_SCROLL_PAUSE", "0.4"))',
    )

    result = run_bundle(tmp_path, files, [], max_pages=1, timeout=180)

    assert result["ok"] is True, result["errors"]
    assert result["raw_count"] == 30  # 5 lazy batches of 6, not just the first one
    assert "[scroll] 30 items after scrolling" in result["stdout"]


def test_pipeline_end_to_end_on_a_client_side_site(site, monkeypatch) -> None:
    """Real probe, real render, real sandbox; only the model is faked."""
    strategies: list[str] = []

    async def fake_build(**kwargs):
        if kwargs["browser"]:
            strategies.append("browser")
            return _browser_bundle_for(site.url("/")), 0.002, []
        strategies.append("http")
        rendered = kwargs["probe"].get("rendered") or {}
        assert rendered["network"][0]["item_count"] == 8  # the model saw the API
        files = offline_bundle()
        files["scraper.py"] = files["scraper.py"].replace(
            "for item in fetch_page(1):", "for item in []:"
        )
        return files, 0.001, []

    monkeypatch.setattr(pipeline, "MAX_REPAIR_ATTEMPTS", 0)
    monkeypatch.setattr(pipeline.agent, "build_bundle", fake_build)

    job = db.create_job(site.url("/"), "openai", "https://api.test/v1", "m", {"browser": "auto"})
    asyncio.run(pipeline.run_job(job["id"], "secret"))
    final = db.get_job(job["id"])

    assert final["status"] == "done", final["error"]
    assert strategies == ["http", "browser"]
    assert final["summary"]["strategy"] == "browser"
    assert final["preview"]["mapped_count"] == 8
    assert final["summary"]["render_seconds"] > 0
