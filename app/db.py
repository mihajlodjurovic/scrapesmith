"""SQLite job store. Jobs are rows; artifacts live on disk under data/jobs/<id>/."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .config import DATA_DIR, DB_PATH, JOBS_DIR

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    url           TEXT NOT NULL,
    status        TEXT NOT NULL,
    provider      TEXT NOT NULL,
    base_url      TEXT NOT NULL,
    model         TEXT NOT NULL,
    options       TEXT NOT NULL DEFAULT '{}',
    attempts      INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0,
    error         TEXT,
    probe         TEXT,
    files         TEXT NOT NULL DEFAULT '[]',
    preview       TEXT,
    summary       TEXT,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id  TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    ts      REAL NOT NULL,
    level   TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id, id);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
"""

TERMINAL_STATUSES = {"done", "failed"}


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connection() as conn:
        conn.executescript(SCHEMA)


def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    job = dict(row)
    for key in ("options", "probe", "files", "preview", "summary"):
        raw = job.get(key)
        job[key] = json.loads(raw) if raw else None
    return job


def create_job(
    url: str,
    provider: str,
    base_url: str,
    model: str,
    options: dict[str, Any],
) -> dict[str, Any]:
    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, url, status, provider, base_url, model, options,
                              created_at, updated_at)
            VALUES (?, ?, 'queued', ?, ?, ?, ?, ?, ?)
            """,
            (job_id, url, provider, base_url, model, json.dumps(options), now, now),
        )
        conn.execute(
            "INSERT INTO events (job_id, ts, level, message) VALUES (?, ?, ?, ?)",
            (job_id, now, "info", "Job queued."),
        )
    job = get_job(job_id)
    assert job is not None
    return job


def get_job(job_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def list_jobs(limit: int = 20) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_job(row) for row in rows]


def update_job(job_id: str, **fields: Any) -> None:
    allowed = {
        "status",
        "attempts",
        "cost_usd",
        "error",
        "probe",
        "files",
        "preview",
        "summary",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    encoded = {
        key: json.dumps(value) if key in {"probe", "files", "preview", "summary"} else value
        for key, value in updates.items()
    }
    assignments = ", ".join(f"{key} = ?" for key in encoded)
    with connection() as conn:
        conn.execute(
            f"UPDATE jobs SET {assignments}, updated_at = ? WHERE id = ?",
            (*encoded.values(), time.time(), job_id),
        )


def add_event(job_id: str, message: str, level: str = "info") -> None:
    with connection() as conn:
        conn.execute(
            "INSERT INTO events (job_id, ts, level, message) VALUES (?, ?, ?, ?)",
            (job_id, time.time(), level, message),
        )


def events_since(job_id: str, last_id: int = 0, limit: int = 500) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT id, ts, level, message FROM events WHERE job_id = ? AND id > ? "
            "ORDER BY id LIMIT ?",
            (job_id, last_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def job_dir(job_id: str):
    path = JOBS_DIR / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path
