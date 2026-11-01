"""API surface: creation rules, job serialisation, file access."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.routes import api as api_routes

PAYLOAD = {
    "url": "example.com/list",
    "api_key": "test-key-not-a-real-secret",
    "provider": "opencode",
    "base_url": "https://opencode.ai/inference/openai/v1",
    "model": "minimax-m3",
    "proxies": ["1.2.3.4:8000:user:pass"],
    "fields": "title, price",
}


def noop_pipeline(monkeypatch) -> None:
    async def _run(job_id: str, api_key: str | None) -> None:
        return None

    monkeypatch.setattr(api_routes, "run_job", _run)


def test_providers_endpoint(client: TestClient) -> None:
    response = client.get("/api/providers")
    assert response.status_code == 200
    data = response.json()
    assert "opencode" in data["presets"]
    assert data["has_env_key"] is False


def test_create_job_does_not_store_the_key(client: TestClient, monkeypatch) -> None:
    noop_pipeline(monkeypatch)
    response = client.post("/api/jobs", json=PAYLOAD)
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["url"] == "https://example.com/list"
    assert job["model"] == "minimax-m3"
    assert job["status"] == "queued"
    assert "test-key-not-a-real-secret" not in response.text

    fetched = client.get(f"/api/jobs/{job['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == job["id"]


def test_job_requires_an_api_key(client: TestClient) -> None:
    payload = {**PAYLOAD, "api_key": ""}
    response = client.post("/api/jobs", json=payload)
    assert response.status_code == 400
    assert "API key" in response.json()["detail"]


def test_unknown_provider_is_rejected(client: TestClient) -> None:
    response = client.post("/api/jobs", json={**PAYLOAD, "provider": "not-a-provider"})
    assert response.status_code == 400


def test_missing_job_returns_404(client: TestClient) -> None:
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/jobs/nope/download").status_code == 404


def test_non_whitelisted_file_is_rejected(client: TestClient, monkeypatch) -> None:
    noop_pipeline(monkeypatch)
    job = client.post("/api/jobs", json=PAYLOAD).json()
    response = client.get(f"/api/jobs/{job['id']}/files/proxies.json")
    assert response.status_code == 400


def test_download_before_generation_is_409(client: TestClient, monkeypatch) -> None:
    noop_pipeline(monkeypatch)
    job = client.post("/api/jobs", json=PAYLOAD).json()
    assert client.get(f"/api/jobs/{job['id']}/download").status_code == 409


def test_health(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"
