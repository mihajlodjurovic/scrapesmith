"""Security regressions: SSRF via redirects, extra bundle files, server key reuse."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app import agent, discovery
from app.proxies import ProxyPool
from app.routes import api as api_routes
from app.sandbox import write_bundle
from app.validate import validate_bundle


@pytest.fixture
def guarded_network(monkeypatch):
    """Turn the SSRF guard on and route every httpx client through a fake web."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "internal.example.com":
            return httpx.Response(200, text="cloud metadata secrets")
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"Location": "http://internal.example.com/"})
        return httpx.Response(200, text="<html>ok</html>")

    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    monkeypatch.setattr(discovery, "ALLOW_PRIVATE_NETWORKS", False)
    monkeypatch.setattr(
        discovery, "_is_public_host", lambda host: host != "internal.example.com"
    )
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs)
    )


def test_probe_fetch_refuses_redirect_to_private_host(guarded_network) -> None:
    with pytest.raises(discovery.DiscoveryError, match="SSRF"):
        asyncio.run(discovery._fetch("https://example.com/redirect", ProxyPool([])))


def test_probe_fetch_allows_public_pages(guarded_network) -> None:
    response, _ = asyncio.run(discovery._fetch("https://example.com/list", ProxyPool([])))
    assert response.status_code == 200


def test_agent_fetch_refuses_private_subdomain(guarded_network) -> None:
    tool = agent.FetchTool("https://example.com/list")
    observation = asyncio.run(tool.fetch("https://internal.example.com/"))
    assert "SSRF" in observation.get("error", "")
    assert "secrets" not in str(observation)


def test_agent_fetch_refuses_redirect_to_private_host(guarded_network) -> None:
    tool = agent.FetchTool("https://example.com/list")
    observation = asyncio.run(tool.fetch("https://example.com/redirect"))
    assert "SSRF" in observation.get("error", "")
    assert "secrets" not in str(observation)


def test_validator_rejects_extra_modules() -> None:
    errors = validate_bundle(
        {
            "scraper.py": "x = 1\n",
            "mapper.py": "x = 1\n",
            "sitecustomize.py": "import subprocess\n",
        }
    )
    assert any("unexpected files sitecustomize.py" in error for error in errors)


def test_write_bundle_never_writes_extra_modules(tmp_path) -> None:
    write_bundle(
        tmp_path,
        {"scraper.py": "x = 1\n", "helper.py": "import os\nos.system('id')\n"},
        [],
    )
    assert (tmp_path / "scraper.py").exists()
    assert not (tmp_path / "helper.py").exists()


def _payload(base_url: str) -> dict:
    return {
        "url": "example.com/list",
        "api_key": "",
        "provider": "custom",
        "base_url": base_url,
        "model": "some-model",
    }


def _noop_pipeline(monkeypatch) -> None:
    async def _run(job_id: str, api_key: str | None) -> None:
        return None

    monkeypatch.setattr(api_routes, "run_job", _run)


def test_server_key_is_not_sent_to_a_client_chosen_endpoint(
    client: TestClient, monkeypatch
) -> None:
    _noop_pipeline(monkeypatch)
    monkeypatch.setattr(api_routes, "ENV_API_KEY", "server-key")
    response = client.post("/api/jobs", json=_payload("https://attacker.example/v1"))
    assert response.status_code == 400
    assert "custom base URL" in response.json()["detail"]


def test_server_key_still_works_with_a_preset_endpoint(
    client: TestClient, monkeypatch
) -> None:
    _noop_pipeline(monkeypatch)
    monkeypatch.setattr(api_routes, "ENV_API_KEY", "server-key")
    response = client.post("/api/jobs", json=_payload("https://api.openai.com/v1"))
    assert response.status_code == 201, response.text
