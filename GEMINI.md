# GEMINI.md

Read `AGENTS.md` first. It is the source of truth for this repository.

Quick reference:

- Build a bundle: `python scripts/build.py "<url>" --json --out ./bundles/<site>`
- Hand-write/repair: `python scripts/scaffold_bundle.py <dir>` and
  `python scripts/validate_bundle.py <dir>`
- Keep `polite` mode unless the user explicitly permits otherwise.
- Never commit `.env`, `data/` or `proxies.json`.
- Success: validator exits 0, at least one mapped record, no secrets added.
