"""Browser discovery without a browser: shell detection, network analysis, prompts."""

from __future__ import annotations

import json
from pathlib import Path

from app import agent, render
from app.prompt import system_prompt, user_prompt
from app.validate import validate_bundle

REFERENCE = Path(__file__).resolve().parents[1] / "app" / "reference"

ITEMS = [{"id": n, "name": f"Item {n}", "price": n * 10} for n in range(1, 9)]


def _captured() -> list[dict]:
    return [
        {
            "url": "https://www.google-analytics.com/g/collect?v=2",
            "method": "POST",
            "status": 204,
            "content_type": "application/json",
            "body": json.dumps({"items": ITEMS}),
        },
        {
            "url": "https://shop.example.com/page-fragment",
            "method": "GET",
            "status": 200,
            "content_type": "text/html",
            "body": "<div>not json</div>",
        },
        {
            "url": "https://api.example.com/v2/search?page=1",
            "method": "POST",
            "status": 200,
            "content_type": "application/json; charset=utf-8",
            "body": json.dumps({"meta": {"total": 99}, "data": {"results": ITEMS}}),
            "post_data": '{"query": "mugs", "page": 1}',
            "request_headers": {
                "Accept": "application/json",
                "Cookie": "session=secret-cookie-value",
                "Authorization": "Bearer secret-token-value",
                "Content-Type": "application/json",
            },
        },
        {
            "url": "https://shop.example.com/api/config",
            "method": "GET",
            "status": 200,
            "content_type": "application/json",
            "body": json.dumps({"locale": "en", "flags": [1, 2, 3]}),
        },
    ]


RENDERED_HTML = (
    "<html><head><title>Mugs</title></head><body><section class='grid'>"
    + "".join(
        f"<article class='card'><a href='/p/{n}'>Handmade ceramic mug {n}</a></article>"
        for n in range(1, 9)
    )
    + "</section></body></html>"
)


def test_shell_detection() -> None:
    shell = {"skeleton": "<html><body><div id=\"root\"></div></body></html>"}
    assert render.looks_like_shell(shell) is True
    assert render.looks_like_shell({**shell, "repeat_blocks": [{"selector": "li"}]}) is False
    assert render.looks_like_shell({**shell, "embedded_state": [{"kind": "x"}]}) is False
    long_text = "<html><body>" + "<p>" + "word " * 2000 + "</p></body></html>"
    assert render.looks_like_shell({"skeleton": long_text}) is False
    assert render.looks_like_shell(
        {"skeleton": long_text, "block_markers": ["enable javascript"]}
    ) is True


def test_find_item_list_returns_the_biggest_list_and_its_path() -> None:
    data = {"meta": {"tags": [{"a": 1}]}, "data": {"results": ITEMS}}
    path, items = render.find_item_list(data)
    assert path == "data.results"
    assert len(items) == 8
    assert render.find_item_list([1, 2, 3]) == ("", [])


def test_analyze_ranks_api_responses_and_hides_credentials() -> None:
    report = render.analyze(RENDERED_HTML, "https://shop.example.com/", _captured())
    network = report["network"]
    assert [entry["url"] for entry in network] == [
        "https://api.example.com/v2/search?page=1",
        "https://shop.example.com/api/config",
    ]
    best = network[0]
    assert best["method"] == "POST"
    assert best["item_count"] == 8
    assert best["list_path"] == "data.results"
    assert best["item_keys"] == ["id", "name", "price"]
    assert best["post_data"] == '{"query": "mugs", "page": 1}'
    assert best["request_headers"] == {
        "accept": "application/json",
        "content-type": "application/json",
    }
    assert best["other_header_names"] == ["authorization", "cookie"]
    serialised = json.dumps(report)
    assert "secret-cookie-value" not in serialised
    assert "secret-token-value" not in serialised
    assert report["repeat_blocks"][0]["selector"] == "article.card"
    assert report["title"] == "Mugs"


def test_network_helpers() -> None:
    probe = {"rendered": render.analyze(RENDERED_HTML, "https://shop.example.com/", _captured())}
    assert render.has_api(probe) is True
    assert render.network_hosts(probe) == {"api.example.com"}
    assert render.has_api({}) is False
    lines = render.describe({**probe["rendered"], "seconds": 1.2, "proxy_used": "direct"})
    assert any("8 items at data.results" in line for line in lines)


def test_playwright_proxy_format() -> None:
    assert render.playwright_proxy(None) is None
    assert render.playwright_proxy("http://user:pa%40ss@1.2.3.4:8000") == {
        "server": "http://1.2.3.4:8000",
        "username": "user",
        "password": "pa%40ss",
    }
    assert render.playwright_proxy("http://1.2.3.4:8000") == {"server": "http://1.2.3.4:8000"}


def test_fetch_tool_may_verify_hosts_the_browser_saw() -> None:
    tool = agent.FetchTool("https://shop.example.com/", extra_hosts={"api.example.com"})
    assert tool.allowed("https://api.example.com/v2/search")
    assert tool.allowed("https://cdn.shop.example.com/x.json")
    assert not tool.allowed("https://other.example.org/")


def test_prompts_carry_the_render_and_the_browser_reference() -> None:
    probe = {
        "url": "https://shop.example.com/",
        "final_url": "https://shop.example.com/",
        "skeleton": "<div id='root'></div>",
        "rendered": render.analyze(RENDERED_HTML, "https://shop.example.com/", _captured()),
    }
    http_prompt = user_prompt(probe, {})
    assert "Rendered page (headless Chromium" in http_prompt
    assert "api.example.com/v2/search" in http_prompt
    assert "Reference scraper (keep this plumbing)" in http_prompt
    assert "Why plain HTTP failed" not in http_prompt

    browser_prompt = user_prompt(probe, {}, browser=True, failure=["scraper wrote no records"])
    assert "Reference browser scraper" in browser_prompt
    assert "sync_playwright" in browser_prompt
    assert "- scraper wrote no records" in browser_prompt

    assert "Browser fallback build" in system_prompt(browser=True)
    assert "Browser fallback build" not in system_prompt()


def _browser_bundle() -> dict[str, str]:
    return {
        "scraper.py": (REFERENCE / "scraper_browser.py").read_text(encoding="utf-8"),
        "mapper.py": (REFERENCE / "mapper.py").read_text(encoding="utf-8"),
        "runner.sh": (REFERENCE / "runner_browser.sh").read_text(encoding="utf-8"),
        "requirements.txt": (REFERENCE / "requirements_browser.txt").read_text(encoding="utf-8"),
        "README.md": "# Browser bundle\n\n" + "Renders the page in headless Chromium. " * 6,
    }


def test_reference_browser_bundle_passes_browser_validation() -> None:
    assert validate_bundle(_browser_bundle(), browser=True) == []


def test_playwright_is_rejected_outside_the_browser_build() -> None:
    errors = validate_bundle(_browser_bundle())
    assert any("only allowed in the browser fallback build" in error for error in errors)


def test_browser_build_needs_playwright_in_requirements() -> None:
    files = _browser_bundle()
    files["requirements.txt"] = "beautifulsoup4>=4.12\n"
    assert "requirements.txt: missing 'playwright'" in validate_bundle(files, browser=True)


def test_evasion_tooling_is_always_rejected() -> None:
    files = _browser_bundle()
    files["scraper.py"] = "from playwright_stealth import stealth_sync\n" + files["scraper.py"]
    errors = validate_bundle(files, browser=True)
    assert any("forbidden import 'from playwright_stealth'" in error for error in errors)


def test_cookie_consent_apis_are_noise() -> None:
    captured = [{"url": "https://app.termly.io/api/v1/snippets/websites/abc/cookies",
                 "content_type": "application/json", "body": json.dumps({"cookies": ITEMS})}]
    assert render.analyze(RENDERED_HTML, "https://shop.example.com/", captured)["network"] == []
