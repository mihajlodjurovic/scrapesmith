"""Proxy list parsing covers every shape we accept."""

from app.proxies import ProxyPool, mask_proxy, parse_proxies


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


def test_pool_prefers_least_failing_proxy() -> None:
    pool = ProxyPool(["http://a:1", "http://b:2"])
    pool.report("http://a:1", False)
    assert pool.pick() == "http://b:2"


def test_pool_cools_down_after_a_block_status() -> None:
    pool = ProxyPool(["http://a:1"], cooldown_seconds=60)
    pool.report("http://a:1", False, 403)
    assert pool.pick() is None
    assert pool.stats()["cooling"] == 1
    assert pool.attempts(1)[-1] is None


def test_pool_blacklists_after_consecutive_failures() -> None:
    pool = ProxyPool(["http://a:1"], blacklist_after=3, blacklist_seconds=60)
    for _ in range(3):
        pool.report("http://a:1", False, 500)
    assert pool.stats()["blacklisted"] == 1
    assert pool.pick() is None


def test_success_resets_the_consecutive_counter() -> None:
    pool = ProxyPool(["http://a:1"], blacklist_after=2)
    pool.report("http://a:1", False, 500)
    pool.report("http://a:1", True)
    pool.report("http://a:1", False, 500)
    assert pool.stats()["blacklisted"] == 0


def test_attempts_always_end_with_direct() -> None:
    pool = ProxyPool(["http://a:1", "http://b:2", "http://c:3"])
    assert pool.attempts(2)[-1] is None
    assert len(pool.attempts(2)) <= 3
