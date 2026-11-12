"""Job orchestration: discover -> generate -> validate -> run -> repair.

A build runs in up to three strategies, each with its own repair rounds:

1. ``http``    plain requests bundle from the HTTP probe (plus the rendered page
               when the probe looked like a JavaScript shell);
2. ``api``     plain requests bundle that replays a JSON endpoint a headless
               browser saw the page call (only when that render is new);
3. ``browser`` Playwright bundle, the last resort for client-side rendered pages.

Strategies 2 and 3 need the optional Playwright install and the browser setting
(``auto``/``always``); with ``off`` the build behaves exactly as before.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import agent, db, discovery, generator, llm, render, validate
from .config import (
    BROWSER_MODE,
    BROWSER_MODES,
    BROWSER_SANDBOX_TIMEOUT_SECONDS,
    DEFAULT_MODE,
    ENV_API_KEY,
    MAX_REPAIR_ATTEMPTS,
    PROXY_CHECK_CONCURRENCY,
    PROXY_CHECK_LIMIT,
    PROXY_CHECK_TIMEOUT_SECONDS,
    SANDBOX_MAX_PAGES,
    SANDBOX_TIMEOUT_SECONDS,
)
from .proxies import ProxyPool, mask_proxy, parse_proxies
from .sandbox import run_bundle

STRATEGY_LABELS = {
    "http": "plain HTTP",
    "api": "captured API replay",
    "browser": "headless browser",
}


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
        line = f"[{self.job_id}] {level}: {message}"
        try:
            print(line)
        except UnicodeEncodeError:
            # Windows consoles default to cp1252; a log line must never fail a job.
            encoding = getattr(sys.stdout, "encoding", None) or "ascii"
            print(line.encode(encoding, "replace").decode(encoding, "replace"))


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
    markers = [m for m in probe.get("block_markers") or [] if m != "enable javascript"]
    if "enable javascript" in (probe.get("block_markers") or []):
        lines.append("Page asks for JavaScript: the server HTML is probably an app shell")
    if markers:
        lines.append(("Warning: page shows anti-bot markers: " + ", ".join(markers), "warn"))
    return lines


def _usage_cost(usage: dict[str, Any]) -> float:
    return llm.usage_cost(usage)


def _file_index(job_dir: Path) -> list[dict[str, Any]]:
    names = [
        "scraper.py",
        "mapper.py",
        "runner.sh",
        "README.md",
        "requirements.txt",
        "probe.json",
        "agent_trace.json",
    ]
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


def browser_setting(options: dict[str, Any]) -> str:
    value = str(options.get("browser") or BROWSER_MODE).strip().lower()
    return value if value in BROWSER_MODES else "auto"


class _Build:
    """Mutable state shared by the strategies of one job."""

    def __init__(self) -> None:
        self.cost = 0.0
        self.attempts = 0
        self.trace: list[dict[str, Any]] = []
        self.errors: list[str] = []
        # generation | validation | runtime: only runtime failures mean the data was
        # out of reach, which is what the browser fallback can fix.
        self.failure_kind = ""
        self.render_seconds = 0.0
        self.render_tried = False


def _log_trace(log: Log, trace: list[dict[str, Any]]) -> None:
    for entry in trace:
        observation = entry.get("observation") or {}
        if observation.get("error"):
            log(f"Tool fetch {entry.get('url')} -> {observation['error']}", "warn")
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


async def _attempt(
    strategy: str,
    *,
    job: dict[str, Any],
    job_id: str,
    job_dir: Path,
    probe: dict[str, Any],
    options: dict[str, Any],
    secret: str,
    proxies: list[str],
    pool: ProxyPool,
    log: Log,
    build: _Build,
    failure: list[str] | None = None,
) -> dict[str, Any] | None:
    """Generate, validate and prove one strategy. Returns the sandbox result or None."""
    browser = strategy == "browser"
    timeout = BROWSER_SANDBOX_TIMEOUT_SECONDS if browser else SANDBOX_TIMEOUT_SECONDS
    bundle: dict[str, str] = {}
    repair_errors: list[str] = []
    max_rounds = MAX_REPAIR_ATTEMPTS + 1

    for round_number in range(1, max_rounds + 1):
        build.attempts += 1
        db.update_job(job_id, status="generating", attempts=build.attempts)
        round_cost = 0.0
        try:
            if round_number == 1:
                log(f"Calling {job['model']} ({STRATEGY_LABELS[strategy]})")
                bundle, round_cost, trace = await agent.build_bundle(
                    probe=probe,
                    options=options,
                    base_url=job["base_url"],
                    api_key=secret,
                    model=job["model"],
                    proxies=proxies,
                    pool=pool,
                    browser=browser,
                    failure=failure,
                )
                build.trace = trace
                (job_dir / "agent_trace.json").write_text(
                    json.dumps(trace, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                _log_trace(log, trace)
            else:
                log(f"Repair round {round_number - 1}/{MAX_REPAIR_ATTEMPTS}", "step")
                bundle, usage = await generator.repair(
                    files=bundle,
                    errors=repair_errors,
                    base_url=job["base_url"],
                    api_key=secret,
                    model=job["model"],
                    browser=browser,
                )
                round_cost = _usage_cost(usage)
        except (generator.GenerationError, agent.AgentError) as exc:
            log(str(exc), "error")
            build.errors = [str(exc)]
            build.failure_kind = "generation"
            return None

        generator.ensure_requirements(bundle, browser=browser)
        build.cost += round_cost
        db.update_job(job_id, cost_usd=round(build.cost, 6))
        log(
            f"Model returned {len(bundle)} files "
            f"({sum(len(v) for v in bundle.values()) / 1024:.1f} KB) "
            f"· cost ${round_cost:.4f}",
            "llm",
        )

        from .sandbox import write_bundle

        write_bundle(job_dir, bundle, proxies)
        db.update_job(job_id, files=_file_index(job_dir))

        errors = validate.validate_bundle(
            bundle,
            browser=browser,
            target_url=probe.get("final_url") or probe.get("url") or job["url"],
            allowed_hosts=_allowed_hosts(probe, build.trace),
        )
        if errors:
            for error in errors:
                log(error, "warn")
            build.errors = errors
            build.failure_kind = "validation"
            if round_number < max_rounds:
                log("Asking the model to fix validation errors…")
                repair_errors = errors
                continue
            log("Bundle still fails validation: " + "; ".join(errors[:5]), "error")
            return None

        db.update_job(job_id, status="testing")
        log(f"Sandbox run ({STRATEGY_LABELS[strategy]})", "step")
        result = await asyncio.to_thread(
            run_bundle, job_dir, bundle, proxies, SANDBOX_MAX_PAGES, timeout
        )
        if result.get("stdout"):
            log("scraper output:\n" + result["stdout"][-1500:], "code")
        if (result.get("stderr") or "").strip():
            log("stderr:\n" + result["stderr"][-1500:], "code")
        if result["ok"]:
            return result

        for error in result["errors"]:
            log(error, "error")
        build.errors = result["errors"]
        build.failure_kind = "runtime"
        if round_number < max_rounds:
            log("Asking the model to fix the runtime failure…", "warn")
            repair_errors = result["errors"]
    return None


CHALLENGE_STATUSES = discovery.CHALLENGE_STATUSES


def _challenge_markers(report: dict[str, Any]) -> list[str]:
    return [m for m in report.get("block_markers") or [] if m != "enable javascript"]


def is_blocked(probe: dict[str, Any]) -> bool:
    """True when neither the plain probe nor the render reached the real page."""
    plain_blocked = probe.get("status") in CHALLENGE_STATUSES and bool(
        _challenge_markers(probe)
    )
    if not plain_blocked:
        return False
    rendered = probe.get("rendered")
    if not rendered:
        return True
    return rendered.get("status") in CHALLENGE_STATUSES or bool(_challenge_markers(rendered))


def _allowed_hosts(probe: dict[str, Any], trace: list[dict[str, Any]]) -> set[str]:
    hosts = set(render.network_hosts(probe))
    for entry in trace or []:
        observation = entry.get("observation") or {}
        if 200 <= int(observation.get("status") or 0) < 300:
            host = urlparse(str(observation.get("url") or entry.get("url") or "")).hostname
            if host:
                hosts.add(host.lower())
    return hosts


async def _render_into(
    probe: dict[str, Any], url: str, pool: ProxyPool, log: Log, job_dir: Path, build: _Build
) -> bool:
    """Render the page in headless Chromium and attach the report to the probe."""
    build.render_tried = True
    started = time.perf_counter()
    try:
        rendered = await render.render(url, pool)
    except (render.RenderError, discovery.DiscoveryError) as exc:
        log(str(exc), "warn")
        return False
    finally:
        build.render_seconds += round(time.perf_counter() - started, 1)
    probe["rendered"] = rendered
    (job_dir / "probe.json").write_text(
        json.dumps(probe, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    for line in render.describe(rendered):
        log(line)
    if rendered.get("block_markers"):
        log(
            "Rendered page still shows anti-bot markers: "
            + ", ".join(rendered["block_markers"])
            + ". ScrapeSmith does not bypass challenges.",
            "warn",
        )
    return True


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
    browser_mode = browser_setting(options)
    started = time.perf_counter()
    build = _Build()
    probe_seconds = 0.0

    try:
        if mode == "unrestricted":
            log("Mode: not polite — robots.txt and terms are ignored by explicit choice.", "warn")
        else:
            log("Mode: polite — robots.txt and terms are respected.")
        if proxies:
            log(f"Proxy pool: {len(proxies)} entries (first: {mask_proxy(proxies[0])})")
        else:
            log("No proxies provided — requests will go direct.", "warn")

        browser_ready = browser_mode != "off" and render.available()
        if browser_mode != "off" and not browser_ready:
            log(
                "Browser fallback unavailable: Playwright is not installed "
                "(pip install -r requirements-browser.txt && "
                "python -m playwright install chromium).",
                "warn",
            )

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

        target = probe.get("final_url") or job["url"]
        if browser_ready and (browser_mode == "always" or render.looks_like_shell(probe)):
            reason = (
                "browser mode is 'always'"
                if browser_mode == "always"
                else "the page looks like a JavaScript shell"
            )
            log(f"Rendering in headless Chromium because {reason}", "step")
            await _render_into(probe, target, pool, log, job_dir, build)
            db.update_job(job_id, probe=probe)

        if is_blocked(probe) and browser_ready and not build.render_tried:
            log("The page answered with an anti-bot challenge; checking it in Chromium", "step")
            await _render_into(probe, target, pool, log, job_dir, build)
            db.update_job(job_id, probe=probe)
        if is_blocked(probe) and not proxies:
            raise discovery.DiscoveryError(
                f"The site answered {probe.get('status')} with an anti-bot challenge "
                f"({', '.join(_challenge_markers(probe))}) and no proxies were given, so "
                "the build stopped before spending model tokens. ScrapeSmith does not "
                "bypass challenges or fall back to archives and caches. Add proxies you "
                "are allowed to use, or pick a site that serves its pages to clients."
            )
        if is_blocked(probe):
            scope = (
                f"the first {PROXY_CHECK_LIMIT}"
                if 0 < PROXY_CHECK_LIMIT < len(proxies)
                else f"all {len(proxies)}"
            )
            log(
                f"Anti-bot challenge on the probe; testing {scope} proxies "
                f"({PROXY_CHECK_CONCURRENCY} at a time) before calling the model",
                "step",
            )
            check_started = time.perf_counter()
            check = await discovery.find_unblocked(
                target,
                pool,
                limit=PROXY_CHECK_LIMIT,
                concurrency=PROXY_CHECK_CONCURRENCY,
                timeout=PROXY_CHECK_TIMEOUT_SECONDS,
            )
            log(
                discovery.summarize_check(check)
                + f" in {time.perf_counter() - check_started:.1f}s"
            )
            if not check["passed_via"]:
                raise discovery.DiscoveryError(
                    "The anti-bot challenge also appears through every proxy tested "
                    f"({len(check['tested'])} of {len(proxies)}), so the build stopped "
                    "before spending model tokens. These proxies are likely recognised "
                    "as datacenter IPs. ScrapeSmith does not bypass challenges; try "
                    "proxies the site accepts."
                )
            log(f"{check['passed_via']} reaches the real page; continuing through the pool")

        log("Step 2/3 · generation", "step")
        generate_started = time.perf_counter()
        common = {
            "job": job,
            "job_id": job_id,
            "job_dir": job_dir,
            "probe": probe,
            "options": options,
            "secret": secret,
            "proxies": proxies,
            "pool": pool,
            "log": log,
            "build": build,
        }

        strategy = "http"
        result = await _attempt("http", **common)

        escalate = build.failure_kind == "runtime" or "rendered" in probe
        if result is None and browser_ready and not escalate:
            log(
                "Not starting the browser: plain HTTP failed on "
                f"{build.failure_kind or 'generation'}, not on missing data.",
                "warn",
            )
        if result is None and browser_ready and escalate:
            rendered_now = False
            if "rendered" not in probe and not build.render_tried:
                log("Plain HTTP failed — rendering the page in headless Chromium", "step")
                rendered_now = await _render_into(probe, target, pool, log, job_dir, build)
                db.update_job(job_id, probe=probe)
            if rendered_now and render.has_api(probe):
                log("The browser saw a JSON API — trying to replay it with requests", "step")
                strategy = "api"
                result = await _attempt("api", failure=build.errors, **common)
            if result is None and "rendered" in probe:
                log("Switching to the headless-browser scraper (last resort)", "step")
                strategy = "browser"
                result = await _attempt("browser", failure=build.errors, **common)

        if result is None:
            message = "; ".join(build.errors)[:800] or "no bundle was produced"
            raise generator.GenerationError(f"Sandbox run failed after repairs: {message}")

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
                "source": _guess_source(
                    probe, build.trace, strategy, _read(job_dir / "scraper.py")
                ),
                "strategy": strategy,
                "raw_count": result["raw_count"],
                "mapped_count": result["mapped_count"],
                "seconds": round(time.perf_counter() - started, 1),
                "probe_seconds": probe_seconds,
                "render_seconds": build.render_seconds,
                "generate_seconds": generate_seconds,
                "sandbox_seconds": result["duration_seconds"],
                "cost_usd": round(build.cost, 6),
                "attempts": build.attempts,
                "model": job["model"],
                "mode": mode,
            },
        )
        log(
            f"Done: {result['mapped_count']} mapped records from "
            f"{result['raw_count']} raw items in {result['duration_seconds']}s "
            f"({STRATEGY_LABELS[strategy]})",
            "step",
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
                "cost_usd": round(build.cost, 6),
                "attempts": build.attempts,
                "model": job["model"],
            },
        )


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _uses_captured_api(probe: dict[str, Any], urls: list[str], scraper: str) -> bool:
    for entry in (probe.get("rendered") or {}).get("network") or []:
        if not entry.get("item_count"):
            continue
        endpoint = urlparse(entry["url"])
        if any(urlparse(url).path == endpoint.path for url in urls):
            return True
        if endpoint.path not in ("", "/") and endpoint.path in scraper:
            return True
    return False


def _guess_source(
    probe: dict[str, Any],
    trace: list[dict[str, Any]] | None = None,
    strategy: str = "http",
    scraper: str = "",
) -> str:
    if strategy == "browser":
        return "headless browser"
    urls = [
        str(entry.get("url", ""))
        for entry in (trace or [])
        if 200 <= int((entry.get("observation") or {}).get("status") or 0) < 300
    ]
    if strategy == "api" or _uses_captured_api(probe, urls, scraper):
        return "captured api"
    types = [
        str((entry.get("observation") or {}).get("content_type") or "").lower()
        for entry in (trace or [])
        if 200 <= int((entry.get("observation") or {}).get("status") or 0) < 300
    ]
    # Judge by what came back, not by the URL: "/x.rss" often answers with HTML.
    if any(t and ("rss" in t or "atom" in t or "xml" in t) for t in types) or (
        not any(types) and any(".rss" in url or "/feed" in url for url in urls)
    ):
        return "feed"
    if any("json" in t for t in types) or (
        not any(types) and any(".json" in url for url in urls)
    ):
        return "json api"
    if probe.get("embedded_state"):
        return "embedded state"
    if probe.get("json_ld"):
        return "json-ld"
    if probe.get("api_candidates"):
        return "api candidates"
    return "html"
