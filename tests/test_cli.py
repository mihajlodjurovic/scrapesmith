"""Agent-facing CLI helpers: build result shape, validation, scaffolding."""

from __future__ import annotations

import asyncio
import json

from app import cli, db


def test_scaffold_then_validate_clean(tmp_path) -> None:
    directory = tmp_path / "bundle"
    files = cli.scaffold_bundle(directory)
    assert {"scraper.py", "mapper.py", "runner.sh", "README.md", "proxies.json"} <= set(files)
    assert cli.validate_bundle_dir(directory) == []


def test_validate_reports_missing_files(tmp_path) -> None:
    (tmp_path / "scraper.py").write_text("print('hi')\n", encoding="utf-8")
    errors = cli.validate_bundle_dir(tmp_path)
    assert any("mapper.py: missing" in error for error in errors)
    assert any("runner.sh: missing" in error for error in errors)


def test_build_returns_a_machine_result(tmp_path, monkeypatch) -> None:
    async def fake_run(job_id: str, api_key: str | None) -> None:
        directory = db.job_dir(job_id)
        (directory / "scraper.py").write_text("print('scraper')\n", encoding="utf-8")
        (directory / "agent_trace.json").write_text(
            json.dumps(
                [
                    {
                        "url": "https://example.com/list.rss",
                        "observation": {
                            "status": 200,
                            "content_type": "application/atom+xml",
                        },
                    }
                ]
            ),
            encoding="utf-8",
        )
        db.update_job(
            job_id,
            status="done",
            summary={"seconds": 1.2, "source": "feed"},
            preview={
                "raw_count": 2,
                "mapped_count": 2,
                "fill_rates": {"title": 100.0},
                "rows": [{"title": "Example"}],
            },
        )

    monkeypatch.setattr(cli, "run_job", fake_run)
    target = tmp_path / "out"

    result = asyncio.run(
        cli.build(
            "https://example.com/list",
            proxies=["1.2.3.4:8000:user:pass"],
            output_dir=target,
        )
    )

    assert result["status"] == "done"
    assert result["records"] == {"raw": 2, "mapped": 2}
    assert result["source"] == "feed"
    assert result["agent_trace"][0]["status"] == 200
    assert "proxies.json" not in result["copied_files"]
    assert (target / "scraper.py").exists()

    result_file = db.job_dir(result["job_id"]) / "result.json"
    stored = json.loads(result_file.read_text(encoding="utf-8"))
    assert stored["job_id"] == result["job_id"]
    assert stored["status"] == "done"
