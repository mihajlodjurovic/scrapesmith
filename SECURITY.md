# Security

## Supported versions

Only the `main` branch is supported. This is a local-first tool, not a hosted
service.

## Reporting

Open a private security advisory on the repository (GitHub → Security →
Advisories). Please do not file a public issue for a vulnerability.

## Threat model

Three untrusted inputs reach the system:

1. **The target page.** It can be hostile: huge bodies, redirect loops, private
   IPs, misleading content types, anti-bot challenges.
2. **The model response.** It is untrusted code. It can import anything, read
   files, hardcode secrets, or loop forever.
3. **The user's API key.** It is a paid credential and must not leak into logs,
   the database, generated files or a downloaded zip.

## Controls

| Risk | Control |
|---|---|
| SSRF / internal network access | Hostname resolved before fetch; private, loopback, link-local, reserved and multicast addresses refused (`SCRAPESMITH_ALLOW_PRIVATE=1` opts out for local testing) |
| Oversized responses | 4 MB body cap, 25 s timeout, 5 redirects |
| Crawling disallowed paths | `robots.txt` parsed and enforced in `polite` mode |
| Generated code importing dangerous modules | AST rejects `subprocess`, `socket`, `ctypes`, `pickle`, `shutil`, `multiprocessing` |
| Generated code calling dangerous functions | AST rejects `eval`, `exec`, `compile`, `os.system`, `os.popen` |
| Credentials in generated code | Regex scan for Mongo URIs, API keys and proxy credentials; `verify=False` rejected |
| Key leakage | Key held in memory for the job, redacted from logs, never persisted, absent from the sandbox environment |
| Proxy leakage | `proxies.json` is excluded from the download zip |
| Runaway scraper | Subprocess with a 180 s timeout, output caps and a per-job working directory |
| Tool abuse | Fetch tool restricted to the target's registrable domain, six calls, small delay |

## Known limitations

- The sandbox is a **guardrail, not a jail**. Generated code can still make
  network requests and read files your user can read. Run ScrapeSmith in a
  container or VM if you do not trust the model output.
- The SSRF check resolves DNS once before the request. A determined attacker
  controlling DNS could rebind between the check and the connection.
- Rate limiting is per tool call and per job, not global per domain.
- There is no multi-tenant isolation: jobs share one SQLite database and one
  filesystem.
- The API key travels over plain HTTP to localhost by default. Do not expose the
  server publicly without TLS and authentication in front of it.

## Deployment guidance

- Bind to `127.0.0.1` unless you add authentication and TLS.
- Keep `.env` out of version control (already in `.gitignore`).
- Use a dedicated API key with a spending limit.
- If you deploy the demo publicly, disable job creation for anonymous users or
  put it behind an allowlist.
- Treat `data/` as sensitive: it contains scraped content and proxy lists.
