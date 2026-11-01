# Architecture

ScrapeSmith is a single-node FastAPI service with four trust boundaries:

```text
browser ──HTTP──► FastAPI ──► discovery (ssrf-guarded) ──► agent (model + fetch tool)
                    │                                            │
                    │                                            ▼
                    │                                     bundle validation (static)
                    │                                            │
                    │                                            ▼
                    └──────────── SQLite + job dir ◄──── sandbox (subprocess, limits)
```

## Components

| Module | Responsibility |
|---|---|
| `app/routes/api.py` | Job creation, SSE log stream, file access, zip download |
| `app/routes/pages.py` | Server-rendered UI shell |
| `app/pipeline.py` | State machine: discover → verify/generate → validate → run → repair |
| `app/discovery.py` | One polite probe: robots, headers, API candidates, embedded state, repeated blocks, skeleton |
| `app/agent.py` | Bounded tool loop; the model may fetch candidate sources before writing code |
| `app/prompt.py` | Reference bundle + schema + probe → prompt; candidate URL list |
| `app/generator.py` | Parse `### FILE:` sections, repair prompts, incomplete-reply retry |
| `app/validate.py` | AST, forbidden imports/calls, secret scan, contract and schema coverage |
| `app/sandbox.py` | Subprocess execution, JSONL contract, preview and fill rates |
| `app/llm.py` | OpenAI-compatible client, Go session header, cost extraction |
| `app/db.py` | SQLite job/event store; artifacts live on disk |
| `app/reference/` | The golden scraper, mapper, runner and record schema |

## Lifecycle

1. **Job creation.** The API validates the payload, stores the job (key excluded),
   and schedules `pipeline.run_job` as an asyncio task. At most three jobs run at
   once.
2. **Discovery.** A single GET with a polite user agent, proxy fallback, timeout
   and body cap. `robots.txt` is fetched and parsed. The page is reduced to a
   JSON report: status, headers, API candidates, embedded state, repeated DOM
   blocks, JSON-LD types and a trimmed HTML skeleton.
3. **Policy gate.** In `polite` mode a disallowed robots path aborts before any
   model call. In `unrestricted` mode the build continues with a warning. Both
   modes refuse logins, CAPTCHA solving and private data.
4. **Agent verification.** The model receives the reference bundle, the record
   schema, the discovery report and a list of candidate URLs (JSON/RSS/feed
   siblings). It may call `TOOL: fetch <url>` up to six times. The tool is
   restricted to the target's registrable domain, uses proxy rotation, and
   returns status, content type, size and a 3 KB preview.
5. **Generation.** Once the model knows where the data lives it emits four files
   in a strict `### FILE:` format. Partial replies are rejected and retried once;
   after that the job fails with a readable error.
6. **Static validation.** No network, no execution.
7. **Sandbox.** `py_compile` both scripts, run the scraper for one page, then the
   mapper. The process gets a scrubbed environment (no API key), a timeout and a
   working directory containing `proxies.json`.
8. **Preview.** The first five mapped records and per-field fill rates are shown.
9. **Repair.** Validation or runtime errors are fed back verbatim, up to two
   rounds. The bundle is always returned in full, never as a diff.
10. **Persistence.** Artifacts are written under `data/jobs/<id>/`; the DB stores
    status, cost, attempts, file index, probe summary and preview.

## Discovery signals

The probe is deterministic and cheap; it never calls the model.

| Signal | Why it matters |
|---|---|
| `robots` | Policy gate and crawl-delay |
| `api_candidates` | Same-origin JSON endpoints quoted in scripts |
| `embedded_state` | `__NEXT_DATA__`, RSC flight, `__NUXT__`, Apollo/Redux |
| `repeat_blocks` | Sibling groups with the same tag/classes — the item boundary |
| `json_ld` | `schema.org` types and keys |
| `skeleton` | 45 KB max, scripts/styles stripped, class/id kept |
| `block_markers` | Anti-bot/CAPTCHA hints; surfaced, never bypassed |

Selectors are not guessed by string matching; repeated blocks are found by
grouping direct siblings on `(tag, class tuple)` and scoring by count × link
density. That is why the report can say "20 items under `article.listing`"
without knowing the site.

## Agent protocol

```text
TOOL: fetch https://example.com/list.rss
```

The observation is JSON:

```json
{"url": "...", "requested_url": "...", "status": 200, "content_type": "application/atom+xml",
 "bytes": 27141, "entry_count": 42, "proxy": "direct", "preview": "<feed>..."}
```

Constraints:

- same registrable domain only (subdomains allowed);
- six calls maximum, small delay, no auth, no private data;
- a 403 on one route does not end the search;
- the final message must contain all four files.

Every call is written to `agent_trace.json` beside the bundle, so the reasoning
is auditable after the fact.

## Validation contract

The bundle is rejected if it:

- imports `subprocess`, `socket`, `ctypes`, `pickle`, `shutil`, `multiprocessing`;
- calls `eval`, `exec`, `compile`, `os.system`, `os.popen`;
- contains Mongo/proxy/API credentials or `verify=False`;
- misses `fetch_page`, `parse_items`, `request`, `run`, `main`, `map_listing`;
- drops a field from `CANONICAL_KEYS`;
- has a `runner.sh` that does not run both scripts;
- exceeds 120 KB per file.

Parsing is `ast.parse`, not regex, so a syntax error is reported with a line
number and the repair prompt gets an exact message.

## Sandbox

```text
python -m py_compile scraper.py mapper.py
python scraper.py --max-pages 1 --out raw.jsonl --proxies proxies.json
python mapper.py --in raw.jsonl --out mapped.jsonl
```

- `subprocess.run` with a configurable timeout (180 s default).
- Environment allowlist: `PATH`, `SYSTEMROOT`, `TEMP`, `TMP`, `HOME`,
  `USERPROFILE`, `LANG`, plus `PYTHONIOENCODING`, `PYTHONPATH`, `PROXIES_FILE`.
  The model key is never present.
- Output is capped; stderr tails are fed into repair prompts.
- This is a guardrail, not a hardened jail. Run it on a machine or container you
  control.

## Data model

```text
jobs(id, url, status, provider, base_url, model, options, attempts, cost_usd,
     error, probe, files, preview, summary, created_at, updated_at)
events(id, job_id, ts, level, message)
```

Artifacts:

```text
data/jobs/<id>/
  scraper.py  mapper.py  runner.sh  README.md
  probe.json  agent_trace.json  proxies.json
  raw.jsonl   mapped.jsonl
```

SQLite runs in WAL mode; writes are short and serialized by SQLite itself.

## Proxy health

A job creates one `ProxyPool` (`app/proxies.py`) and shares it between discovery
and the agent's fetch tool, so a proxy burned during the probe is not retried by
the model. The generated scraper ships its own copy of the same rules because it
runs later, outside the app.

Selection is deterministic: fewest failures, then oldest last use. This avoids
both hammering the same exit IP and randomly picking a known-bad one. Responses
`401/403/407/429/503` trigger a cooldown; five consecutive failures blacklist the
proxy; a success resets the counter. Direct is the final attempt, not the first.

```text
attempts(limit) -> [least_failing, next, ..., None]
report(proxy, ok, status) -> failures/consecutive/cooldown/blacklist updates
stats() -> {total, available, cooling, blacklisted, requests, failures}
```

`probe.json` stores the stats after the probe and the pipeline logs them, so a
job that fails because every proxy is dead is distinguishable from a job that
fails because the site is down.

## Scaling notes

The single-node design is deliberate for v1. To scale:

- move jobs to a real queue (RQ/Celery) and keep the API stateless;
- replace event polling with Redis pub/sub or Postgres LISTEN/NOTIFY;
- store artifacts in object storage;
- run the sandbox in a container per job with network policy;
- put a per-domain rate limiter in front of discovery and the fetch tool.

None of these change the bundle contract, which is the part worth preserving.

## Failure taxonomy

| Failure | Where it surfaces | Recovery |
|---|---|---|
| Robots disallowed (polite) | Policy gate | Switch mode only with permission |
| Blocked page / JS shell | Discovery report | Agent verifies alternate sources |
| Model returns no text | LLM client | Retry with diagnostics; lower reasoning effort |
| Incomplete bundle | Generator | One re-ask, then fail |
| Contract violation | Validator | Repair round |
| Scraper runs, zero records | Sandbox | Repair round with stdout/stderr |
| Mapper fails | Sandbox | Repair round |
| Timeout | Sandbox | Repair round or fail |
