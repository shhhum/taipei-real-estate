# Taipei Real Estate Search Engine

Scrapes Taipei **commercial** real-estate rental listings from four sites, filters
them against a set of exclusion rules, and inserts the survivors into Airtable
(deduped by canonical URL).

The target search space is fixed in [`src/config.py`](src/config.py): seven central
Taipei districts (中正 / 大安 / 大同 / 萬華 / 中山 / 松山 / 信義), monthly rent
NT$25,000–100,000, area 35–70 坪, property types 店面 (storefront) and 辦公 (office).

> **This scraper is for personal use only.** It hits each site's public pages at a
> modest rate to build a private shortlist. Please respect each site's terms of
> service and do not use it for bulk redistribution or commercial data harvesting.

## Sites

| Source key   | Site                    | Transport                          |
| ------------ | ----------------------- | ---------------------------------- |
| `591`        | 591 租屋                | Undocumented JSON API + SSR HTML   |
| `sinyi`      | 信義房屋 (Sinyi)         | Server-rendered HTML               |
| `hbhousing`  | 住商不動產 (HB Housing)  | Nuxt SSR HTML                      |
| `yungching`  | 永慶房屋 (Yungching)     | Angular SPA via Playwright/Chromium |

## Prerequisites

- **Python 3.12+**
- [**uv**](https://docs.astral.sh/uv/) for dependency management
- **Chromium for Playwright** (only the Yungching scraper needs it):

  ```bash
  uv run playwright install chromium
  ```

## Setup

```bash
uv sync                       # create .venv and install dependencies
cp .env.example .env          # then fill in your Airtable credentials
uv run playwright install chromium
```

Fill `.env` with an Airtable [personal access token](https://airtable.com/create/tokens)
and the base/table you want to write to:

```
AIRTABLE_TOKEN=
AIRTABLE_BASE_ID=
AIRTABLE_TABLE_ID=
```

The 591 scraper works out of the box against the site's public API. An optional
`mcp` extra pulls in the upstream `mcp-591` package, but note it requires Python
**3.14+** on PyPI and is not needed for normal runs:

```bash
uv sync --extra mcp           # optional; needs a 3.14+ interpreter
```

## Running

Use the PowerShell launcher, which resolves the interpreter (`.venv`, falling back
to `uv run`), forces unbuffered UTF-8 output (so Chinese district names survive),
and streams stdout+stderr into a timestamped log under `runs/` (gitignored):

```powershell
./scripts/run.ps1 --dry-run   # scrape + filter, no Airtable writes
./scripts/run.ps1             # live run: scrape + filter + insert into Airtable
```

A **dry run** prints a per-site / per-district / per-rejection-reason breakdown and
dumps the full result set to `dry_run_output.json`. A **live run** prints a single
summary line and inserts new (deduped) rows into Airtable:

```
scraped=<N> accepted=<N> rejected=<N> inserted=<N>
```

Tail a run live from another terminal:

```powershell
Get-Content <log> -Wait -Tail 20
```

You can also invoke the pipeline directly: `uv run python -m src.main [--dry-run]`.

### Environment knobs

| Variable              | Effect                                                                                     |
| --------------------- | ------------------------------------------------------------------------------------------ |
| `SITE_591_LIMIT`      | Cap the number of 591 listings fetched (useful for quick dry runs).                        |
| `YUNGCHING_NO_ENRICH` | Set to `1` to skip per-listing Yungching detail enrichment — a fast card-only crawl that omits `building_type` / `description`. |
| `DRY_RUN`             | Set to `1` as an alternative to the `--dry-run` flag.                                       |

## Architecture

The pipeline is **four scrapers → filter → Airtable insert**, orchestrated by
[`src/main.py`](src/main.py). Every scraper returns a list of the shared `Listing`
model; the filter partitions them into accepted / rejected; the Airtable client
dedups and inserts the accepted set. One scraper failing does not sink the run —
its error is caught, logged, and reported.

```
src/
├── config.py            # search space: districts, rent band, area band, property types
├── models.py            # Listing pydantic model — the shared contract every layer passes around
├── main.py              # orchestrator: run 4 scrapers → filter → insert (+ dry-run reporting)
├── airtable_client.py   # dedup (by Link) + batched insert; Status="Unseen", Date Added=today (Taipei tz)
├── scrapers/
│   ├── site_591.py          # 591 租屋      (JSON API + SSR HTML)
│   ├── site_sinyi.py        # 信義房屋      (per-district SSR fetches)
│   ├── site_hbhousing.py    # 住商不動產    (Nuxt SSR, /{N}-page pagination)
│   └── site_yungching.py    # 永慶房屋      (Playwright/Chromium)
└── filters/
    └── rules.py         # 10 exclusion rules — see docs/RULES.md
```

**Contracts:**

- **`Listing`** ([`src/models.py`](src/models.py)) — the single shape every layer
  passes around. `link` is the canonical URL and Airtable dedup key.
- **Scraper** — each `src/scrapers/site_X.py` exposes `fetch() -> list[Listing]`;
  it reads the search space from `src/config.py` and returns *unfiltered* listings.
- **Filter** — `src/filters/rules.py` exposes
  `apply(listings) -> (accepted, [(rejected, reason), ...])`.
- **Airtable** — `AirtableClient.insert_many(listings)` dedups against the table's
  existing `Link` values and inserts the rest in batches of 10.

## Filter rules

Eleven hard-reject exclusion rules (住辦 / 住宅 / bedroom-layout / industrial /
basement / 透天厝 / 公寓 / shared-bathroom / price-area / district /
building-height ≤ 10F) are documented with their exact regex patterns in
**[docs/RULES.md](docs/RULES.md)**.

## Site-specific notes (for future maintainers)

Each scraper's module docstring carries the full detail; the load-bearing findings:

- **591** — the detail JSON endpoint refuses commercial listings, so 用途 / 型態
  must be scraped from the server-rendered HTML (the NUXT payload's
  `baseInfo.labelInfo`) rather than the API.
- **信義 (Sinyi)** — pagination is JS-only; a raw HTTP GET on any page number
  returns page 1 every time. The server *does* honour the `{zip}-zip` filter, so we
  issue one fetch per district and union the results (each target district holds
  ≤ 20 listings).
- **住商 (HB Housing)** — pagination is a **path suffix** `/{N}-page`, **not**
  `?page=N`. This form is server-rendered; `?page=` / `/page/N` all silently return
  page 1.
- **永慶 (Yungching)** — the commercial results mix 住宅 / 住辦 into the list, so
  `property_type` is read from each card's `.purpose` field rather than trusting the
  URL's use filter.

## Development

```bash
uv run ruff check .
uv run pytest                 # unit tests; integration tests opt in via RUN_INTEGRATION=1
```

## Credits

The 591 scraper is a direct port of the `Client591` HTTP client (and its
region/section/kind ID mappings) from
[**mcp-591**](https://github.com/asgard-ai-platform/mcp-591) by Asgard AI Platform,
which is MIT licensed. Only the undocumented-API calls and response parsing are
reused; the MCP server layer is not.

## License

MIT — see [LICENSE](LICENSE).
