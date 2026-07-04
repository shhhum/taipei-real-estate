"""Orchestrator: run all four scrapers, filter, and insert into Airtable."""

from src.models import Listing


def main() -> None:
    from src.airtable_client import AirtableClient
    from src.filters import rules
    from src.scrapers import site_591, site_hbhousing, site_sinyi, site_yungching

    raw: list[Listing] = []
    for scraper in [site_591, site_sinyi, site_hbhousing, site_yungching]:
        try:
            raw.extend(scraper.fetch())
        except Exception as e:  # noqa: BLE001 - one bad scraper shouldn't sink the run
            print(f"[{scraper.__name__}] failed: {e}")

    accepted, rejected = rules.apply(raw)
    at = AirtableClient()
    inserted = at.insert_many(accepted)
    print(
        f"scraped={len(raw)} accepted={len(accepted)} "
        f"rejected={len(rejected)} inserted={inserted}"
    )


if __name__ == "__main__":
    main()
