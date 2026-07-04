# taipei-real-estate

Scrapes Taipei commercial-real-estate listings from four sites, filters them
against a set of exclusion rules, and inserts the survivors into Airtable
(deduped by canonical URL).

This repo is the **foundation / interface layer**. The four scrapers and the
filter are stubs that `raise NotImplementedError` — sibling agents implement them
against the interfaces defined here.

## Layout

```
taipei-real-estate/
├── pyproject.toml          # uv + Python 3.12+, deps
├── README.md
├── .env.example            # AIRTABLE_TOKEN / AIRTABLE_BASE_ID / AIRTABLE_TABLE_ID
├── .gitignore
├── docs/
│   └── RULES.md            # ported filter rules — see below
├── src/
│   ├── config.py           # search filters (districts, price, area, property types)
│   ├── models.py           # Listing pydantic model — the shared contract
│   ├── airtable_client.py  # dedup + insert against Airtable
│   ├── main.py             # orchestrator: run 4 scrapers → filter → insert
│   ├── scrapers/
│   │   ├── site_591.py         # 591 租屋      (stub)
│   │   ├── site_sinyi.py       # 信義房屋      (stub)
│   │   ├── site_hbhousing.py   # 住商不動產    (stub)
│   │   └── site_yungching.py   # 永慶房屋      (stub, Playwright)
│   └── filters/
│       └── rules.py        # exclusion-rule filter (stub)
└── tests/
```

## Interfaces (the shared contract)

- **`src/models.py` → `Listing`** — the single data shape every layer passes
  around. Do not change field names/types without updating all consumers.
- **Scraper** — each `src/scrapers/site_X.py` exposes `fetch() -> list[Listing]`.
  No arguments; it reads the search space from `src/config.py` and returns
  **unfiltered** listings.
- **Filter** — `src/filters/rules.py` exposes
  `apply(listings) -> (accepted, [(rejected, reason), ...])`.
- **Airtable** — `src/airtable_client.AirtableClient`:
  - `existing_links() -> set[str]` — current `Link` field values (dedup source).
  - `insert_many(listings) -> int` — dedups against `existing_links()`, inserts
    new rows with `Status="Unseen"` and `Date Added=today` (Taipei tz), batch
    size 10; returns the count inserted.

## Filter rules

The exclusion rules (住辦 / 住宅 / bedroom-layout / industrial / basement /
透天厝 / 公寓 / shared-bathroom / price-area / district) are documented with their
exact regex patterns in **[docs/RULES.md](docs/RULES.md)**.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+.

```bash
uv sync                      # create .venv and install deps
cp .env.example .env         # then fill in your Airtable credentials
```

The 永慶 (Yung Ching) scraper uses Playwright; after `uv sync` its sibling agent
will run `uv run playwright install chromium`.

The 591 sibling scraper uses the optional `mcp-591` bridge:

```bash
uv sync --extra mcp
```

## Run

```bash
python -m src.main
```

Until the scrapers and filter are implemented, this runs without `ImportError`
but exits with `NotImplementedError` from the stubs — that's expected at the
foundation stage.

Output line on a full run:

```
scraped=<N> accepted=<N> rejected=<N> inserted=<N>
```

## Development

```bash
uv run ruff check .
uv run pytest
```
