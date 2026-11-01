# ScrapeSmith

**Paste a URL. Get a scraper and a mapper you'd actually ship.**

ScrapeSmith is a small web app that turns one public listing page into a runnable
Python scraping bundle: `scraper.py` + `mapper.py` + `runner.sh` + `README.md`.
It probes the site first, hands the model a battle-tested template instead of an
empty page, validates the returned code, and **proves it runs** in a sandbox
before letting you download it.

It is not a "chatbot that writes code". The template, the validation contract and
the sandbox are the product; the model just fills in the site-specific parts.

---

## Why

Writing a scraper is easy. Writing the *same* scraper for the twentieth time —
with a proxy pool, cooldowns, retries, API-first fallback, polite delays, stable
IDs, canonical field mapping and enum translation — is where teams lose days.
Generated code from a chat window usually skips all of that, or invents
endpoints, or hardcodes credentials.

ScrapeSmith encodes the boring parts once:

- **API first, HTML second.** Discovery looks for JSON endpoints and embedded
  state (`__NEXT_DATA__`, RSC flight payload, `__NUXT__`, Apollo/Redux) before
  falling back to DOM parsing.
- **Polite by default.** 0.2 s delay with jitter, capped pages, robots.txt check,
  `Retry-After` awareness, no CAPTCHA or login bypass.
- **Proxies are first-class.** Parsed, rotated, cooled down on 401/403/407/429,
  blacklisted after repeated failures, never hardcoded.
- **The mapper is part of the bundle.** Raw payload → canonical schema with
  integer coercion, enum maps and stable IDs.
- **Validated, not trusted.** AST checks, forbidden imports, secret scanning,
  contract functions, schema coverage — then a real subprocess run.

## How it works

```text
URL + key + proxies
        │
        ▼
┌──────────────────┐   robots.txt, headers, status, API candidates,
│ 1. Discovery     │   embedded state, repeated DOM blocks, HTML skeleton
└────────┬─────────┘
         ▼
┌──────────────────┐   reference scraper + mapper + schema + probe → model
│ 2. Generation    │   returns 4 files
└────────┬─────────┘
         ▼
┌──────────────────┐   AST, banned imports/calls, secrets, contract,
│ 3. Validation    │   CANONICAL_KEYS coverage, file size limits
└────────┬─────────┘
         ▼
┌──────────────────┐   scraper --max-pages 1 → raw.jsonl
│ 4. Sandbox run   │   mapper --in raw.jsonl → mapped.jsonl
└────────┬─────────┘   preview + per-field fill rates
         ▼
   done ── or ──► repair loop (max 2) with the exact error fed back
```

Everything the pipeline learns is stored in `probe.json` next to the generated
code, so a reviewer can audit *why* the model produced that parse logic.

## Quickstart

```bash
git clone <this-repo>
cd scrapesmith
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env      # add OPENCODE_API_KEY
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>, paste a URL, press **Generate scraper**.

### Using OpenCode inference (recommended)

[OpenCode Console](https://opencode.ai/console) exposes an OpenAI-compatible
inference API that works from external clients:

```bash
OPENCODE_API_KEY=oc_sk_...
SCRAPESMITH_BASE_URL=https://opencode.ai/inference/openai/v1
SCRAPESMITH_MODEL=minimax-m3
```

Paid models or a Go subscription are required for external clients; free-tier
models only work inside OpenCode itself and return `403` here.

Any OpenAI-compatible endpoint works: OpenAI, OpenRouter, or a self-hosted
gateway. The key can also be pasted in the form per job.

### Docker

```bash
docker build -t scrapesmith .
docker run --rm -p 8000:8000 -e OPENCODE_API_KEY=oc_sk_... scrapesmith
```

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `OPENCODE_API_KEY` | — | Fallback key if the form field is empty |
| `SCRAPESMITH_BASE_URL` | OpenCode inference | OpenAI-compatible base URL |
| `SCRAPESMITH_MODEL` | `minimax-m3` | Default model |
| `SCRAPESMITH_MAX_REPAIRS` | `2` | Extra generate attempts after failures |
| `SCRAPESMITH_FETCH_TIMEOUT` | `25` | Discovery request timeout (s) |
| `SCRAPESMITH_SANDBOX_TIMEOUT` | `180` | Per subprocess limit (s) |
| `SCRAPESMITH_SANDBOX_MAX_PAGES` | `1` | Pages fetched during the proof run |
| `SCRAPESMITH_ALLOW_PRIVATE` | `0` | Allow probing private/loopback hosts (local testing only) |

## The bundle

```
jobs/<id>/
  scraper.py    proxy pool, retries/backoff, API-first fetch, JSONL output, CLI
  mapper.py     raw → canonical schema, integer coercion, enum maps, stable ids
  runner.sh     scrape → map in one command
  README.md     what the site is, what was assumed, what breaks first
  probe.json    discovery report used for the build
  proxies.json  runtime proxy list (never included in the download zip)
  raw.jsonl     sample raw output from the proof run
  mapped.jsonl  sample canonical output from the proof run
```

Generated scrapers accept:

```bash
python scraper.py --max-pages 5 --out raw.jsonl --proxies proxies.json [--mongo URI]
```

## Validation contract

A bundle is rejected before execution if it:

- imports `subprocess`, `socket`, `ctypes`, `pickle`, `shutil` or `multiprocessing`;
- calls `eval`, `exec`, `compile`, `os.system`, `os.popen`;
- contains Mongo/proxy/API credentials or disables TLS verification;
- misses `fetch_page`, `parse_items`, `request`, `run`, `main`, `map_listing`;
- drops a field from `CANONICAL_KEYS`;
- has a `runner.sh` that doesn't run both scripts.

If validation passes but the sandbox fails, the exact stderr tail is fed back to
the model and the bundle is regenerated — up to two repair rounds.

## Security model

- The API key lives in memory for the duration of a job, is never written to
  SQLite, and is redacted from every log line.
- Discovery has an SSRF guard: URLs resolving to private, loopback, link-local,
  reserved or multicast addresses are refused.
- Generated code runs in a separate subprocess with a timeout and a scrubbed
  environment (no API key) — a guardrail, not a hardened jail. Run the tool on
  your own machine or a dedicated container.
- `proxies.json` is excluded from downloads so credentials don't leak into a zip.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/jobs` | Start a build |
| `GET` | `/api/jobs` | Recent builds |
| `GET` | `/api/jobs/{id}` | Status, files, preview, cost |
| `GET` | `/api/jobs/{id}/events` | SSE log stream |
| `GET` | `/api/jobs/{id}/files/{name}` | Read one generated file |
| `GET` | `/api/jobs/{id}/download` | Zip the bundle |
| `GET` | `/api/providers` | Provider presets |

Interactive docs: `/api/docs`.

## Tests

```bash
pytest -q      # 32 tests, no network and no model calls
ruff check .
```

Covered: proxy parsing, discovery heuristics (API candidates, embedded state,
repeated blocks, skeleton), bundle validation and each rejection path, model
reply parsing, sandbox execution with preview/fill rates, and the JSON API.

## Limitations

- Chat Completions only; GPT models that require the Responses API need an
  adapter.
- No JavaScript rendering. JS-heavy sites are detected and reported, not
  bypassed — adding Playwright would contradict the "fast, deterministic"
  promise for v1.
- The sandbox proves one page, not a full crawl.
- Multi-process fan-out would need a queue and a shared hub; this is a
  single-node tool by design.

## Roadmap

- [ ] Playwright fallback behind a flag, with the same bundle contract
- [ ] Recipe mode: emit a declarative selector spec before code, so reviews are cheap
- [ ] Scheduled re-runs with schema drift diffs between runs
- [ ] Per-field confidence from two sample pages instead of one
- [ ] Bundle diffing between model attempts in the UI

## Origin

The scraper/mapper template is distilled from a production pipeline that has
ingested millions of vehicle listings across eleven European markets — proxy
pools that lose a third of their members every week, sites that silently switch
from JSON endpoints to server-rendered payloads, and mappers that must coerce
postal codes to integers without turning `3250.0` into `32500`.

ScrapeSmith packages those hard-won rules so the next site takes an afternoon,
not a sprint.

## License

MIT
