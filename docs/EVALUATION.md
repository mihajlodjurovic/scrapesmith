# Evaluation

A small, reproducible field test of the pipeline. The goal is not a benchmark
record — it is evidence that the gates (verified source, contract, sandbox)
produce bundles that run on the first attempt across different page shapes.

## Method

| Item | Value |
|---|---|
| Date | 2026-10-31 |
| Model | `deepseek-v4.1-flash` on an OpenAI-compatible endpoint |
| Parameters | `temperature=0.2`, `reasoning_effort=low`, `max_tokens=16000` |
| Tool budget | 6 fetches, same registrable domain |
| Sandbox | 1 page, 180 s timeout, no API key in env |
| Repair budget | 2 rounds |
| Manual intervention | none |

Sites were chosen to span the shapes the tool claims to handle: server-rendered
HTML, a JavaScript shell with a feed, a real JSON API, and a site whose
`robots.txt` forbids crawling. Each build starts from a single URL.

## Browser fallback (2026-11-12)

Same model (`deepseek-v4.1-flash`), polite mode, `--browser auto`, 2 repair
rounds, no manual fixes. The two forced rows replace earlier strategies with a
simulated failure to exercise the later ones with the real model.

| Target | Shape | Strategy that won | Records | Cost | Rounds |
|---|---|---|---:|---:|---:|
| `books.toscrape.com` | static HTML (regression) | http | 20 / 20 | $0.0068 | 1 |
| `quotes.toscrape.com/scroll` | infinite scroll, XHR `/api/quotes` | http, replaying the captured API | 10 / 10 | $0.0056 | 1 |
| `quotes.toscrape.com/js/` | data in an inline JS variable | http, parsing the script | 10 / 10 | $0.0063 | 1 |
| local CSR test site (`tests/csr_site.py`) | empty `#root`, `fetch('/api/items')` | http, replaying the captured API | 8 / 8 | $0.0048 | 1 |
| local CSR test site, http forced to fail | same | browser (Playwright bundle) | 8 / 8 | $0.0069 | 1 |

On every client-side site the early render handed the model the endpoint the
page calls, so the cheap requests bundle won; the Playwright bundle is the last
resort and also passed on its first round when forced. Before the placeholder
fix in the secret scanner, `books.toscrape.com` needed two extra repair rounds
because the model's README showed `mongodb://user:pass@...` as an example.

## Results

| Target | Shape | Mode | Outcome | Records | Cost | Source chosen |
|---|---|---|---|---:|---:|---|
| `books.toscrape.com` | Static HTML list | polite | done | 20 / 20 | $0.0075 | HTML |
| `quotes.toscrape.com` | Static HTML, repeated blocks | polite | done | 10 / 10 | $0.0066 | HTML |
| Reddit thread (`r/AskSerbia`) | JS shell, 403 on JSON | unrestricted | done | 42 / 42 | $0.0070 | Atom feed |
| `jsonplaceholder.typicode.com/posts` | Plain JSON array | polite | done | 100 / 100 | $0.0078 | JSON |
| `autoscout24.com/lst` | SPA + `__NEXT_DATA__` | polite | stopped by policy | 0 | $0.0000 | — |
| `autoscout24.com/lst` | same page, explicit override | unrestricted | done | 20 / 20 | $0.0103 | embedded JSON |
| YouTube watch page | detail page, embedded JSON | polite | done | 1 / 1 | $0.0176 | embedded state |

Total model spend for the seven builds: **under $0.06**.

> Reddit was run in `unrestricted` mode purely to test feed discovery on a page
> whose `robots.txt` disallows crawlers. The default `polite` mode refuses that
> path; the override exists for sites you own or have written permission for.

### What the agent actually did

- **books / quotes**: discovery already found the repeated block (20 `li`, 10
  `div.quote`), so the model wrote the scraper directly with BeautifulSoup. No
  tool calls were needed.
- **Reddit**: `.json` returned 403, `.rss` returned 200 with a 42-entry Atom
  document, `old.reddit.com` and `api.reddit.com` returned 403. The model chose
  the Atom feed and documented why in the scraper docstring. Four tool calls,
  trace saved.
- **jsonplaceholder**: the response was `application/json`, the model used the
  JSON array directly and skipped HTML parsing entirely.
- **autoscout24**: discovery succeeded (20 listing cards, embedded
  `__NEXT_DATA__`, 2 API candidates) but `robots.txt` disallows the path. In
  polite mode the pipeline stopped before any model call — the intended
  behaviour, and the reason cost is $0.0000. The same URL, run explicitly in
  `unrestricted` mode, produced 20/20 records for $0.0103 from the embedded
  JSON, which shows the policy gate and the override are independent.
- **YouTube**: the probe found `ytInitialPlayerResponse` and `ytInitialData`.
  Because the signals (short snippets around each payload) were already in the
  report, the model needed **zero tool calls**, parsed both blobs and used
  JSON-LD `VideoObject` as a fallback. 120.8 s end to end, of which 2.1 s
  discovery, ~112 s generation and 6.4 s sandbox.

## Quality signals

For the successful bundles:

| Check | Result |
|---|---|
| Static contract violations | 0 |
| Bundle completeness on first reply | 6/6 builds |
| Sandbox attempts used | 1 per build |
| `pytest` suite | 60 tests, no network |
| Generated code includes `ProxyPool`, `DELAY`, retries | yes |
| Hardcoded credentials | none |
| Fill rate, `title` / `url` / `id` | 100% |
| Fill rate, `price` | 100% where the page has a price |
| Fill rate, fields absent from the source | 0% (reported honestly) |

The Reddit bundle reports `score` and `comment_count` as `null` because the Atom
feed does not expose them. That is the desired behaviour: a missing field is
visible, not invented.

## Threats to validity

- **Sample size.** Five targets, one model, one day. This is a smoke evaluation,
  not a benchmark.
- **Site drift.** Public pages change; a bundle that passes today can fail next
  month. The sandbox run is a point-in-time proof, not a guarantee.
- **One model family.** Costs and quality are specific to
  `deepseek-v4.1-flash`. Reasoning-heavy models need the low-effort setting and
  cost more.
- **No human baseline.** We did not compare against a developer writing the same
  scraper from scratch. The claim is "runs without manual fixes", not "faster
  than a senior engineer".
- **Network variance.** Proxies were not used. Anti-bot behaviour from a
  different IP range may differ.

## Reproducing

```bash
pytest -q

python scripts/run_build.py "https://books.toscrape.com/" \
    --fields "title, price, availability, rating"

python scripts/run_build.py "https://quotes.toscrape.com/" \
    --fields "author, tags"

python scripts/run_build.py "https://jsonplaceholder.typicode.com/posts" \
    --fields "title, body, userId"

# Only for sites you have permission to crawl:
python scripts/run_build.py "<url>" --mode unrestricted
```

Results appear under `data/jobs/<id>/` together with `probe.json`,
`agent_trace.json`, `raw.jsonl` and `mapped.jsonl`.

## Next measurements

- 20+ sites per shape, including pagination and detail pages
- a second model family (GLM-5.3, Kimi K3) at equal settings
- human review of generated code by an engineer who did not build the tool
- failure injection: remove a selector, change a field name, serve a 429
