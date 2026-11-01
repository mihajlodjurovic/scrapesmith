"""Discovery heuristics on local HTML — no network in tests."""

from bs4 import BeautifulSoup

from app.discovery import (
    _api_candidates,
    _embedded_state,
    _repeat_blocks,
    _skeleton,
    html_signals,
)

CARD_HTML = """
<html><head><title>Used cars</title></head><body>
<script id="__NEXT_DATA__" type="application/json">{"props": {}}</script>
<div class="grid">
  {cards}
</div>
<script>const endpoint = "/api/search?page=2";</script>
</body></html>
"""

CARD = """
<div class="card">
  <a href="/car/{i}">Car number {i}</a>
  <span class="price">EUR {price}</span>
</div>
"""


def sample_html(count: int = 8) -> str:
    cards = "".join(CARD.format(i=i, price=1000 + i) for i in range(count))
    return CARD_HTML.replace("{cards}", cards)


def test_repeated_blocks_finds_the_card_list() -> None:
    soup = BeautifulSoup(sample_html(), "html.parser")
    blocks = _repeat_blocks(soup)
    assert blocks
    assert blocks[0]["selector"] == "div.card"
    assert blocks[0]["count"] == 8
    assert "Car number" in blocks[0]["sample"][0]


def test_api_candidate_detection() -> None:
    html = sample_html()
    candidates = _api_candidates(html, "https://example.com/list")
    assert any("/api/search?page=2" in item["url"] for item in candidates)
    assert all(item["same_origin"] for item in candidates)


def test_embedded_state_detection() -> None:
    soup = BeautifulSoup(sample_html(), "html.parser")
    kinds = [item["kind"] for item in _embedded_state(sample_html(), soup)]
    assert "__NEXT_DATA__" in kinds


def test_skeleton_removes_scripts_and_keeps_structure() -> None:
    soup = BeautifulSoup(sample_html(), "html.parser")
    skeleton = _skeleton(soup)
    assert "<script" not in skeleton
    assert "Car number 3" in skeleton
    assert "div" in skeleton


def test_html_signals_find_embedded_payloads() -> None:
    html = (
        "<html><body><script>var ytInitialPlayerResponse = {\"videoDetails\": {}};</script>"
        "<script id='__NEXT_DATA__' type='application/json'>{}</script></body></html>"
    )
    signals = html_signals(html)
    kinds = [signal["kind"] for signal in signals]
    assert "YouTube player response" in kinds
    assert "Next.js data" in kinds
    assert all(signal["snippet"] for signal in signals)


def test_embedded_state_recognises_youtube_blobs() -> None:
    html = sample_html() + "<script>var ytInitialPlayerResponse = {};</script>"
    soup = BeautifulSoup(html, "html.parser")
    kinds = [item["kind"] for item in _embedded_state(html, soup)]
    assert "YouTube player response" in kinds
