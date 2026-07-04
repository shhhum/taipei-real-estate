# CLAUDE.md

Orientation for agents working in this repo from a fresh clone (e.g. a scheduled
Claude Code routine). Read this before doing anything else; the README covers the
same ground in more depth for humans.

## What this repo is

A daily search pipeline for Taipei **commercial** rental listings (storefront/office):
four site scrapers → exclusion-rule filter → Airtable insert, deduped by listing URL.
The search space (7 central districts, NT$25k–100k/month, 35–70 坪, 店面/辦公) is
fixed in `src/config.py`.

## The routine: an end-to-end run

```bash
uv run python -m src.main --dry-run   # scrape + filter only; report + dry_run_output.json
uv run python -m src.main             # live: scrape + filter + insert into Airtable
```

- A **live run** ends with one summary line: `scraped=N accepted=N rejected=N inserted=N`.
  Inserts are idempotent — re-running the same day just inserts 0 duplicates.
- Per-site progress is printed as it goes; one scraper failing does not abort the
  run (its error is caught and reported). Treat a run where *all four* sites
  return 0 or error as a failure worth investigating; a single site erroring is
  worth reporting but the run still counts.
- A full run takes on the order of 15–40 minutes, and the first 10–20 minutes are
  **completely silent**: the 591 scraper (which runs first) fetches each detail
  page with a politeness sleep and prints nothing until it finishes. A quiet
  process is not a hung process — don't kill it early; run it in the background
  and check back. `SITE_591_LIMIT=<n>` caps 591 detail fetches and
  `YUNGCHING_NO_ENRICH=1` skips Yungching enrichment for a faster smoke run.
- `scripts/run.ps1` is a Windows/PowerShell launcher for local use — on Linux
  containers, invoke `uv run python -m src.main` directly.

## Environment

- **Setup** is handled by the SessionStart hook (`.claude/hooks/session-start.sh`):
  `uv sync --extra dev` + a Chromium availability check. If it didn't run,
  execute it once yourself.
- **Airtable credentials** (`AIRTABLE_TOKEN`, `AIRTABLE_BASE_ID`,
  `AIRTABLE_TABLE_ID`) come from the remote environment's configured env vars
  (locally: a `.env` file, see `.env.example`). Live runs need them; dry runs don't.
- **Chromium**: only the Yungching scraper needs it. It automatically falls back
  to the container's pre-installed `$PLAYWRIGHT_BROWSERS_PATH/chromium` when
  Playwright's own revision is missing (see `_chromium_executable` in
  `src/scrapers/site_yungching.py`); `CHROMIUM_EXECUTABLE` overrides everything.
  Never run `playwright install` in the remote container.
- **Chromium + the egress proxy** needs two accommodations, both already
  handled: the scraper passes `HTTPS_PROXY` to the browser explicitly (Chromium
  ignores the env var), and the SessionStart hook installs an enterprise policy
  disabling post-quantum key agreement + ECH, whose ClientHellos the proxy's
  TLS inspection resets. If Playwright hits `net::ERR_CONNECTION_RESET`
  everywhere, check `/etc/chromium/policies/managed/tls-compat.json` exists.
- Knobs: `SITE_591_LIMIT` (cap 591 fetches), `YUNGCHING_NO_ENRICH=1`,
  `DRY_RUN=1` (same as `--dry-run`).
- **Transient egress flakiness is real**: the container's egress proxy has been
  observed to serve short windows of 503s / TLS connection resets that can fail
  one site's scrape. The run degrades gracefully (the site is reported in
  `ERRORS PER SITE` / the error log). If a site failed, re-running just that
  site later usually succeeds — inserts are deduped, so partial re-runs are safe.

## Layout & contracts

```
src/config.py            # search space — the single source of truth
src/models.py            # Listing (pydantic) — the shape every layer exchanges
src/main.py              # orchestrator + dry-run reporting
src/airtable_client.py   # dedup by Link + batched insert (Status="Unseen", Date Added=Taipei today)
src/scrapers/site_*.py   # each exposes fetch() -> list[Listing], returns UNFILTERED rows
src/filters/rules.py     # apply(listings) -> (accepted, [(rejected, reason), ...])
docs/RULES.md            # the 10 exclusion rules, with exact regexes
```

Site-specific scraping gotchas (pagination quirks, which field to trust for
property type) are documented in each scraper's module docstring and the README's
"Site-specific notes" section — read those before touching a scraper.

## Checks before committing

```bash
uv run ruff check .
uv run pytest            # unit tests are offline; integration tests opt in via RUN_INTEGRATION=1
```

Both must pass. Scraper parsing logic is covered by fixture-based unit tests —
if you change parsing, update the fixtures/tests in `tests/`.
