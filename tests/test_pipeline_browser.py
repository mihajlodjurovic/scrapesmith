"""Strategy order with a fake model and a fake browser: http -> api -> browser."""

from __future__ import annotations

import asyncio

import pytest

from app import db, pipeline, render
from tests.test_pipeline import FAKE_PROBE
from tests.test_render import RENDERED_HTML, _captured
from tests.test_sandbox import bundle as offline_bundle

SHELL_PROBE = {
    **FAKE_PROBE,
    "repeat_blocks": [],
    "skeleton": '<html><body><div id="root"></div></body></html>',
}


def empty_bundle() -> dict[str, str]:
    """Passes validation, runs, but writes no records — a JavaScript-shell failure."""
    files = offline_bundle()
    files["scraper.py"] = files["scraper.py"].replace("for item in fetch_page(1):", "for item in []:")
    return files


@pytest.fixture
def harness(monkeypatch):
    calls: dict = {"builds": [], "renders": 0}

    def setup(probe: dict, succeed_on: str, rendered: dict | None = None, available=True):
        async def fake_probe(url, pool):
            return dict(probe)

        async def fake_render(url, pool=None):
            calls["renders"] += 1
            return rendered or render.analyze(RENDERED_HTML, url, _captured())

        async def fake_build(**kwargs):
            strategy = "browser" if kwargs["browser"] else (
                "api" if kwargs.get("failure") else "http"
            )
            calls["builds"].append(
                {
                    "strategy": strategy,
                    "rendered": "rendered" in kwargs["probe"],
                    "failure": kwargs.get("failure"),
                }
            )
            files = offline_bundle() if strategy == succeed_on else empty_bundle()
            return files, 0.001, []

        monkeypatch.setattr(pipeline, "MAX_REPAIR_ATTEMPTS", 0)
        monkeypatch.setattr(pipeline.discovery, "probe", fake_probe)
        monkeypatch.setattr(pipeline.render, "render", fake_render)
        monkeypatch.setattr(pipeline.render, "available", lambda: available)
        monkeypatch.setattr(pipeline.agent, "build_bundle", fake_build)
        return calls

    return setup


def _run(options: dict) -> dict:
    job = db.create_job("https://example.com/list", "openai", "https://api.test/v1", "m", options)
    asyncio.run(pipeline.run_job(job["id"], "secret"))
    return db.get_job(job["id"])


def _messages(job_id: str) -> str:
    return " ".join(event["message"] for event in db.events_since(job_id))


def test_shell_is_rendered_early_then_falls_back_to_the_browser(harness) -> None:
    calls = harness(SHELL_PROBE, succeed_on="browser")
    job = _run({"browser": "auto"})

    assert job["status"] == "done", job["error"]
    assert calls["renders"] == 1
    # The early render already showed the API to the http attempt, so no api round.
    assert [(c["strategy"], c["rendered"]) for c in calls["builds"]] == [
        ("http", True),
        ("browser", True),
    ]
    assert any("no records" in error for error in calls["builds"][1]["failure"])
    assert job["summary"]["strategy"] == "browser"
    assert job["summary"]["source"] == "headless browser"
    assert "JavaScript shell" in _messages(job["id"])


def test_server_rendered_page_tries_the_captured_api_before_the_browser(harness) -> None:
    calls = harness(FAKE_PROBE, succeed_on="api")
    job = _run({"browser": "auto"})

    assert job["status"] == "done", job["error"]
    assert calls["renders"] == 1
    assert [(c["strategy"], c["rendered"]) for c in calls["builds"]] == [
        ("http", False),
        ("api", True),
    ]
    assert job["summary"]["strategy"] == "api"
    assert job["summary"]["source"] == "captured api"


def test_no_api_in_the_render_goes_straight_to_the_browser(harness) -> None:
    no_api = render.analyze(RENDERED_HTML, "https://example.com/list", [])
    calls = harness(FAKE_PROBE, succeed_on="browser", rendered=no_api)
    job = _run({"browser": "auto"})

    assert job["status"] == "done", job["error"]
    assert [c["strategy"] for c in calls["builds"]] == ["http", "browser"]


def test_browser_off_keeps_the_old_behaviour(harness) -> None:
    calls = harness(SHELL_PROBE, succeed_on="browser")
    job = _run({"browser": "off"})

    assert job["status"] == "failed"
    assert calls["renders"] == 0
    assert [c["strategy"] for c in calls["builds"]] == ["http"]
    assert "Sandbox run failed after repairs" in job["error"]


def test_missing_playwright_is_reported_not_fatal(harness) -> None:
    calls = harness(SHELL_PROBE, succeed_on="http", available=False)
    job = _run({"browser": "auto"})

    assert job["status"] == "done"
    assert calls["renders"] == 0
    assert "Playwright is not installed" in _messages(job["id"])


def test_successful_http_build_never_starts_a_browser(harness) -> None:
    calls = harness(FAKE_PROBE, succeed_on="http")
    job = _run({"browser": "auto"})

    assert job["status"] == "done"
    assert calls["renders"] == 0
    assert job["summary"]["strategy"] == "http"


def test_source_is_captured_api_when_the_scraper_replays_a_rendered_endpoint() -> None:
    probe = {**SHELL_PROBE, "rendered": render.analyze(RENDERED_HTML, "https://x.test/", _captured())}
    scraper = 'API = "https://api.example.com/v2/search"\n'
    assert pipeline._guess_source(probe, [], "http", scraper) == "captured api"
    assert pipeline._guess_source(probe, [], "http", "nothing here") == "html"
    assert pipeline._guess_source(probe, [], "browser") == "headless browser"


def test_enable_javascript_is_reported_as_a_shell_not_as_anti_bot() -> None:
    lines = pipeline.describe_probe({**FAKE_PROBE, "block_markers": ["enable javascript"]})
    assert not any(isinstance(line, tuple) for line in lines)
    assert any("app shell" in line for line in lines if isinstance(line, str))


def test_validation_failures_on_a_server_rendered_page_do_not_start_a_browser(
    harness, monkeypatch
) -> None:
    calls = harness(FAKE_PROBE, succeed_on="never")

    async def invalid_build(**kwargs):
        calls["builds"].append({"strategy": "http"})
        files = offline_bundle()
        files["scraper.py"] = files["scraper.py"].replace("def fetch_page(", "def oops(", 1)
        return files, 0.001, []

    monkeypatch.setattr(pipeline.agent, "build_bundle", invalid_build)
    job = _run({"browser": "auto"})

    assert job["status"] == "failed"
    assert calls["renders"] == 0
    assert len(calls["builds"]) == 1
    assert "Not starting the browser" in _messages(job["id"])
