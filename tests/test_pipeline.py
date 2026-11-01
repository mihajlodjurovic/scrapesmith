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
    async def fake_generate(**kwargs):
        assert kwargs["api_key"] == "secret-key-for-test"
        return offline_bundle(), {"cost": 0.0012}

    monkeypatch.setattr(pipeline.discovery, "probe", fake_probe_factory())
    monkeypatch.setattr(pipeline.generator, "generate", fake_generate)

    job = db.create_job("https://example.com/list", "opencode", "https://api.test/v1", "test-model", {})
    asyncio.run(pipeline.run_job(job["id"], "secret-key-for-test"))

    updated = db.get_job(job["id"])
    assert updated["status"] == "done"
    assert updated["attempts"] == 1
    assert updated["cost_usd"] == 0.0012
    assert updated["preview"]["mapped_count"] == 2
    assert updated["summary"]["source"] == "html"
    assert any(file["name"] == "scraper.py" for file in updated["files"])

    messages = " ".join(event["message"] for event in db.events_since(job["id"]))
    assert "secret-key-for-test" not in messages
    assert "Done: 2 mapped records" in messages


def test_pipeline_repairs_a_bad_bundle(monkeypatch) -> None:
    broken = offline_bundle()
    broken["scraper.py"] = broken["scraper.py"].replace("def fetch_page(", "def oops(", 1)

    calls = {"generate": 0, "repair": 0}

    async def fake_generate(**kwargs):
        calls["generate"] += 1
        return broken, {"cost": 0.001}

    async def fake_repair(**kwargs):
        calls["repair"] += 1
        assert kwargs["errors"]
        assert any("fetch_page" in error for error in kwargs["errors"])
        return offline_bundle(), {"cost": 0.002}

    monkeypatch.setattr(pipeline.discovery, "probe", fake_probe_factory())
    monkeypatch.setattr(pipeline.generator, "generate", fake_generate)
    monkeypatch.setattr(pipeline.generator, "repair", fake_repair)

    job = db.create_job("https://example.com/list", "opencode", "https://api.test/v1", "test-model", {})
    asyncio.run(pipeline.run_job(job["id"], "secret"))

    updated = db.get_job(job["id"])
    assert updated["status"] == "done"
    assert updated["attempts"] == 2
    assert updated["cost_usd"] == 0.003
    assert calls == {"generate": 1, "repair": 1}
