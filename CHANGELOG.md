# Changelog

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
