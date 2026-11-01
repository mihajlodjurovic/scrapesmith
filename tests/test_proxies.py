"""Proxy list parsing covers every shape we accept."""

from app.proxies import mask_proxy, parse_proxies


def test_json_url_list() -> None:
    raw = '["http://user:pass@1.2.3.4:8000", "https://5.6.7.8:9000"]'
    assert parse_proxies(raw) == [
        "http://user:pass@1.2.3.4:8000",
        "https://5.6.7.8:9000",
    ]


def test_json_object_list() -> None:
    raw = '[{"server": "http://1.2.3.4:8000", "username": "u", "password": "p"}]'
    assert parse_proxies(raw) == ["http://u:p@1.2.3.4:8000"]


def test_host_port_user_pass_lines() -> None:
    raw = "1.2.3.4:8000:user:secret\n5.6.7.8:9000"
    assert parse_proxies(raw) == [
        "http://user:secret@1.2.3.4:8000",
        "http://5.6.7.8:9000",
    ]


def test_duplicates_are_removed() -> None:
    assert parse_proxies(["1.2.3.4:8000", "1.2.3.4:8000"]) == ["http://1.2.3.4:8000"]


def test_empty_inputs() -> None:
    assert parse_proxies(None) == []
    assert parse_proxies("") == []
    assert parse_proxies("[]") == []


def test_credentials_are_masked() -> None:
    assert mask_proxy("http://user:pass@1.2.3.4:8000") == "http://***@1.2.3.4:8000"
    assert mask_proxy("http://1.2.3.4:8000") == "http://1.2.3.4:8000"
