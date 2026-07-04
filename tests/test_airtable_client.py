"""Tests for the Airtable client (`src.airtable_client`).

pyairtable is fully mocked — nothing here touches the real base. The one live
smoke test is marked ``@pytest.mark.integration`` and skipped by default.
"""

import os
from unittest.mock import MagicMock

import pytest

from src.airtable_client import (
    AirtableClient,
    _listing_to_fields,
    _today_taipei,
    format_notes,
)
from src.models import Listing


def make_listing(**overrides) -> Listing:
    """A fully-populated baseline listing; override individual fields per test."""
    base = dict(
        source="591",
        listing_id="abc123",
        link="https://rent.591.com.tw/abc123",
        title="優質店面出租",
        rent_ntd=50_000,
        area_ping=50.0,
        floor="1F/12F",
        district="大安區",
        address="台北市大安區xx路1號",
        property_type="店面",
        building_type="電梯大樓",
        layout="OPEN",
        description="採光佳，適合開店",
        labels=["近捷運", "可登記", "繁華商圈"],
        photo_url="https://img.591.com.tw/photo/abc123.jpg",
    )
    base.update(overrides)
    return Listing(**base)


def make_client() -> AirtableClient:
    """An AirtableClient whose pyairtable table is replaced with a MagicMock."""
    client = AirtableClient(token="fake", base_id="appFAKE", table_id="tblFAKE")
    client._table = MagicMock()
    return client


# --------------------------------------------------------------------------- #
# field mapping
# --------------------------------------------------------------------------- #


def test_field_mapping_full():
    listing = make_listing()
    fields = _listing_to_fields(listing)

    assert fields["Name"] == "優質店面出租"
    assert fields["Status"] == "Unseen"
    assert fields["Link"] == "https://rent.591.com.tw/abc123"
    assert fields["Price (月)"] == 50_000
    assert fields["坪數"] == 50.0
    assert fields["Floor"] == "1F/12F"
    assert fields["地址"] == "台北市大安區xx路1號"
    assert fields["Photo"] == [{"url": "https://img.591.com.tw/photo/abc123.jpg"}]
    assert fields["Date Added"] == _today_taipei()
    assert "Notes" in fields


def test_status_is_always_unseen():
    # even if some upstream field looked like a status, we always write "Unseen"
    fields = _listing_to_fields(make_listing())
    assert fields["Status"] == "Unseen"


def test_district_suffix_is_stripped():
    assert _listing_to_fields(make_listing(district="大安區"))["地區"] == "大安"
    assert _listing_to_fields(make_listing(district="中正區"))["地區"] == "中正"
    # already-normalized form is left untouched
    assert _listing_to_fields(make_listing(district="信義"))["地區"] == "信義"


def test_client_never_writes_curated_fields():
    """The client must not touch user-curated / formula / auto fields."""
    fields = _listing_to_fields(make_listing())
    forbidden = {
        "ID",
        "每坪價格",
        "Rating",
        "Viewing Time",
        "Viewing Time End",
        "Phone",
        "Contact Name",
        "Google Calendar Event",
        "特殊",
    }
    assert forbidden.isdisjoint(fields)


def test_no_legacy_field_names():
    """Guard against the old scaffold field names sneaking back in."""
    fields = _listing_to_fields(make_listing())
    legacy = {"Title", "Rent", "Area (坪)", "District", "Location",
              "Property Type", "Source", "Building Type", "Layout", "Labels"}
    assert legacy.isdisjoint(fields)


# --------------------------------------------------------------------------- #
# Photo attachment
# --------------------------------------------------------------------------- #


def test_photo_present():
    fields = _listing_to_fields(make_listing())
    assert fields["Photo"] == [{"url": "https://img.591.com.tw/photo/abc123.jpg"}]


def test_photo_absent_is_empty_list_not_none():
    fields = _listing_to_fields(make_listing(photo_url=None))
    assert fields["Photo"] == []
    assert fields["Photo"] is not None


# --------------------------------------------------------------------------- #
# Floor
# --------------------------------------------------------------------------- #


def test_floor_none_becomes_empty_string():
    fields = _listing_to_fields(make_listing(floor=None))
    assert fields["Floor"] == ""


# --------------------------------------------------------------------------- #
# format_notes
# --------------------------------------------------------------------------- #


def test_format_notes_full():
    notes = format_notes(make_listing())
    expected = (
        "[source] 591 · [type] 店面 · [building] 電梯大樓 · [layout] OPEN\n"
        "[labels] 近捷運, 可登記, 繁華商圈\n"
        "\n"
        "採光佳，適合開店"
    )
    assert notes == expected


def test_format_notes_skips_missing_headers():
    listing = make_listing(building_type=None, layout=None, labels=[])
    notes = format_notes(listing)
    assert notes == "[source] 591 · [type] 店面\n\n採光佳，適合開店"
    assert "[building]" not in notes
    assert "[layout]" not in notes
    assert "[labels]" not in notes


def test_format_notes_no_description():
    listing = make_listing(description=None)
    notes = format_notes(listing)
    # header block only, no trailing blank line / description
    assert notes == (
        "[source] 591 · [type] 店面 · [building] 電梯大樓 · [layout] OPEN\n"
        "[labels] 近捷運, 可登記, 繁華商圈"
    )


def test_format_notes_only_description():
    listing = make_listing(
        source="", property_type="", building_type=None, layout=None, labels=[]
    )
    assert format_notes(listing) == "採光佳，適合開店"


def test_format_notes_all_empty():
    listing = make_listing(
        source="",
        property_type="",
        building_type=None,
        layout=None,
        labels=[],
        description=None,
    )
    assert format_notes(listing) == ""


# --------------------------------------------------------------------------- #
# existing_links
# --------------------------------------------------------------------------- #


def test_existing_links_extracts_link_values():
    client = make_client()
    client._table.all.return_value = [
        {"id": "rec1", "fields": {"Link": "https://a.example/1"}},
        {"id": "rec2", "fields": {"Link": "https://b.example/2"}},
        {"id": "rec3", "fields": {}},           # row with no Link — ignored
    ]
    assert client.existing_links() == {
        "https://a.example/1",
        "https://b.example/2",
    }
    client._table.all.assert_called_once_with(fields=["Link"])


# --------------------------------------------------------------------------- #
# insert_many
# --------------------------------------------------------------------------- #


def test_insert_many_filters_existing_duplicates():
    client = make_client()
    client._table.all.return_value = [
        {"id": "rec1", "fields": {"Link": "https://rent.591.com.tw/dup"}},
    ]

    listings = [
        make_listing(link="https://rent.591.com.tw/dup"),   # already in table
        make_listing(link="https://rent.591.com.tw/new"),   # new
    ]
    inserted = client.insert_many(listings)

    assert inserted == 1
    client._table.batch_create.assert_called_once()
    (batch,), _ = client._table.batch_create.call_args
    assert len(batch) == 1
    assert batch[0]["Link"] == "https://rent.591.com.tw/new"


def test_insert_many_dedupes_within_the_batch():
    client = make_client()
    client._table.all.return_value = []

    listings = [
        make_listing(link="https://rent.591.com.tw/same"),
        make_listing(link="https://rent.591.com.tw/same"),
    ]
    assert client.insert_many(listings) == 1


def test_insert_many_nothing_new_skips_create():
    client = make_client()
    client._table.all.return_value = [
        {"id": "rec1", "fields": {"Link": "https://rent.591.com.tw/abc123"}},
    ]
    assert client.insert_many([make_listing()]) == 0
    client._table.batch_create.assert_not_called()


def test_insert_many_batches_by_ten():
    client = make_client()
    client._table.all.return_value = []

    listings = [
        make_listing(link=f"https://rent.591.com.tw/{i}") for i in range(23)
    ]
    inserted = client.insert_many(listings)

    assert inserted == 23
    # 23 records → batches of 10, 10, 3
    sizes = [len(call.args[0]) for call in client._table.batch_create.call_args_list]
    assert sizes == [10, 10, 3]


# --------------------------------------------------------------------------- #
# live smoke test — gated on RUN_INTEGRATION=1 like the scraper suites
# --------------------------------------------------------------------------- #


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="live Airtable test; set RUN_INTEGRATION=1 (and real AIRTABLE_* env) to run",
)
def test_live_existing_links():
    """Hits real Airtable. Run explicitly: RUN_INTEGRATION=1 pytest -m integration."""
    client = AirtableClient()
    assert isinstance(client.existing_links(), set)
