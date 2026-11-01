# Bundle contract

Every generated or hand-written bundle must satisfy this contract. It is what
`app/validate.py` checks and what the sandbox assumes.

## Required files

| File | Required | Purpose |
|---|---|---|
| `scraper.py` | yes | Fetch, parse, write raw JSONL |
| `mapper.py` | yes | Raw → canonical record schema |
| `runner.sh` | yes | Run both steps |
| `README.md` | yes | Source, assumptions, breakage notes |
| `probe.json` | generated | Discovery report (optional for hand-written bundles) |
| `agent_trace.json` | generated | Verified sources (optional) |
| `raw.jsonl`, `mapped.jsonl` | proof | Sample output from one page |

`proxies.json` is a runtime input and must never be committed or shipped.

## scraper.py

Must define:

```python
def request(session, pool, url): ...     # retries, proxy rotation, backoff
def fetch_page(session, pool, page): ... # one page → list[dict]
def parse_items(payload): ...            # payload → list[dict]
def run(max_pages, out_path, proxies_file, mongo): ...  # writes JSONL
def main(): ...
```

Also required:

- a `ProxyPool` class (or equivalent) and a `DELAY` constant (default 0.2 s);
- a `--max-pages`, `--out`, `--proxies` CLI, and optional `--mongo`;
- one JSON object per line in `--out`, no wrapper array;
- each raw item may carry `_meta` with `source`, `page`, `scraped_at`;
- API-first: JSON or embedded state before DOM parsing;
- no hardcoded credentials, no `verify=False`, no browser automation.

Rejected imports: `subprocess`, `socket`, `ctypes`, `pickle`, `shutil`,
`multiprocessing`. Rejected calls: `eval`, `exec`, `compile`, `os.system`,
`os.popen`.

## mapper.py

Must define a literal `CANONICAL_KEYS` list containing every required field, and:

```python
def map_listing(raw: dict, base_url: str = "") -> dict: ...
def run(in_path: str, out_path: str, base_url: str = "") -> int: ...
def main(): ...
```

CLI: `--in`, `--out`, optional `--base-url`.

Required keys (also in `app/reference/schema.json`):

```text
id, url, title, price, currency, description, location,
images, attributes, source, scraped_at
```

Rules:

- `price` is numeric or null; `currency` is separate;
- `images` is a list of absolute URLs;
- `attributes` is a flat dict for domain fields (`author`, `mileage`, `tags`, …);
- `id` is stable across runs (source id, URL, or a hash);
- `scraped_at` is ISO 8601 UTC;
- never invent values that are not on the page. Null is honest.

## runner.sh

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 scraper.py --max-pages "${MAX_PAGES:-5}" --out raw.jsonl --proxies proxies.json
python3 mapper.py --in raw.jsonl --out mapped.jsonl
```

## Sandbox proof

The app runs exactly:

```bash
python -m py_compile scraper.py mapper.py
python scraper.py --max-pages 1 --out raw.jsonl --proxies proxies.json
python mapper.py --in raw.jsonl --out mapped.jsonl
```

with a scrubbed environment (no API key), a 180 s timeout and the job directory
as cwd. Zero mapped rows means the bundle is not done.

## Runtime environment

| Variable | Default | Purpose |
|---|---|---|
| `SCRAPESMITH_DELAY` | `0.2` | Delay between requests (s) |
| `SCRAPESMITH_TIMEOUT` | `20` | Request timeout (s) |
| `SCRAPESMITH_RETRIES` | `3` | Attempts per request |
| `SCRAPESMITH_MAX_PAGES` | `5` | Page cap |
| `SCRAPESMITH_PROXY_COOLDOWN` | `90` | Proxy cooldown (s) |
| `SCRAPESMITH_PROXY_BLACKLIST_AFTER` | `5` | Consecutive failures before blacklist |
| `PROXIES_FILE` | `proxies.json` | Proxy list path |
| `MONGO_URI` | — | Optional sink |

## Definition of done

1. `python scripts/validate_bundle.py <dir>` exits 0.
2. One page produces at least one mapped record.
3. The bundle README names the data source and the main assumption.
4. No secrets, no `proxies.json`, no `data/` in git.
