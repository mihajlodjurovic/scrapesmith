# AGENTS.md

This file is the entry point for coding agents (Codex, Claude Code, Cursor,
Gemini CLI, Copilot, …). Read it before touching the repo.

## What this repo is

ScrapeSmith turns **one public URL** into a **scraper + mapper bundle** that has
been statically validated and executed once in a sandbox. The model writes the
site-specific parts; the repository supplies discovery, a production-shaped
template, the contract and the proof run.

If the user says *"here is a link, use this repo to make a scraper"*, you have
three supported paths:

| Situation | Path |
|---|---|
| An API key is available and the site is reachable | **Generate** with `scripts/build.py` (recommended) |
| The site is a JavaScript shell, blocked, or the model failed | **Scaffold + hand-write**, then validate |
| A bundle already exists and needs review/repair | **Validate**, then run its `runner.sh` |

## Path A — generate (default)

```bash
# 1. environment
python scripts/smoke_llm.py          # confirms the configured model + key

# 2. build (JSON on stdout, logs on stderr, exit 0 = done, 2 = failed)
python scripts/build.py "https://example.com/list" \
    --fields "title, price, author" \
    --json --out ./bundles/example

# 3. read the result
cat ./bundles/example/result.json
```

`result.json` contains `status`, `records.mapped`, `fill_rates`, `source`,
`cost_usd`, `agent_trace`, `error` and the output directory. On success the
bundle is already proven: `raw.jsonl` and `mapped.jsonl` exist.

Useful flags:

- `--mode polite` (default) or `--mode unrestricted` — the second one ignores
  `robots.txt`. **Never use it without explicit user permission.**
- `--proxies path-or-json` — a file, JSON array, or newline list.
- `--model`, `--base-url`, `--provider` — override the provider preset.
- `--out DIR` — copy the finished bundle (never copies `proxies.json`).
- `--browser auto|always|off` — headless-browser fallback for JavaScript-rendered
  pages (needs `pip install -r requirements-browser.txt` and
  `python -m playwright install chromium`). `result.json` reports the winning
  `strategy`: `http`, `api` (captured JSON replayed with requests) or `browser`.

## Path B — scaffold + hand-write

Use this when the site needs logic the generator could not verify, or when the
user asks you to write the scraper yourself.

```bash
python scripts/scaffold_bundle.py ./bundles/site
# edit ./bundles/site/scraper.py and ./bundles/site/mapper.py
python scripts/validate_bundle.py ./bundles/site --json
cd ./bundles/site && bash runner.sh
```

Keep the reference plumbing: `load_proxies`, `ProxyPool`, `request`, `run`,
`main`, the `--max-pages/--out/--proxies/--mongo` flags, and the mapper's
`CANONICAL_KEYS`. Replace only the site-specific sections (`page_url`,
`parse_items`, `extract_payload`, `map_listing`).

Read `docs/BUNDLE-CONTRACT.md` for the exact requirements, and
`docs/AGENT-WORKFLOW.md` for the decision tree and failure playbook.

## Path C — review or repair an existing bundle

```bash
python scripts/validate_bundle.py ./bundles/site
python -m py_compile ./bundles/site/scraper.py ./bundles/site/mapper.py
cd ./bundles/site && bash runner.sh
```

If the sandbox produced zero records, inspect in this order:

1. `probe.json` — was the page a shell, blocked, or disallowed?
2. `agent_trace.json` — which sources returned 2xx, with what content type?
3. the sandbox stdout/stderr — status codes, selector misses, parse errors.

## Hard rules

- **Politeness.** `polite` is the default and must stay the default. If
  `robots.txt` disallows the path, stop and tell the user; only switch to
  `unrestricted` with explicit permission.
- **No bypass.** Never add logins, CAPTCHA solving, paywall circumvention or
  private-data collection. Never fetch the data from archives (archive.org),
  search engines, caches or scraping APIs when the site blocks you — report the
  block instead; the validator rejects such bundles.
- **No secrets.** API keys live in `.env`; proxy credentials in `proxies.json`.
  Never commit either, never print them, never copy `proxies.json` into a
  deliverable.
- **Do not edit `app/reference/`** to make one site work. That template is the
  contract for every site. Site-specific changes go in the generated bundle.
- **Do not commit `data/`** — it holds scraped content, job artifacts and proxy
  lists.
- **One page of proof is not a crawl guarantee.** Say so when reporting.

## Repository map

| Path | Purpose |
|---|---|
| `app/render.py` | Headless Chromium render: captured XHR/fetch JSON, rendered DOM, SSRF-guarded routing |
| `app/discovery.py` | Polite probe: robots, headers, API candidates, embedded state, repeated blocks, signals |
| `app/agent.py` | Bounded fetch tool + verify-before-write loop |
| `app/prompt.py` | Reference bundle + schema + probe → prompt; candidate URL list |
| `app/validate.py` | AST, forbidden imports, secret scan, contract, schema coverage |
| `app/sandbox.py` | Subprocess run, JSONL contract, preview, fill rates |
| `app/pipeline.py` | State machine and repair loop |
| `app/reference/` | Golden scraper, mapper, runner, schema — the contract |
| `app/cli.py` | Functions behind `scripts/build.py`, `validate_bundle.py`, `scaffold_bundle.py` |
| `docs/` | Architecture, decisions, evaluation, workflow, contract |
| `tests/` | 114 tests (browser tests skip without Playwright); run `pytest -q` before claiming success |

## Definition of done

A task is complete when **all** of these are true:

1. `python scripts/validate_bundle.py <bundle>` exits 0.
2. The bundle produced at least one mapped record in a run.
3. `README.md` in the bundle states the data source and the main assumption.
4. No secret, proxy credential or `data/` file was added to git.
5. You reported `status`, `records.mapped`, `fill_rates`, `source` and `cost_usd`
   from `result.json` (or explained exactly why the build failed).

## Environment

```ini
SCRAPESMITH_API_KEY=...        # any provider
SCRAPESMITH_BASE_URL=...       # OpenAI-compatible base URL
SCRAPESMITH_MODEL=...          # model id
SCRAPESMITH_MODE=polite        # polite | unrestricted
```

The UI (`uvicorn app.main:app`) is for humans; the scripts above are the stable
interface for agents. Both use the same pipeline and the same contract.
