"""Orchestrator: run all four scrapers, filter, and insert into Airtable.

Supports a dry-run mode (``--dry-run`` flag or ``DRY_RUN=1`` env var) that runs
the full scrape + filter pipeline, prints a breakdown of the results, and dumps
everything to ``dry_run_output.json`` — without ever touching Airtable.
"""

import json
import os
import re
import sys
import time
from collections import Counter

from src.models import Listing

# Reason strings look like "Rule 3: bedroom layout (2房)". Strip the trailing
# "(...)" match detail so we can group by the rule family.
_REASON_DETAIL_RE = re.compile(r"\s*\([^()]*\)\s*$")

_DRY_RUN_OUTPUT = "dry_run_output.json"


def _run_scrapers() -> tuple[list[Listing], dict[str, int], dict[str, str]]:
    """Run every scraper, returning (all listings, per-site raw counts, errors)."""
    from src.scrapers import site_591, site_hbhousing, site_sinyi, site_yungching

    raw: list[Listing] = []
    raw_counts: dict[str, int] = {}
    errors: dict[str, str] = {}
    name_map = {
        "site_591": "591",
        "site_sinyi": "sinyi",
        "site_hbhousing": "hbhousing",
        "site_yungching": "yungching",
    }
    for scraper in [site_591, site_sinyi, site_hbhousing, site_yungching]:
        label = name_map.get(scraper.__name__.rsplit(".", 1)[-1], scraper.__name__)
        try:
            # Yungching's slow tail is per-listing detail enrichment; YUNGCHING_NO_ENRICH=1
            # runs a fast card-only crawl (no building_type/description).
            if scraper is site_yungching and os.environ.get("YUNGCHING_NO_ENRICH") == "1":
                found = scraper.fetch(enrich=False)
            else:
                found = scraper.fetch()
            raw.extend(found)
            raw_counts[label] = len(found)
            print(f"[{label}] fetched {len(found)} listings")
        except Exception as e:  # noqa: BLE001 - one bad scraper shouldn't sink the run
            errors[label] = f"{type(e).__name__}: {e}"
            raw_counts[label] = 0
            print(f"[{label}] FAILED: {type(e).__name__}: {e}")
    return raw, raw_counts, errors


def _dry_run() -> None:
    from src.filters import rules

    started = time.monotonic()
    raw, raw_counts, errors = _run_scrapers()

    accepted, rejected = rules.apply(raw)

    # Per-site accepted counts.
    accepted_by_site: Counter[str] = Counter(listing.source for listing in accepted)
    # Per-district accepted counts (strip the 區 suffix for display).
    accepted_by_district: Counter[str] = Counter(
        listing.district.rstrip("區") for listing in accepted
    )
    # Rejection reasons grouped by rule family.
    reject_reasons: Counter[str] = Counter(
        _REASON_DETAIL_RE.sub("", reason) for _, reason in rejected
    )

    elapsed_min = (time.monotonic() - started) / 60

    # ---- Report --------------------------------------------------------------
    print("\n" + "=" * 60)
    print("DRY RUN RESULTS")
    print("=" * 60)
    for label in ("591", "sinyi", "hbhousing", "yungching"):
        raw_n = raw_counts.get(label, 0)
        acc_n = accepted_by_site.get(label, 0)
        print(f"- {label}: raw={raw_n}, accepted={acc_n}")
    print(f"- TOTAL raw: {len(raw)}")
    print(f"- TOTAL accepted: {len(accepted)}")
    print(f"- TOTAL rejected: {len(rejected)}")

    print("\nTOP REJECTION REASONS:")
    for i, (reason, count) in enumerate(reject_reasons.most_common(10), 1):
        print(f"{i}. {reason}: {count}")

    print("\nPER-DISTRICT (accepted):")
    if accepted_by_district:
        print(
            ", ".join(
                f"{d}: {n}" for d, n in accepted_by_district.most_common()
            )
        )
    else:
        print("(none)")

    if errors:
        print("\nERRORS PER SITE:")
        for label, msg in errors.items():
            print(f"- {label}: {msg}")
    else:
        print("\nERRORS PER SITE: none")

    print(f"\nRuntime: {elapsed_min:.1f} minutes")

    # ---- JSON dump -----------------------------------------------------------
    dump = {
        "raw_counts": raw_counts,
        "errors": errors,
        "totals": {
            "raw": len(raw),
            "accepted": len(accepted),
            "rejected": len(rejected),
        },
        "accepted_by_site": dict(accepted_by_site),
        "accepted_by_district": dict(accepted_by_district),
        "rejection_reasons": dict(reject_reasons),
        "accepted": [json.loads(listing.model_dump_json()) for listing in accepted],
        "rejected": [
            {"reason": reason, "listing": json.loads(listing.model_dump_json())}
            for listing, reason in rejected
        ],
    }
    with open(_DRY_RUN_OUTPUT, "w", encoding="utf-8") as fh:
        json.dump(dump, fh, ensure_ascii=False, indent=2)
    print(f"\nWrote {_DRY_RUN_OUTPUT} ({len(accepted)} accepted, {len(rejected)} rejected)")


def _live_run() -> None:
    from src.airtable_client import AirtableClient
    from src.filters import rules

    raw, _raw_counts, _errors = _run_scrapers()
    accepted, rejected = rules.apply(raw)
    at = AirtableClient()
    inserted = at.insert_many(accepted)
    print(
        f"scraped={len(raw)} accepted={len(accepted)} "
        f"rejected={len(rejected)} inserted={inserted}"
    )


def main() -> None:
    dry_run = "--dry-run" in sys.argv or os.environ.get("DRY_RUN") == "1"
    if dry_run:
        _dry_run()
    else:
        _live_run()


if __name__ == "__main__":
    main()
