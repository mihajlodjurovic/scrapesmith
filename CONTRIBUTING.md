# Contributing

Thanks for taking a look. This project values small, verifiable changes over
large rewrites.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env             # add SCRAPESMITH_API_KEY for real builds
uvicorn app.main:app --reload
```

## Before you open a PR

```bash
ruff check .
pytest -q
```

Both must pass. Tests never touch the network and never call a model; if your
change needs a live request, mock it or add a fixture.

## What to contribute

- **New provider preset** — add it to `PROVIDER_PRESETS` in `app/config.py` and
  to the README table. Keep the base URL OpenAI-compatible.
- **New validation rule** — add it to `app/validate.py`, plus a test that proves
  the rejection and one that proves the reference bundle still passes.
- **Better discovery signals** — `app/discovery.py`. Follow the existing pattern:
  deterministic, JSON-serialisable, no model calls.
- **Prompts and templates** — `app/prompt.py` and `app/reference/`. If you change
  the contract, update `tests/test_validate.py` and the schema together.
- **UI** — vanilla JS and CSS, no build step. Keep the dark amber theme and the
  accessibility basics (focus styles, labels, reduced motion).

## Code style

- Python 3.11+, type hints where they add clarity, `ruff` line length 100.
- No secrets, ever. Use environment variables or `proxies.json`.
- Generated code is a product artifact: keep the reference bundle readable and
  comment the parts that are easy to break.

## Tests

| File | Covers |
|---|---|
| `test_proxies.py` | Every proxy format we accept |
| `test_discovery.py` | API candidates, embedded state, repeated blocks, skeleton |
| `test_validate.py` | Each rejection path and the reference bundle |
| `test_generator.py` | Model reply parsing, prompts, candidate URLs |
| `test_agent.py` | Tool protocol, site guard, verify-then-write loop |
| `test_sandbox.py` | Subprocess run, preview, fill rates, failure tails |
| `test_pipeline.py` | Statuses, robots gate, repair loop, cost, redaction |
| `test_api.py` | Job creation, key handling, file access, download |
| `test_cli.py` | Agent helpers: build result shape, validation, scaffolding |

## Commit style

Imperative, scoped, one idea per commit:

```
Add RSS sibling to candidate list
Fix block-marker log crash and cover it with a test
```

## Reporting a bug

Include the job id, the URL shape (redact anything private), the model id, and
the last 30 lines of the live log. `probe.json` and `agent_trace.json` from the
job directory are usually enough to reproduce a failure.
