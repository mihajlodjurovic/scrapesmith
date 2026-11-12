# Design decisions

Short ADR-style notes. Each entry records the context, the choice, and what it
costs us.

## 1. Generate a code bundle, not a runtime recipe

**Context.** A declarative recipe (selectors in JSON) is safer to execute, but it
cannot express pagination edge cases, embedded-state parsing, custom
normalisation or the proxy/retry plumbing that real sites need.

**Decision.** The model emits four files: `scraper.py`, `mapper.py`,
`runner.sh`, `README.md`. The bundle is portable, reviewable and runs without
ScrapeSmith.

**Consequences.** Generated code needs sandboxing and static checks. In exchange,
the output is something a team can actually own, diff and schedule.

## 2. Verify the source before writing code

**Context.** One-shot generation guesses endpoints from a page dump. On
JavaScript shells it invents URLs that 404 or 403, and the first proof run fails.

**Decision.** The model gets a bounded `fetch` tool (six calls, same domain) and
must confirm a 2xx structured source. Discovery also proposes candidate siblings
(`.json`, `.rss`, `/feed`, `sitemap.xml`).

**Consequences.** A few extra requests and a slightly larger prompt; in return the
bundle is based on observed data. The trace is stored as `agent_trace.json`.

## 3. Validate statically before executing anything

**Context.** Model output can import `subprocess`, hardcode credentials or drop
schema fields. Executing first is both unsafe and expensive.

**Decision.** Parse with `ast`, reject forbidden imports/calls and secret
patterns, require the contract functions, and check `CANONICAL_KEYS` coverage
before the sandbox.

**Consequences.** Some legitimate code (e.g. a custom socket client) is rejected;
that is acceptable for a scraping tool.

## 4. Execute in a subprocess with a scrubbed environment

**Context.** The model's scraper must be proven, but it is untrusted code.

**Decision.** `subprocess.run` with a timeout, an environment allowlist, a
working directory per job and no API key. One page only.

**Consequences.** This is a guardrail, not a jail. Documented in `SECURITY.md`;
production use should run the sandbox in a dedicated container.

## 5. SQLite and files, not Postgres and object storage

**Context.** The tool is meant to be cloned and run in a minute.

**Decision.** One SQLite database for jobs/events, one directory per job for
artifacts.

**Consequences.** Single node only. The pipeline is written so the queue and
store can be swapped without touching the bundle contract.

## 6. The model key lives in memory only

**Context.** A local tool still handles someone's paid API key.

**Decision.** The key is passed into the job task, redacted in logs, never written
to SQLite, and the sandbox environment excludes it.

**Consequences.** Restarting the server loses nothing but the key context;
`SCRAPESMITH_API_KEY` in `.env` is the fallback.

## 7. Two politeness modes

**Context.** Users have different legal positions: some scrape their own sites,
some have written permission, some just want to study a public page.

**Decision.** `polite` (default) fetches and enforces robots.txt; `unrestricted`
ignores it with a warning stored on the job. Both refuse logins, CAPTCHAs and
private data.

**Consequences.** The default is defensible, and the escape hatch is explicit
rather than hidden.

## 8. A generic record schema with an `attributes` bag

**Context.** A car-specific schema is useless for books, jobs or articles, but a
fully free-form schema cannot be validated.

**Decision.** Eleven stable fields (`id`, `url`, `title`, `price`, `currency`,
`description`, `location`, `images`, `attributes`, `source`, `scraped_at`) plus a
free-form `attributes` object for domain fields.

**Consequences.** Cross-site analysis is possible on the stable fields; vertical
data stays available without schema migrations.

## 9. A provider-neutral OpenAI-compatible client

**Context.** Users bring different keys and endpoints. Hard-coding one vendor
ages badly and makes the tool look like a wrapper for that vendor.

**Decision.** One chat-completions client with a configurable base URL, model and
key, plus presets for common providers and a free-form custom option. The
default is `gpt-4.1-mini` on the OpenAI endpoint because it is widely available;
the model is the user's choice.

**Consequences.** Responses-API-only models need an adapter; documented as a
limitation. Local models that need no key (Ollama) are supported by leaving the
key empty.

## 10. No headless browser in v1 (superseded by 11)

**Context.** Playwright would cover JavaScript-heavy sites but adds hundreds of
megabytes, makes runs slow and non-deterministic, and weakens the "fast, polite"
story.

**Decision.** No browser. When a page is a shell, the agent looks for structured
siblings (RSS/JSON/API) and the limitation is stated in the UI and README.

**Consequences.** Some sites are out of reach, but every successful build is
reproducible and cheap. A browser fallback remains on the roadmap.

## 11. Browser fallback: capture the API first, render as a last resort

**Context.** Decision 10 left client-side rendered sites out of reach. Most of
them load their data from a JSON endpoint that the page calls after JavaScript
runs; a smaller number need a real browser every time (signed requests,
per-session tokens).

**Decision.** Playwright is an optional extra. The pipeline tries strategies in
cost order: plain HTTP (with the rendered page attached when the probe looks like
a shell), then a requests bundle that replays an XHR/fetch endpoint the browser
captured, and only then a Playwright bundle with the same contract. Every browser
request, including each redirect hop, is SSRF-checked in a route handler, because
Chromium does not send redirect hops back through Playwright routing.

**Consequences.** Browser builds are slower (seconds per page instead of
milliseconds), need a Chromium download, and get a longer proof timeout. In
exchange, most "JavaScript" sites still end up with a fast requests scraper, and
`off` reproduces the old behaviour exactly. Decision 10 is superseded.
