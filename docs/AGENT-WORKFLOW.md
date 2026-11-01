# Agent workflow

How a coding agent should use this repository when the user says *"here is a
link, make me a scraper"*. `AGENTS.md` is the short entry point; this page is the
detailed playbook.

## 0. Check the environment

```bash
python scripts/smoke_llm.py
```

- Success: provider, model, key and a tiny completion. Continue.
- `No SCRAPESMITH_API_KEY`: ask the user for a key or a `.env` entry. Do not
  paste keys into source files.
- 401/403: the key or the model access is wrong; report it instead of retrying.

## 1. Generate first

```bash
python scripts/build.py "<url>" --json --out ./bundles/<slug>
```

Why first: the generator already does discovery, source verification, static
validation, a sandbox run and up to two repair rounds. Hand-writing should be the
fallback, not the default.

Read `bundles/<slug>/result.json`:

| Field | Meaning |
|---|---|
| `status` | `done` or `failed` |
| `records.mapped` | rows produced by the proof run |
| `fill_rates` | per-field coverage on the sample |
| `source` | `feed`, `json api`, `embedded state`, `json-ld`, `html` |
| `agent_trace` | every URL the model verified, with status and content type |
| `error` | the failing step, already redacted |
| `cost_usd` | provider-reported cost |

If `status` is `done` and `records.mapped > 0`, inspect the code and report.

## 2. Decide whether generation is viable

```text
Did discovery see repeated blocks, API candidates or embedded state?
├─ yes → generation should work; if it failed, read the error and repair
└─ no  → the page is likely a JS shell
         ├─ try a structured sibling: .json, .rss, /feed, sitemap
         ├─ if a sibling returns 2xx structured data, rerun or hand-write
         └─ if the page is a hard block, stop and report
```

`probe.json` has `block_markers`, `robots`, `signals` and `repeat_blocks`.
`agent_trace.json` shows what the model actually fetched.

## 3. Hand-write or repair

```bash
python scripts/scaffold_bundle.py ./bundles/<slug>
# edit scraper.py + mapper.py
python scripts/validate_bundle.py ./bundles/<slug> --json
cd ./bundles/<slug> && bash runner.sh
```

Rules of thumb:

- keep the reference plumbing and change only the site-specific functions;
- prefer embedded JSON/RSS over DOM selectors;
- make `id` stable and dedupe in the mapper;
- return `null` for missing fields instead of guessing;
- add the source and assumptions to the bundle README.

## 4. Politeness

`polite` is the default. If `robots.txt` disallows the path, the pipeline stops
before spending model tokens. Only use:

```bash
python scripts/build.py "<url>" --mode unrestricted
```

when the user explicitly says they own the site or have permission. Both modes
still refuse logins, CAPTCHA solving and private data.

## 5. Failure playbook

| Symptom | Check | Fix |
|---|---|---|
| `robots.txt` disallowed | `probe.json → robots` | Ask the user; do not override silently |
| No mapped records | sandbox stdout/stderr | Fix selectors, endpoint, or mapper |
| 403 during proof | `agent_trace.json` | Try a feed/JSON sibling or a proxy |
| Page is a JS shell | `probe.json → signals`, `repeat_blocks` | Use embedded state or a feed |
| Model returned no text | job log | Raise max tokens or lower reasoning effort |
| Bundle incomplete | job log | Rerun; the generator re-asks once |
| Provider 429 | log | Wait or switch model |

## 6. Report back

Give the user a short, verifiable summary:

```text
Built:  ./bundles/example (job 73551f4ebed9)
Source: html (20 repeated blocks)
Proof:  20/20 mapped, price 100%, title 100%
Cost:   $0.0066
Caveat: one page only; pagination untested.
```

Include the path to `result.json`. If the build failed, include the exact error
and the file to inspect next.
