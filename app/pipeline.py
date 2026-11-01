"""Job orchestration: discover -> generate -> validate -> run -> repair."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from . import agent, db, discovery, generator, llm, validate
from .config import (
    DEFAULT_MODE,
    ENV_API_KEY,
    MAX_REPAIR_ATTEMPTS,
    SANDBOX_MAX_PAGES,
)
from .proxies import ProxyPool, mask_proxy, parse_proxies
from .sandbox import run_bundle


def _key(job_id: str, api_key: str | None) -> str:
    return (api_key or ENV_API_KEY or "").strip()


def _redact(text: str, secret: str) -> str:
    if secret and len(secret) > 8:
        return text.replace(secret, "***")
    return text


class Log:
    """Writes to the events table and echoes to the server log."""

    def __init__(self, job_id: str, secret: str) -> None:
        self.job_id = job_id
        self.secret = secret

    def __call__(self, message: str, level: str = "info") -> None:
        message = _redact(message, self.secret)
        db.add_event(self.job_id, message, level)
        print(f"[{self.job_id}] {level}: {message}")


def describe_probe(probe: dict[str, Any]) -> list[str]:
    lines = [
        f"GET {probe['status']} · {probe.get('content_type') or 'unknown type'} · "
        f"{probe['fetched_bytes'] / 1024:.0f} KB · via {probe['proxy_used']}",
        f"robots.txt: found={probe['robots']['found']} "
        f"allowed={probe['robots']['allowed']} "
        f"crawl_delay={probe['robots']['crawl_delay']}",
    ]
    candidates = probe.get("api_candidates") or []
    same_origin = sum(1 for item in candidates if item.get("same_origin"))
    lines.append(f"API candidates: {len(candidates)} ({same_origin} same-origin)")
    state = [item["kind"] for item in probe.get("embedded_state") or []]
    if state:
        lines.append(f"Embedded state: {', '.join(state)}")
    blocks = probe.get("repeat_blocks") or []
    if blocks:
        best = blocks[0]
        lines.append(
            f"Repeated blocks: {len(blocks)} (best {best['selector']} × {best['count']})"
        )
    ld = probe.get("json_ld") or []
    if ld:
        lines.append("JSON-LD: " + ", ".join(str(item.get("type")) for item in ld[:5]))
    if probe.get("block_markers"):
        lines.append(
            (
                "Warning: page shows anti-bot markers: "
                + ", ".join(probe["block_markers"]),
                "warn",
            )
        )
    return lines


def _usage_cost(usage: dict[str, Any]) -> float:
    return llm.usage_cost(usage)


def _file_index(job_dir: Path) -> list[dict[str, Any]]:
    names = ["scraper.py", "mapper.py", "runner.sh", "README.md", "probe.json", "agent_trace.json"]
    index = []
    for name in names:
        path = job_dir / name
        if path.exists():
            index.append({"name": name, "size": path.stat().st_size})
    for name in ("raw.jsonl", "mapped.jsonl"):
        path = job_dir / name
        if path.exists():
            index.append({"name": name, "size": path.stat().st_size})
    return index


async def run_job(job_id: str, api_key: str | None) -> None:
    job = db.get_job(job_id)
    if job is None:
        return

    secret = _key(job_id, api_key)
    log = Log(job_id, secret)
    job_dir = db.job_dir(job_id)
    options = job.get("options") or {}
    proxies = parse_proxies(options.get("proxies"))
    pool = ProxyPool(proxies)
    mode = str(options.get("mode") or DEFAULT_MODE).lower()
    if mode not in {"polite", "unrestricted"}:
        mode = "polite"
    started = time.perf_counter()
    cost = 0.0
    attempts = 0
    probe_seconds = 0.0
    generate_seconds = 0.0

    try:
        if mode == "unrestricted":
            log("Mode: not polite — robots.txt and terms are ignored by explicit choice.", "warn")
        else:
            log("Mode: polite — robots.txt and terms are respected.")
        if proxies:
            log(f"Proxy pool: {len(proxies)} entries (first: {mask_proxy(proxies[0])})")
        else:
            log("No proxies provided — requests will go direct.", "warn")

        db.update_job(job_id, status="discovering")
        log("Step 1/3 · discovery", "step")
        probe_started = time.perf_counter()
        probe = await discovery.probe(job["url"], pool)
        probe_seconds = round(time.perf_counter() - probe_started, 1)
        (job_dir / "probe.json").write_text(
            json.dumps(probe, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        db.update_job(job_id, probe=probe)
        for line in describe_probe(probe):
            if isinstance(line, tuple):
                log(line[0], line[1])
            else:
                log(line)

        stats = probe.get("proxy_stats") or {}
        if stats.get("total"):
            log(
                f"Proxy health: {stats['available']}/{stats['total']} available · "
                f"{stats['cooling']} cooling · {stats['blacklisted']} blacklisted · "
                f"{stats['requests']} requests / {stats['failures']} failures"
            )
        log(f"Discovery finished in {probe_seconds}s")

        robots = probe.get("robots") or {}
        disallowed = bool(robots.get("found")) and not robots.get("allowed", True)
        if disallowed and mode == "polite":
            raise discovery.DiscoveryError(
                "robots.txt disallows this path and the build is in polite mode, so it "
                "stopped before spending model tokens. Switch to 'not polite' only if "
                "you have permission for this site."
            )
        if disallowed:
            log(
                "robots.txt disallows this path; continuing because mode is 'not polite'.",
                "warn",
            )

        db.update_job(job_id, status="generating")
        log("Step 2/3 · generation", "step")
        generate_started = time.perf_counter()
        bundle: dict[str, str] = {}
        usage: dict[str, Any] = {}
        repair_errors: list[str] = []
        trace: list[dict[str, Any]] = []
        max_rounds = MAX_REPAIR_ATTEMPTS + 1

        for round_number in range(1, max_rounds + 1):
            attempts = round_number
            db.update_job(job_id, attempts=attempts)
            round_cost = 0.0
            if round_number == 1:
                log(f"Calling {job['model']}")
                bundle, round_cost, trace = await agent.build_bundle(
                    probe=probe,
                    options=options,
                    base_url=job["base_url"],
                    api_key=secret,
                    model=job["model"],
                    proxies=proxies,
                    pool=pool,
                )
                (job_dir / "agent_trace.json").write_text(
                    json.dumps(trace, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                for entry in trace:
                    observation = entry.get("observation") or {}
                    if observation.get("error"):
                        log(
                            f"Tool fetch {entry.get('url')} -> {observation['error']}",
                            "warn",
                        )
                    else:
                        content_type = (observation.get("content_type") or "").split(";")[0]
                        log(
                            f"Tool fetch {observation.get('requested_url')} -> "
                            f"{observation.get('status')} {content_type} "
                            f"({observation.get('bytes')} B)",
                            "llm",
                        )
                if trace:
                    log(f"Agent verified {len(trace)} source(s) before writing code.")
            else:
                log(f"Repair round {round_number - 1}/{MAX_REPAIR_ATTEMPTS}", "step")
                bundle, usage = await generator.repair(
                    files=bundle,
                    errors=repair_errors,
                    base_url=job["base_url"],
                    api_key=secret,
                    model=job["model"],
                )
                round_cost = _usage_cost(usage)

            cost += round_cost
            db.update_job(job_id, cost_usd=round(cost, 6))
            log(
                f"Model returned {len(bundle)} files "
                f"({sum(len(v) for v in bundle.values()) / 1024:.1f} KB) "
                f"· cost ${round_cost:.4f}",
                "llm",
            )

            from .sandbox import write_bundle

            write_bundle(job_dir, bundle, proxies)
            db.update_job(job_id, files=_file_index(job_dir))

            errors = validate.validate_bundle(bundle)
            if errors:
                for error in errors:
                    log(error, "warn")
                if round_number < max_rounds:
                    log("Asking the model to fix validation errors…")
                    repair_errors = errors
                    continue
                raise generator.GenerationError(
                    "Bundle still fails validation: " + "; ".join(errors[:5])
                )

            db.update_job(job_id, status="testing")
            log("Step 3/3 · sandbox run", "step")
            result = await asyncio.to_thread(
                run_bundle,
                job_dir,
                bundle,
                proxies,
                SANDBOX_MAX_PAGES,
            )
            if result.get("stdout"):
                log("scraper output:\n" + result["stdout"][-1500:], "code")
            if result.get("stderr"):
                log("stderr:\n" + result["stderr"][-1500:], "code")

            if result["ok"]:
                generate_seconds = round(time.perf_counter() - generate_started, 1)
                db.update_job(
                    job_id,
                    status="done",
                    error=None,
                    files=_file_index(job_dir),
                    preview={
                        "rows": result["preview"],
                        "fill_rates": result["fill_rates"],
                        "raw_count": result["raw_count"],
                        "mapped_count": result["mapped_count"],
                    },
                    summary={
                        "site": probe.get("final_url"),
                        "source": _guess_source(probe, trace),
                        "raw_count": result["raw_count"],
                        "mapped_count": result["mapped_count"],
                        "seconds": round(time.perf_counter() - started, 1),
                        "probe_seconds": probe_seconds,
                        "generate_seconds": generate_seconds,
                        "sandbox_seconds": result["duration_seconds"],
                        "cost_usd": round(cost, 6),
                        "attempts": attempts,
                        "model": job["model"],
                        "mode": mode,
                    },
                )
                log(
                    f"Done: {result['mapped_count']} mapped records from "
                    f"{result['raw_count']} raw items in {result['duration_seconds']}s",
                    "step",
                )
                return

            for error in result["errors"]:
                log(error, "error")
            if round_number < max_rounds:
                log("Asking the model to fix the runtime failure…", "warn")
                repair_errors = result["errors"]
                continue
            raise generator.GenerationError(
                "Sandbox run failed after repairs: " + "; ".join(result["errors"])[:800]
            )

    except Exception as exc:  # noqa: BLE001 - surfaced to the job row
        message = _redact(str(exc), secret)
        log(f"Job failed: {message}", "error")
        db.update_job(
            job_id,
            status="failed",
            error=message[:2000],
            files=_file_index(job_dir),
            summary={
                "seconds": round(time.perf_counter() - started, 1),
                "cost_usd": round(cost, 6),
                "attempts": attempts,
                "model": job["model"],
            },
        )


def _guess_source(probe: dict[str, Any], trace: list[dict[str, Any]] | None = None) -> str:
    urls = [str(entry.get("url", "")) for entry in (trace or [])]
    if any(".rss" in url or "/feed" in url for url in urls):
        return "feed"
    if any(".json" in url for url in urls):
        return "json api"
    if probe.get("embedded_state"):
        return "embedded state"
    if probe.get("json_ld"):
        return "json-ld"
    if probe.get("api_candidates"):
        return "api candidates"
    return "html"
