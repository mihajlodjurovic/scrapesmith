"""End-to-end pipeline with a fake model: statuses, repair loop, preview, cost."""

from __future__ import annotations

import asyncio

from app import db, pipeline
from tests.test_sandbox import bundle as offline_bundle

FAKE_PROBE = {
    "url": "https://example.com/list",
    "final_url": "https://example.com/list",
    "status": 200,
    "content_type": "text/html; charset=utf-8",
    "fetched_bytes": 4096,
    "proxy_used": "direct",
    "robots": {"found": True, "allowed": True, "crawl_delay": None},
    "title": "Example list",
    "api_candidates": [],
    "embedded_state": [],
    "repeat_blocks": [{"selector": "div.card", "count": 8, "sample": []}],
    "json_ld": [],
    "block_markers": [],
    "skeleton": "<html><body><div class='card'>x</div></body></html>",
}


def fake_probe_factory():
    async def fake_probe(url: str, proxies: list[str]) -> dict:
        return dict(FAKE_PROBE)

    return fake_probe


def test_pipeline_completes_and_previews(monkeypatch) -> None:
    async def fake_build(**kwargs):
        assert kwargs["api_key"] == "secret-key-for-test"
        return offline_bundle(), 0.0012, []

    monkeypatch.setattr(pipeline.discovery, "probe", fake_probe_factory())
    monkeypatch.setattr(pipeline.agent, "build_bundle", fake_build)

    job = db.create_job("https://example.com/list", "openai", "https://api.test/v1", "test-model", {})
    asyncio.run(pipeline.run_job(job["id"], "secret-key-for-test"))

    updated = db.get_job(job["id"])
    assert updated["status"] == "done"
    assert updated["attempts"] == 1
    assert updated["cost_usd"] == 0.0012
    assert updated["preview"]["mapped_count"] == 2
    assert updated["summary"]["source"] == "html"
    assert any(file["name"] == "scraper.py" for file in updated["files"])
    assert (db.job_dir(job["id"]) / "requirements.txt").exists()

    messages = " ".join(event["message"] for event in db.events_since(job["id"]))
    assert "secret-key-for-test" not in messages
    assert "Done: 2 mapped records" in messages


def test_pipeline_repairs_a_bad_bundle(monkeypatch) -> None:
    broken = offline_bundle()
    broken["scraper.py"] = broken["scraper.py"].replace("def fetch_page(", "def oops(", 1)

    calls = {"generate": 0, "repair": 0}

    async def fake_build(**kwargs):
        calls["generate"] += 1
        return broken, 0.001, []

    async def fake_repair(**kwargs):
        calls["repair"] += 1
        assert kwargs["errors"]
        assert any("fetch_page" in error for error in kwargs["errors"])
        return offline_bundle(), {"cost": 0.002}

    monkeypatch.setattr(pipeline.discovery, "probe", fake_probe_factory())
    monkeypatch.setattr(pipeline.agent, "build_bundle", fake_build)
    monkeypatch.setattr(pipeline.generator, "repair", fake_repair)

    job = db.create_job("https://example.com/list", "openai", "https://api.test/v1", "test-model", {})
    asyncio.run(pipeline.run_job(job["id"], "secret"))

    updated = db.get_job(job["id"])
    assert updated["status"] == "done"
    assert updated["attempts"] == 2
    assert updated["cost_usd"] == 0.003
    assert calls == {"generate": 1, "repair": 1}


def test_pipeline_stops_before_paying_when_robots_disallows(monkeypatch) -> None:
    async def strict_probe(url: str, proxies: list[str]) -> dict:
        report = dict(FAKE_PROBE)
        report["robots"] = {"found": True, "allowed": False, "crawl_delay": None}
        return report

    calls = {"build": 0}

    async def fake_build(**kwargs):
        calls["build"] += 1
        return offline_bundle(), 0.0, []

    monkeypatch.setattr(pipeline.discovery, "probe", strict_probe)
    monkeypatch.setattr(pipeline.agent, "build_bundle", fake_build)

    job = db.create_job(
        "https://example.com/private", "openai", "https://api.test/v1", "m", {}
    )
    asyncio.run(pipeline.run_job(job["id"], "secret"))

    updated = db.get_job(job["id"])
    assert updated["status"] == "failed"
    assert "robots.txt" in (updated["error"] or "")
    assert calls["build"] == 0


def test_guess_source_prefers_the_verified_trace() -> None:
    probe = dict(FAKE_PROBE)
    assert pipeline._guess_source(probe) == "html"
    assert pipeline._guess_source(
        probe, [{"url": "https://example.com/list.rss", "observation": {"status": 200}}]
    ) == "feed"
    assert pipeline._guess_source(
        probe, [{"url": "https://example.com/list.json", "observation": {"status": 200}}]
    ) == "json api"
    assert pipeline._guess_source(
        probe, [{"url": "https://example.com/list.json", "observation": {"status": 404}}]
    ) == "html"
    probe["embedded_state"] = [{"kind": "YouTube player response", "present": True}]
    assert pipeline._guess_source(probe) == "embedded state"


def test_describe_probe_returns_warn_tuple_for_block_markers() -> None:
    report = dict(FAKE_PROBE)
    report["block_markers"] = ["captcha", "just a moment"]
    lines = pipeline.describe_probe(report)
    assert any(isinstance(line, tuple) and line[1] == "warn" for line in lines)


def test_not_polite_mode_continues_despite_robots(monkeypatch) -> None:
    async def strict_probe(url: str, proxies: list[str]) -> dict:
        report = dict(FAKE_PROBE)
        report["robots"] = {"found": True, "allowed": False, "crawl_delay": None}
        return report

    calls = {"build": 0}

    async def fake_build(**kwargs):
        calls["build"] += 1
        return offline_bundle(), 0.0, []

    monkeypatch.setattr(pipeline.discovery, "probe", strict_probe)
    monkeypatch.setattr(pipeline.agent, "build_bundle", fake_build)

    job = db.create_job(
        "https://example.com/private",
        "openai",
        "https://api.test/v1",
        "m",
        {"mode": "unrestricted"},
    )
    asyncio.run(pipeline.run_job(job["id"], "secret"))

    updated = db.get_job(job["id"])
    assert updated["status"] == "done"
    assert updated["summary"]["mode"] == "unrestricted"
    assert calls["build"] == 1


def test_log_survives_a_console_that_cannot_encode_the_message(monkeypatch) -> None:
    import io
    import sys

    console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", console)
    job = db.create_job("https://example.com/list", "openai", "https://api.test/v1", "m", {})
    pipeline.Log(job["id"], "")("GET /api -> 10 items → ✓")
    assert "→" in db.events_since(job["id"])[-1]["message"]


def test_source_is_judged_by_content_type_not_by_url() -> None:
    probe = dict(FAKE_PROBE)
    html_rss = {"url": "https://x.test/list.rss",
                "observation": {"status": 200, "content_type": "text/html"}}
    real_feed = {"url": "https://x.test/feed",
                 "observation": {"status": 200, "content_type": "application/atom+xml"}}
    assert pipeline._guess_source(probe, [html_rss]) == "html"
    assert pipeline._guess_source(probe, [html_rss, real_feed]) == "feed"
