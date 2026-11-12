# Changelog

## Unreleased

Browser fallback for client-side rendered sites.

- Optional Playwright extra (`requirements-browser.txt`). When the probe looks
  like a JavaScript shell, the page is rendered in headless Chromium and the
  model sees the rendered DOM plus every JSON response the page fetched.
- The browser reference scraper scrolls infinite lists until they stop growing,
  with its own scroll budget (`SCRAPESMITH_MAX_SCROLLS`).
- Strategy order: plain HTTP → replay of a captured JSON API with requests →
  Playwright scraper (`app/reference/scraper_browser.py`, same contract).
- Browser setting in the form, `SCRAPESMITH_BROWSER` and `--browser`; job summary
  and `result.json` report `strategy` and `render_seconds`.
- All browser requests and redirect hops pass the SSRF guard; cookies and auth
  headers are never shown to the model; stealth/evasion packages are rejected.
- The browser starts only when plain HTTP failed on missing data (or the page
  is a shell), never because the model failed validation.
- Secret scan ignores placeholder credentials in docs (`mongodb://user:pass@...`).
- Sandbox clears `raw.jsonl`/`mapped.jsonl` before each run so a previous
  attempt can never count as proof.

No fake successes on blocked sites.

- Builds stop before any model call when the site answers with an anti-bot
  challenge and no proxies were given (a real-browser render is tried first).
- The validator rejects bundles that take data from archives, search engines,
  caches or scraping APIs, or from foreign hosts the page never called.
- Prompt rule 12: report a block, never route around it.
- With proxies, an anti-bot wall is checked through every proxy in the list
  (plain GETs, 8 at a time, stops at the first one that gets through) before any
  model call; if all are challenged the build stops. Tunable with
  `SCRAPESMITH_PROXY_CHECK_LIMIT`, `_CONCURRENCY` and `_TIMEOUT`.
- The headless render runs at most once per job and reports every route it tried.
- Pasted proxy lists like `[host:port:user:pass ...]` (not valid JSON) parse cleanly.
- A thinking model that spends its whole budget reasoning gets one retry with
  `SCRAPESMITH_LLM_RETRY_MAX_TOKENS` (32000).
- Job logs no longer crash on consoles that cannot encode the message (Windows
  cp1252).

Security hardening.

- SSRF guard now re-checks every request hop, so a public page can no longer
  redirect the probe or the agent's fetch tool to a private, loopback or
  metadata address. The agent's fetch tool is now covered by the guard, so a
  same-site subdomain that resolves to a private IP is refused.
- Bundles may only contain `scraper.py`, `mapper.py`, `runner.sh`, `README.md`,
  `requirements.txt` (and notes). Extra modules such as `helper.py` or
  `sitecustomize.py` are rejected and never written to the sandbox directory.
- `SCRAPESMITH_API_KEY` is only sent to `SCRAPESMITH_BASE_URL` or a preset
  endpoint; a custom base URL requires a key pasted with the job.

## 0.1.0 — 2026-11-01

First public version.

- Discovery probe: robots.txt, headers, API candidates, embedded state, repeated
  DOM blocks, trimmed skeleton, proxy health stats.
- Bounded agent verification before generation (JSON/RSS/feed siblings, six
  fetches, same-site guard, `agent_trace.json`).
- Code bundle generation from a production-shaped reference: `scraper.py`,
  `mapper.py`, `runner.sh`, `README.md`.
- Static contract: AST checks, forbidden imports/calls, secret scan, schema
  coverage, incomplete-reply retry.
- Sandbox proof: subprocess run, one page, mapped preview, per-field fill rates,
  repair loop with exact errors.
- Shared health-aware proxy pool across probe and agent; same rules in generated
  scrapers.
- Polite and unrestricted modes; disallowed paths stop before model spend.
- Generic record schema with an `attributes` bag; optional requested fields.
- Provider-neutral OpenAI-compatible client with presets (OpenAI, OpenRouter,
  Groq, Together, Ollama, custom); no vendor lock-in.
- FastAPI UI with SSE live log, file tabs, zip download, JSON API and OpenAPI
  docs.
- 60 tests, ruff, GitHub Actions CI.
- Agent interface: `AGENTS.md` plus `CLAUDE.md`, `GEMINI.md`, Copilot and Cursor
  pointers; non-interactive `scripts/build.py` (JSON + exit codes),
  `scripts/validate_bundle.py` and `scripts/scaffold_bundle.py`.
- Hardening from review: public-suffix domain guard (`tldextract`), Retry-After
  aware backoff, real HTML/JSON-LD fallback, working Mongo sink, JSON tool calls.
- Bundles now ship `requirements.txt`; API responses are normalised to
  `list[dict]` so `{"data": {"items": []}}` and scalar arrays cannot crash `run()`.
- Malformed `__NEXT_DATA__` now falls through to JSON-LD/card parsing instead of
  aborting the crawl with zero records.
