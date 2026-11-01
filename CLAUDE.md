# CLAUDE.md

@AGENTS.md

Claude Code: the imported file is the source of truth. The short version:

- Generate a bundle: `python scripts/build.py "<url>" --json --out ./bundles/<site>`
- Hand-write/repair: `python scripts/scaffold_bundle.py <dir>` then
  `python scripts/validate_bundle.py <dir>`
- Never switch to `--mode unrestricted` without user permission.
- Never commit `.env`, `data/` or `proxies.json`.
- Done means: validation exits 0, at least one mapped record, no secrets added.
