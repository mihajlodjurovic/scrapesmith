# ScrapeSmith

**One URL in. A scraper and a mapper out — verified before you download it.**

[![CI](https://github.com/mihajlodjurovic/scrapesmith/actions/workflows/ci.yml/badge.svg)](https://github.com/mihajlodjurovic/scrapesmith/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688?logo=fastapi&logoColor=white)
![Tests](https://img.shields.io/badge/tests-55%20passing-46d18c)
![License](https://img.shields.io/badge/license-MIT-f5a524)

ScrapeSmith is a local web app that turns a public page into a runnable Python
scraping bundle: `scraper.py`, `mapper.py`, `runner.sh` and `README.md`. It
probes the page, lets the model **verify where the data actually lives**, writes
the bundle against a production-grade reference, then validates and **executes
it in a sandbox** before showing you a preview.

It is not a chat window that writes code. The template, the verification step,
the contract and the proof run are the product; the model supplies the
site-specific parts.

![ScrapeSmith job page](docs/screenshot.jpg)

## The interface

One form, three decisions: the page, the politeness mode, and the model. Everything
else is optional. The job page is a live trace: discovery, agent verification,
sandbox output, the generated code and the mapped preview sit on one screen.

![ScrapeSmith new build](docs/home.jpg)

## Why this exists

Any LLM can produce a Python file that looks like a scraper. That file usually
lacks the things that make scraping work in production:

- a proxy pool with cooldowns and blacklisting;
- retries with backoff and `Retry-After`;
- a polite delay and a page cap;
- API-first parsing with an HTML fallback;
- stable IDs and idempotent output;
- normalisation of prices, dates and numbers;
- a mapper that keeps a schema instead of dumping strings;
- tests, or any way to know the thing runs.

Worse, an unconstrained model invents endpoints, hardcodes credentials, imports
`subprocess`, and reports success without ever fetching a record.

ScrapeSmith encodes the missing experience once, then puts a GUI in front of it.
The person using it does not need to know what SSRF is, why `verify=False` is
dangerous, or why `__NEXT_DATA__` matters. They paste a URL and get software.

## The five gates

```text
URL --> 1. PROBE      robots, headers, API candidates, embedded state,
         |            repeated DOM blocks, trimmed skeleton
         v
      2. VERIFY      the agent fetches candidate sources (.json, .rss,
         |            /feed, API paths) before writing code
         v
      3. GENERATE    reference scraper + mapper + schema + verified source
         |            -> four files
         v
      4. VALIDATE    AST, forbidden imports, secret scan, contract,
         |            schema coverage - no execution yet
         v
      5. PROVE       subprocess run, one page, mapped preview,
                     per-field fill rates, repair loop on failure
```

Each gate exists because of a failure mode seen in real scraping work. The
`docs/DESIGN-DECISIONS.md` notes record the trade-offs.

### Verification is the part that matters

A page can return `200` and still contain nothing: a JavaScript shell, a consent
wall, an anti-bot challenge. On Reddit, for example, `.json` answers `403`,
`old.reddit.com` answers `403`, but `.rss` returns a full Atom feed. A one-shot
model guesses and fails. ScrapeSmith gives the model a fetch tool with a six-call
budget and a same-domain guard; the model checks, then writes code against what
actually returned `2xx`. The whole trace is saved as `agent_trace.json`.

## What you get

```text
jobs/<id>/
  scraper.py        proxy pool, retries, API-first fetch, JSONL output, CLI
  mapper.py         raw → generic record schema, number/price parsing, stable ids
  runner.sh         scrape → map in one command
  README.md         what the site is, what was assumed, what breaks first
  probe.json        the discovery report used for the build
  agent_trace.json  every source the model verified
  proxies.json      runtime proxy list (never included in the download zip)
  raw.jsonl         sample raw output from the proof run
  mapped.jsonl      sample mapped output from the proof run
```

Generated scrapers accept:

```bash
python scraper.py --max-pages 5 --out raw.jsonl --proxies proxies.json [--mongo URI]
```

### Proxy rotation

One `ProxyPool` serves the probe, the agent's verification fetches and the
generated scraper:

- least-failing, least-recently-used proxy first;
- cooldown on `401/403/407/429/503`;
- blacklist after five consecutive failures, reset on success;
- direct connection only as the last attempt, never first;
- health stats (`available/cooling/blacklisted/requests/failures`) written to
  `probe.json` and the live log.

Accepted formats: `http://user:pass@host:port`, `host:port:user:pass`,
`host:port`, JSON objects (`server`/`username`/`password`), or one per line.

The job summary also records `probe_seconds`, `generate_seconds` and
`sandbox_seconds`, so a slow run is easy to attribute.

### Records

| Field | Meaning |
|---|---|
| `id` | Stable across runs: source id, URL, or a hash |
| `url` | Absolute link to the item |
| `title` | Headline as shown on the page |
| `price` / `currency` | Numeric amount and ISO code, kept separate |
| `description` | Cleaned summary or body |
| `location` | City/region text when present |
| `images` | Up to 20 absolute image URLs |
| `attributes` | Domain fields (author, rating, mileage, tags, …) |
| `source` | Hostname |
| `scraped_at` | ISO 8601 UTC timestamp |

One schema covers products, cars, books, jobs, rentals and articles because
domain-specific data lives in `attributes`. Optional **Fields to capture** in the
form nudges the model (`author, rating, availability`).

## Quickstart

```bash
git clone https://github.com/mihajlodjurovic/scrapesmith.git
cd scrapesmith

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

cp .env.example .env               # add SCRAPESMITH_API_KEY
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>, paste a URL, press **Generate scraper**.

### Model setup

Any OpenAI-compatible endpoint works. Pick a preset in the UI or set the three
values yourself:

```ini
SCRAPESMITH_API_KEY=...
SCRAPESMITH_BASE_URL=https://api.openai.com/v1
SCRAPESMITH_MODEL=gpt-4.1-mini
```

| Provider | Base URL | Example model |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `gpt-4.1-mini` |
| OpenRouter | `https://openrouter.ai/api/v1` | `anthropic/claude-sonnet-4` |
| Groq | `https://api.groq.com/openai/v1` | `llama-3.3-70b-versatile` |
| Together | `https://api.together.xyz/v1` | `meta-llama/Llama-3.3-70B-Instruct-Turbo` |
| Ollama (local) | `http://127.0.0.1:11434/v1` | `qwen2.5-coder:14b` (no key) |
| Custom | any compatible URL | any model id |

The key can also be pasted per job; it is never stored. For local models without
authentication, leave it empty.

## Use it from a coding agent

The repository is designed to be handed to Codex, Claude Code, Cursor, Gemini CLI
or Copilot. `AGENTS.md` is the contract; the same commands work everywhere:

```bash
# generate a bundle (JSON on stdout, logs on stderr, exit 0 = done)
python scripts/build.py "https://example.com/list" \
    --fields "title, price" --json --out ./bundles/example

# hand-write or repair a bundle
python scripts/scaffold_bundle.py ./bundles/example
python scripts/validate_bundle.py ./bundles/example

# check the configured model first
python scripts/smoke_llm.py
```

`result.json` next to the bundle reports status, record counts, fill rates, the
verified source, cost and the exact error if one occurred. Agent guides:
[AGENTS.md](AGENTS.md) · [workflow](docs/AGENT-WORKFLOW.md) ·
[bundle contract](docs/BUNDLE-CONTRACT.md).

## Politeness modes

| Mode | Behaviour |
|---|---|
| `polite` (default) | Fetches and enforces `robots.txt`. A disallowed path stops the build before any model tokens are spent. |
| `unrestricted` ("not polite") | Ignores `robots.txt`, logs a warning, records the choice on the job. Use only for sites you own or have written permission to scrape. |

Both modes refuse logins, CAPTCHA solving and private data. The tool is a
scraper generator, not a ban-evasion tool.

## Evaluation snapshot

Six targets, one model, mixed modes, no manual fixes:

| Target | Shape | Mode | Outcome | Records | Cost |
|---|---|---|---|---:|---:|
| `books.toscrape.com` | static HTML | polite | done | 20 / 20 | $0.0075 |
| `quotes.toscrape.com` | repeated blocks | polite | done | 10 / 10 | $0.0066 |
| Reddit thread | JS shell, JSON 403 | unrestricted | done via Atom | 42 / 42 | $0.0070 |
| `jsonplaceholder.typicode.com/posts` | plain JSON | polite | done | 100 / 100 | $0.0078 |
| `autoscout24.com/lst` | SPA, robots disallow | polite | policy stop (expected) | 0 | $0.0000 |
| YouTube watch page | detail page, embedded JSON | polite | done | 1 / 1 | $0.0176 |

Costs are for `deepseek-v4.1-flash`; frontier models are roughly 10-50x more
expensive per build. Reddit used `unrestricted` mode only to test feed discovery;
the default `polite` mode refuses that path. Run the override only on sites you
have permission to scrape.

Method, threats to validity and reproduction commands:
[docs/EVALUATION.md](docs/EVALUATION.md). The same AutoScout page, run explicitly
in `unrestricted` mode, produced 20/20 records for $0.0103.

## Quality gates in detail

A bundle is rejected before execution if it:

- imports `subprocess`, `socket`, `ctypes`, `pickle`, `shutil`, `multiprocessing`;
- calls `eval`, `exec`, `compile`, `os.system`, `os.popen`;
- contains Mongo/proxy/API credentials or `verify=False`;
- misses `fetch_page`, `parse_items`, `request`, `run`, `main`, `map_listing`;
- drops a field from `CANONICAL_KEYS`;
- has a `runner.sh` that does not run both scripts.

The sandbox then:

- compiles both files;
- runs the scraper for one page with a scrubbed environment (no API key);
- runs the mapper and reports record count plus per-field fill rates;
- feeds failures back to the model for up to two repair rounds.

## Safety

- SSRF guard: private, loopback, link-local and reserved hosts are refused.
- Keys live in memory for one job, are redacted from logs, never stored in the
  database, and are absent from the sandbox environment.
- `proxies.json` is excluded from the download zip.
- The sandbox is a guardrail, not a hardened jail — see
  [SECURITY.md](SECURITY.md).

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SCRAPESMITH_API_KEY` | — | Fallback key if the form field is empty |
| `SCRAPESMITH_PROVIDER` | `openai` | Preset selected in the UI |
| `SCRAPESMITH_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible base URL |
| `SCRAPESMITH_MODEL` | `gpt-4.1-mini` | Default model |
| `SCRAPESMITH_MODE` | `polite` | `polite` or `unrestricted` |
| `SCRAPESMITH_MAX_REPAIRS` | `2` | Extra rounds after a failure |
| `SCRAPESMITH_SANDBOX_TIMEOUT` | `180` | Per subprocess limit (s) |
| `SCRAPESMITH_SANDBOX_MAX_PAGES` | `1` | Pages fetched during the proof |
| `SCRAPESMITH_REASONING_EFFORT` | `low` | Keeps tokens for code |
| `SCRAPESMITH_LLM_HEADERS` | — | Extra request headers as JSON, for gateways that require them |
| `SCRAPESMITH_ALLOW_PRIVATE` | `0` | Allow local hosts (testing only) |

## Limitations

- **No headless browser.** JavaScript shells are handled through structured
  siblings, not rendering. Sites with neither are out of reach in v1.
- **Chat Completions only.** Models that require the Responses API need an
  adapter. The agent tool protocol is text or JSON, not native function calling.
- **The sandbox is a guardrail, not a jail.** Generated code runs with a timeout
  and a scrubbed environment, but it can still make network calls and read files
  your user can read. Use a container or VM for untrusted use.
- **One page of proof.** The sandbox proves the bundle runs, not that it crawls a
  whole catalogue correctly.
- **Single node.** SQLite and a local job directory; no queue or shared storage.
- **Small evaluation.** Six targets, one model, one day. Treat the numbers as a
  smoke test, not a benchmark.

## Documentation

| Document | Contents |
|---|---|
| [AGENTS.md](AGENTS.md) | Agent entry point: paths, commands, hard rules |
| [docs/AGENT-WORKFLOW.md](docs/AGENT-WORKFLOW.md) | Decision tree, failure playbook, reporting |
| [docs/BUNDLE-CONTRACT.md](docs/BUNDLE-CONTRACT.md) | Exact file/function/CLI contract |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Components, lifecycle, agent protocol, trust boundaries |
| [DESIGN-DECISIONS.md](docs/DESIGN-DECISIONS.md) | Why a code bundle, why verification, why a subprocess |
| [EVALUATION.md](docs/EVALUATION.md) | Method, results, threats to validity |
| [SECURITY.md](SECURITY.md) | Threat model and controls |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Setup, tests, extension points |
| [CHANGELOG.md](CHANGELOG.md) | What shipped in 0.1.0 |

## Roadmap

- [ ] MCP server exposing `build`, `validate` and `scaffold` as tools
- [ ] Playwright fallback behind a flag, same bundle contract
- [ ] Recipe mode: emit a declarative selector spec before code
- [ ] Scheduled re-runs with schema-drift diffs
- [ ] Per-domain rate limiter shared across jobs
- [ ] Bundle diffing between repair attempts in the UI

## Origin

The scraper/mapper template is distilled from a production pipeline that has
ingested millions of vehicle listings across eleven European markets: proxy
pools that lose a third of their members every week, sites that silently switch
from JSON endpoints to server-rendered payloads, and mappers that must coerce
postal codes without turning `3250.0` into `32500`.

ScrapeSmith packages those rules so the next site takes an afternoon, not a
sprint — and so someone who has never written a scraper can produce one without
having to know the pitfalls first.

## License

MIT
