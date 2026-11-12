"""A build must get its data from the target site, not from archives or caches."""

from __future__ import annotations

import asyncio

from app import db, pipeline
from app.validate import validate_bundle
from tests.test_pipeline import FAKE_PROBE
from tests.test_sandbox import bundle as offline_bundle

TARGET = "https://www.cars.example.rs/oglasi?page=1"


def _with_fetch(url_line: str) -> dict[str, str]:
    files = offline_bundle()
    files["scraper.py"] = files["scraper.py"].replace("DELAY = 0.2", f"DELAY = 0.2\n{url_line}")
    return files


def _source_errors(files: dict[str, str], **kwargs) -> list[str]:
    return [
        error
        for error in validate_bundle(files, **kwargs)
        if "data must come from" in error or "outside" in error
    ]


def test_archive_and_search_fallbacks_are_rejected_even_without_a_target() -> None:
    for line in (
        'WAYBACK = "https://web.archive.org/web/2026/{url}"',
        'AVAILABLE = "https://archive.org/wayback/available"',
        'SERP = "https://html.duckduckgo.com/html/"',
        'CACHE = "https://webcache.googleusercontent.com/search?q=cache:{url}"',
        'API = "https://api.scraperapi.com/?url={url}"',
    ):
        errors = _source_errors(_with_fetch(line))
        assert errors, line
        assert "never from archives" in errors[0]


def test_same_site_hosts_and_neutral_urls_are_fine() -> None:
    files = _with_fetch(
        'BASE = "https://www.cars.example.rs/oglasi"\n'
        'CDN = "https://cdn.cars.example.rs/thumbs/"\n'
        'API = "https://api.cars.example.rs/v1/search"\n'
        'LD = "https://schema.org/Car"'
    )
    assert _source_errors(files, target_url=TARGET) == []


def test_foreign_hosts_need_to_be_seen_on_the_page() -> None:
    files = _with_fetch('SEARCH = "https://abc123-dsn.algolia.net/1/indexes/cars/query"')
    errors = _source_errors(files, target_url=TARGET)
    assert any("abc123-dsn.algolia.net" in error for error in errors)
    # The rendered page itself called that host, so it is part of the site.
    assert _source_errors(
        files, target_url=TARGET, allowed_hosts={"abc123-dsn.algolia.net"}
    ) == []


def _blocked_probe(**overrides) -> dict:
    return {
        **FAKE_PROBE,
        "status": 403,
        "repeat_blocks": [],
        "block_markers": ["just a moment", "enable javascript"],
        "skeleton": "<html><body>Just a moment...</body></html>",
        **overrides,
    }


def _run(probe: dict, options: dict, monkeypatch) -> tuple[dict, int]:
    calls = {"build": 0}

    async def fake_probe(url, pool):
        return dict(probe)

    async def fake_build(**kwargs):
        calls["build"] += 1
        return offline_bundle(), 0.001, []

    monkeypatch.setattr(pipeline.discovery, "probe", fake_probe)
    monkeypatch.setattr(pipeline.agent, "build_bundle", fake_build)
    job = db.create_job(TARGET, "openai", "https://api.test/v1", "m", options)
    asyncio.run(pipeline.run_job(job["id"], "secret"))
    return db.get_job(job["id"]), calls["build"]


def test_anti_bot_wall_without_proxies_stops_before_spending_tokens(monkeypatch) -> None:
    job, builds = _run(_blocked_probe(), {"browser": "off"}, monkeypatch)
    assert job["status"] == "failed"
    assert builds == 0
    assert "anti-bot challenge" in job["error"]
    assert "archives" in job["error"]
    assert job["cost_usd"] == 0


def _fake_check(passed_via):
    async def check(url, pool, **kwargs):
        return {
            "passed_via": passed_via,
            "tested": [{"proxy": "http://***@1.2.3.4:8000", "status": 403, "markers": ["x"]}],
        }

    return check


def test_anti_bot_wall_continues_when_a_user_proxy_gets_through(monkeypatch) -> None:
    monkeypatch.setattr(pipeline.discovery, "find_unblocked", _fake_check("http://***@5.6.7.8:80"))
    job, builds = _run(
        _blocked_probe(), {"browser": "off", "proxies": ["1.2.3.4:8000"]}, monkeypatch
    )
    assert builds == 1
    assert job["status"] == "done"


def test_anti_bot_wall_stops_when_the_proxies_are_challenged_too(monkeypatch) -> None:
    monkeypatch.setattr(pipeline.discovery, "find_unblocked", _fake_check(None))
    job, builds = _run(
        _blocked_probe(), {"browser": "off", "proxies": ["1.2.3.4:8000"]}, monkeypatch
    )
    assert builds == 0
    assert job["status"] == "failed"
    assert "also appears through every proxy tested" in job["error"]
    assert job["cost_usd"] == 0


def test_render_runs_at_most_once_per_job(monkeypatch) -> None:
    from app import render as render_module

    calls = {"render": 0}

    async def failing_render(url, pool=None):
        calls["render"] += 1
        raise render_module.RenderError("Headless render failed on all 1 routes: direct: HTTP 403")

    monkeypatch.setattr(pipeline.render, "available", lambda: True)
    monkeypatch.setattr(pipeline.render, "render", failing_render)
    job, builds = _run(_blocked_probe(), {"browser": "auto"}, monkeypatch)
    assert calls["render"] == 1
    assert builds == 0


def test_a_render_that_gets_through_lifts_the_block(monkeypatch) -> None:
    async def fake_render(url, pool=None):
        return {"status": 200, "block_markers": [], "network": [], "repeat_blocks": []}

    monkeypatch.setattr(pipeline.render, "available", lambda: True)
    monkeypatch.setattr(pipeline.render, "render", fake_render)
    monkeypatch.setattr(pipeline.render, "describe", lambda rendered: [])
    job, builds = _run(_blocked_probe(), {"browser": "auto"}, monkeypatch)
    assert builds == 1
    assert job["status"] == "done"


def test_a_403_without_challenge_markers_is_not_treated_as_a_wall() -> None:
    assert pipeline.is_blocked(_blocked_probe(block_markers=[])) is False
    assert pipeline.is_blocked(_blocked_probe()) is True
    assert pipeline.is_blocked(
        _blocked_probe(rendered={"status": 403, "block_markers": ["just a moment"]})
    ) is True


def test_prompt_forbids_archive_fallbacks() -> None:
    from app.prompt import system_prompt

    assert "Never use web archives" in system_prompt()
    assert "A failed\n   build is better than a fake one." in system_prompt(browser=True)
