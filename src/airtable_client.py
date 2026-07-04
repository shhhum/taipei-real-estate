"""Airtable dedup + insert.

Wraps `pyairtable`. Credentials come from the environment (see `.env.example`):
`AIRTABLE_TOKEN`, `AIRTABLE_BASE_ID`, `AIRTABLE_TABLE_ID`.

Dedup key is the listing `Link` field. New rows are inserted with
`Status="Unseen"` and `Date Added=today` in the Taipei timezone.
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pyairtable import Api

from src.models import Listing

load_dotenv()

TAIPEI_TZ = ZoneInfo("Asia/Taipei")
BATCH_SIZE = 10


def _today_taipei() -> str:
    """Today's date in Taipei, ISO formatted (YYYY-MM-DD) for Airtable."""
    return datetime.now(TAIPEI_TZ).date().isoformat()


def _listing_to_fields(listing: Listing) -> dict:
    """Map a `Listing` onto Airtable field names.

    Airtable → Listing:
      Link → link, Title → title, Rent → rent_ntd, Area (坪) → area_ping,
      Floor → floor, District → district, Location → address,
      Property Type → property_type, Photo → photo_url (as [{"url": ...}]),
      Building Type → building_type, Layout → layout, Notes → description,
      Labels → labels (comma-joined), Source → source,
      Date Added → today (Taipei), Status → "Unseen".
    """
    fields: dict = {
        "Link": str(listing.link),
        "Title": listing.title,
        "Rent": listing.rent_ntd,
        "Area (坪)": listing.area_ping,
        "District": listing.district,
        "Location": listing.address,
        "Property Type": listing.property_type,
        "Source": listing.source,
        "Date Added": _today_taipei(),
        "Status": "Unseen",
    }

    if listing.floor is not None:
        fields["Floor"] = listing.floor
    if listing.building_type is not None:
        fields["Building Type"] = listing.building_type
    if listing.layout is not None:
        fields["Layout"] = listing.layout
    if listing.description is not None:
        fields["Notes"] = listing.description
    if listing.photo_url is not None:
        fields["Photo"] = [{"url": str(listing.photo_url)}]
    if listing.labels:
        fields["Labels"] = ", ".join(listing.labels)

    return fields


class AirtableClient:
    def __init__(
        self,
        token: str | None = None,
        base_id: str | None = None,
        table_id: str | None = None,
    ) -> None:
        token = token or os.environ["AIRTABLE_TOKEN"]
        base_id = base_id or os.environ["AIRTABLE_BASE_ID"]
        table_id = table_id or os.environ["AIRTABLE_TABLE_ID"]
        self._table = Api(token).table(base_id, table_id)

    def existing_links(self) -> set[str]:
        """Return the set of `Link` values already present in the table."""
        links: set[str] = set()
        for record in self._table.all(fields=["Link"]):
            link = record.get("fields", {}).get("Link")
            if link:
                links.add(link)
        return links

    def insert_many(self, listings: list[Listing]) -> int:
        """Insert listings whose link is not already in the table.

        Dedups against `existing_links()`, batches inserts by `BATCH_SIZE`, and
        returns the number of records actually inserted.
        """
        existing = self.existing_links()

        new_records: list[dict] = []
        seen_this_run: set[str] = set()
        for listing in listings:
            link = str(listing.link)
            if link in existing or link in seen_this_run:
                continue
            seen_this_run.add(link)
            new_records.append(_listing_to_fields(listing))

        inserted = 0
        for start in range(0, len(new_records), BATCH_SIZE):
            batch = new_records[start : start + BATCH_SIZE]
            self._table.batch_create(batch)
            inserted += len(batch)

        return inserted
