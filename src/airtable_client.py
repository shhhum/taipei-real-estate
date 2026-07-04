"""Airtable dedup + insert.

Wraps `pyairtable`. Credentials come from the environment (see `.env.example`):
`AIRTABLE_TOKEN`, `AIRTABLE_BASE_ID`, `AIRTABLE_TABLE_ID`.

Targets the real "Taipei Venue Search" base — table "Listings". Dedup key is the
listing `Link` field. New rows are inserted with `Status="Unseen"` and
`Date Added=today` in the Taipei timezone.

The base has no dedicated columns for source / property_type / building_type /
layout / labels, so those are folded into the `Notes` body by `format_notes()`.
The client only writes the fields the daily search skill owns; user-curated
fields (`Rating`, `特殊`, `Phone`, ...), formulas, and auto fields are left alone.
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


def format_notes(listing: Listing) -> str:
    """Render the `Notes` body: a compact header block, blank line, description.

    The schema has no columns for source / property_type / building_type /
    layout / labels, so they live here instead of being dropped. Example::

        [source] 591 · [type] 店面 · [building] 電梯大樓 · [layout] OPEN
        [labels] 近捷運, 可登記, 繁華商圈

        <description, verbatim>

    Header entries with a None/empty value are skipped; a listing with no header
    values and no description yields an empty string.
    """
    header_parts: list[str] = []
    if listing.source:
        header_parts.append(f"[source] {listing.source}")
    if listing.property_type:
        header_parts.append(f"[type] {listing.property_type}")
    if listing.building_type:
        header_parts.append(f"[building] {listing.building_type}")
    if listing.layout:
        header_parts.append(f"[layout] {listing.layout}")

    header_lines: list[str] = []
    if header_parts:
        header_lines.append(" · ".join(header_parts))
    if listing.labels:
        header_lines.append(f"[labels] {', '.join(listing.labels)}")

    blocks: list[str] = []
    if header_lines:
        blocks.append("\n".join(header_lines))
    if listing.description:
        blocks.append(listing.description)

    return "\n\n".join(blocks)


def _listing_to_fields(listing: Listing) -> dict:
    """Map a `Listing` onto the real Airtable field names.

    Listing → Airtable::

        title       → Name
        (literal)   → Status ("Unseen")
        link        → Link
        rent_ntd    → Price (月)
        area_ping   → 坪數
        district    → 地區 (區 suffix stripped — base's canonical form)
        photo_url   → Photo ([{"url": ...}] or [] when absent)
        floor       → Floor ("" when None)
        address     → 地址
        (today)     → Date Added (Asia/Taipei, ISO)
        (folded)    → Notes (see `format_notes`)
    """
    return {
        "Name": listing.title,
        "Status": "Unseen",
        "Link": str(listing.link),
        "Price (月)": listing.rent_ntd,
        "坪數": listing.area_ping,
        "地區": listing.district.rstrip("區"),
        "Photo": [{"url": str(listing.photo_url)}] if listing.photo_url else [],
        "Floor": listing.floor or "",
        "地址": listing.address,
        "Date Added": _today_taipei(),
        "Notes": format_notes(listing),
    }


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
        returns the number of records actually inserted. Idempotent on re-run.
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
