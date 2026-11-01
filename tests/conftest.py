"""Test bootstrap: isolated data dir and a clean database per test."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="scrapesmith-tests-"))
os.environ["SCRAPESMITH_DATA_DIR"] = str(_TMP)
os.environ["OPENCODE_API_KEY"] = ""
os.environ["SCRAPESMITH_ALLOW_PRIVATE"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import connection, init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db():
    init_db()
    with connection() as conn:
        conn.execute("DELETE FROM events")
        conn.execute("DELETE FROM jobs")
    yield


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
