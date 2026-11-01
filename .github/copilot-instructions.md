# Copilot instructions

Read `AGENTS.md` before making changes. Key points:

- This repo generates validated scraper + mapper bundles from one URL.
- Use `python scripts/build.py "<url>" --json --out ./bundles/<site>` to generate.
- Use `scripts/scaffold_bundle.py` and `scripts/validate_bundle.py` for manual work.
- `polite` mode is the default; do not bypass robots.txt without user permission.
- Never add API keys, proxy credentials, `.env`, or `data/` to git.
- The contract lives in `docs/BUNDLE-CONTRACT.md`; the decision tree in
  `docs/AGENT-WORKFLOW.md`.
