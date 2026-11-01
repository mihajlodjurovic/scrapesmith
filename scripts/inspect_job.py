"""Inspect one job from the CLI: status, events, preview, agent trace.

Usage:
    python scripts/inspect_job.py <job_id>
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "scrapesmith.db"
JOBS = ROOT / "data" / "jobs"


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: inspect_job.py <job_id>")
        return 1
    job_id = sys.argv[1]
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        print(f"job {job_id} not found")
        return 1
    job = dict(row)
    summary = json.loads(job["summary"]) if job["summary"] else {}
    preview = json.loads(job["preview"]) if job["preview"] else {}
    options = json.loads(job["options"]) if job["options"] else {}

    print(f"job      : {job_id}")
    print(f"url      : {job['url']}")
    print(f"status   : {job['status']}")
    print(f"model    : {job['model']} via {job['provider']}")
    print(f"mode     : {summary.get('mode', options.get('mode', 'polite'))}")
    print(f"cost     : ${job['cost_usd']:.4f}")
    print(f"attempts : {job['attempts']}")
    print(f"seconds  : {summary.get('seconds')}")
    print(
        "stages   : "
        f"probe {summary.get('probe_seconds')}s · "
        f"generate {summary.get('generate_seconds')}s · "
        f"sandbox {summary.get('sandbox_seconds')}s"
    )
    print(f"source   : {summary.get('source')}")
    if job["error"]:
        print(f"error    : {job['error']}")

    print("\n--- events ---")
    for event in conn.execute(
        "SELECT level, message FROM events WHERE job_id = ? ORDER BY id", (job_id,)
    ):
        print(f"[{event['level']}] {event['message']}")

    if preview:
        print("\n--- preview ---")
        print(f"raw={preview.get('raw_count')} mapped={preview.get('mapped_count')}")
        for name, pct in sorted((preview.get("fill_rates") or {}).items()):
            print(f"  {name:<22} {pct:>5}%")
        rows = preview.get("rows") or []
        if rows:
            print("\nfirst mapped record:")
            print(json.dumps(rows[0], indent=2, ensure_ascii=False)[:1600])

    trace_path = JOBS / job_id / "agent_trace.json"
    if trace_path.exists():
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        print("\n--- agent trace ---")
        for entry in trace:
            obs = entry.get("observation") or {}
            print(
                f"  {entry.get('url')} -> {obs.get('status')} "
                f"{str(obs.get('content_type', '')).split(';')[0]} "
                f"({obs.get('bytes')} B)"
            )

    print(f"\nfiles: {JOBS / job_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
