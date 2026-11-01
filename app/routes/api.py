"""JSON API: create jobs, stream logs, read generated files, download the bundle."""

from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from .. import db
from ..config import ENV_API_KEY, PROVIDER_PRESETS
from ..pipeline import run_job

router = APIRouter(prefix="/api", tags=["jobs"])

READABLE_FILES = {
    "scraper.py",
    "mapper.py",
    "runner.sh",
    "README.md",
    "NOTES.md",
    "probe.json",
    "raw.jsonl",
    "mapped.jsonl",
}
DOWNLOAD_EXCLUDE = {"proxies.json", "__pycache__"}
MAX_ACTIVE_JOBS = 3
_tasks: set[asyncio.Task] = set()


class JobCreate(BaseModel):
    url: str = Field(min_length=4, max_length=2000)
    api_key: str | None = Field(default=None, max_length=400)
    provider: str = "opencode"
    base_url: str = ""
    model: str = ""
    proxies: str | list | None = None
    fields: str = Field(default="", max_length=500)
    instructions: str = Field(default="", max_length=2000)

    @field_validator("url")
    @classmethod
    def _clean_url(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith(("http://", "https://")):
            value = f"https://{value}"
        return value


def _active_jobs() -> int:
    return sum(1 for job in db.list_jobs(50) if job["status"] not in db.TERMINAL_STATUSES)


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    probe = job.get("probe") or {}
    return {
        "id": job["id"],
        "url": job["url"],
        "status": job["status"],
        "provider": job["provider"],
        "base_url": job["base_url"],
        "model": job["model"],
        "attempts": job["attempts"],
        "cost_usd": job["cost_usd"],
        "error": job["error"],
        "files": job["files"] or [],
        "preview": job["preview"],
        "summary": job["summary"],
        "created_at": job["created_at"],
        "probe": {
            "status": probe.get("status"),
            "title": probe.get("title"),
            "content_type": probe.get("content_type"),
            "api_candidates": len(probe.get("api_candidates") or []),
            "embedded_state": [
                item.get("kind") for item in probe.get("embedded_state") or []
            ],
            "repeat_blocks": len(probe.get("repeat_blocks") or []),
            "robots": probe.get("robots"),
            "block_markers": probe.get("block_markers"),
            "proxy_used": probe.get("proxy_used"),
        }
        if probe
        else None,
    }


@router.get("/providers")
def providers() -> dict[str, Any]:
    return {
        "presets": PROVIDER_PRESETS,
        "has_env_key": bool(ENV_API_KEY),
        "defaults": {
            "provider": "opencode",
            "base_url": PROVIDER_PRESETS["opencode"]["base_url"],
            "model": "minimax-m3",
        },
    }


@router.post("/jobs", status_code=201)
async def create(request: Request, payload: JobCreate) -> dict[str, Any]:
    if payload.provider not in PROVIDER_PRESETS:
        raise HTTPException(status_code=400, detail="Unknown provider.")
    base_url = (payload.base_url or PROVIDER_PRESETS[payload.provider]["base_url"]).rstrip("/")
    model = payload.model or (
        PROVIDER_PRESETS[payload.provider]["models"][0]
        if PROVIDER_PRESETS[payload.provider]["models"]
        else ""
    )
    if not model:
        raise HTTPException(status_code=400, detail="Model is required.")
    if not base_url:
        raise HTTPException(status_code=400, detail="Base URL is required.")
    if not (payload.api_key or ENV_API_KEY):
        raise HTTPException(
            status_code=400,
            detail="No API key: put OPENCODE_API_KEY in .env or paste a key.",
        )
    if _active_jobs() >= MAX_ACTIVE_JOBS:
        raise HTTPException(status_code=429, detail="Too many jobs running. Wait a bit.")

    job = db.create_job(
        payload.url,
        payload.provider,
        base_url,
        model,
        {
            "proxies": payload.proxies,
            "fields": payload.fields,
            "instructions": payload.instructions,
        },
    )
    task = asyncio.create_task(run_job(job["id"], payload.api_key))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return _public_job(job)


@router.get("/jobs")
def list_jobs(limit: int = 20) -> dict[str, Any]:
    return {
        "jobs": [_public_job(job) for job in db.list_jobs(max(1, min(limit, 100)))],
        "active": _active_jobs(),
    }


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return _public_job(job)


@router.get("/jobs/{job_id}/events")
async def events(job_id: str) -> StreamingResponse:
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    async def stream():
        last_id = 0
        while True:
            rows = db.events_since(job_id, last_id)
            for row in rows:
                last_id = row["id"]
                yield f"id: {last_id}\ndata: {json.dumps(row)}\n\n"
            current = db.get_job(job_id)
            if current is None:
                break
            if current["status"] in db.TERMINAL_STATUSES and not rows:
                yield "event: end\ndata: {}\n\n"
                break
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/jobs/{job_id}/files/{name}")
def read_file(job_id: str, name: str) -> Response:
    if name not in READABLE_FILES:
        raise HTTPException(status_code=400, detail="That file is not readable.")
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    path = db.job_dir(job_id) / name
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not generated yet.")
    return Response(
        content=path.read_text(encoding="utf-8", errors="replace"),
        media_type="text/plain; charset=utf-8",
    )


@router.get("/jobs/{job_id}/download")
def download(job_id: str) -> Response:
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    directory = db.job_dir(job_id)
    buffer = io.BytesIO()
    written = 0
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(Path(directory).iterdir()):
            if path.is_dir() or path.name in DOWNLOAD_EXCLUDE:
                continue
            archive.write(path, arcname=f"scrapesmith-{job_id}/{path.name}")
            written += 1
    if not written:
        raise HTTPException(status_code=409, detail="Nothing to download yet.")
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="scrapesmith-{job_id}.zip"'
        },
    )
