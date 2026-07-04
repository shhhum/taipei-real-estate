"""Tests for the Yungching scraper.

The pure field-parsing helpers are unit-tested unconditionally. The live crawl is
gated behind ``RUN_INTEGRATION=1`` because it launches Chromium and hits the
network (~3-5 min)::

    RUN_INTEGRATION=1 uv run pytest tests/test_yungching.py -s
"""

import os

import pytest

from src.config import DISTRICT_NAMES
from src.scrapers import site_yungching as yc


# --- pure parsing helpers (fast, always run) -------------------------------

@pytest.mark.parametrize("text,expected", [
    ("72,800", 72800),
    ("月租金 68,000元", 68000),
    ("100,000", 100000),
    ("", None),
    ("--", None),
])
def test_parse_int(text, expected):
    assert yc._parse_int(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("45.23坪", 45.23),
    ("35坪", 35.0),
    ("", None),
])
def test_parse_area(text, expected):
    assert yc._parse_area(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("5/14樓", "5F/14F"),
    ("10/11樓", "10F/11F"),
    ("B1~1/5樓", "B1~1F/5F"),  # basement hybrid preserved for filter Rule 5
    ("B1/5樓", "B1F/5F"),
    ("", None),
])
def test_parse_floor(text, expected):
    assert yc._parse_floor(text) == expected


@pytest.mark.parametrize("address,expected", [
    ("台北市士林區中山北路六段", "士林"),
    ("台北市大安區復興南路一段", "大安"),
    ("台北市萬華區成都路", "萬華"),
    ("新北市板橋區", None),
])
def test_parse_district(address, expected):
    assert yc._parse_district(address) == expected


def test_canonical_photo_rebuilds_full_size():
    src = "//yccdn.yungching.com.tw/v1/image/?key=ABC123&amp;width=480&amp;height=0"
    out = yc._canonical_photo(src)
    assert out == "https://yccdn.yungching.com.tw/v1/image/?key=ABC123&width=1024&height=768"


def test_canonical_photo_ignores_non_yccdn():
    assert yc._canonical_photo("https://example.com/x.jpg") is None
    assert yc._canonical_photo(None) is None


def test_card_to_listing_uses_real_purpose_not_filter():
    """property_type must reflect the card's .purpose, even when it's residential."""
    card = {
        "href": "house/2389409",
        "photo": "//yccdn.yungching.com.tw/v1/image/?key=K&width=480&height=0",
        "title": "三普安和四房",
        "address": "台北市大安區復興南路一段",
        "community": "三普",
        "purpose": "整層住家",  # the site returns residential rows under 店面,辦公
        "area": "50.72坪",
        "floor": "5/16樓",
        "layout": "4房(室)2廳2衛",
        "tags": ["永慶房屋", "近捷運", "有電梯"],
        "price": "68,000",
    }
    listing = yc._card_to_listing(card)
    assert listing is not None
    assert listing.source == "yungching"
    assert listing.listing_id == "2389409"
    assert str(listing.link) == "https://rent.yungching.com.tw/house/2389409"
    assert listing.rent_ntd == 68000
    assert listing.area_ping == 50.72
    assert listing.floor == "5F/16F"
    assert listing.district == "大安"
    assert listing.property_type == "整層住家"
    assert "永慶房屋" not in listing.labels  # agency name dropped
    assert listing.labels == ["近捷運", "有電梯"]


def test_card_to_listing_drops_unparseable():
    assert yc._card_to_listing({"href": "house/1", "price": "", "area": ""}) is None


# --- live integration crawl (gated) ----------------------------------------

@pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1",
                    reason="set RUN_INTEGRATION=1 to run the live Chromium crawl")
def test_fetch_live():
    listings = yc.fetch(enrich=False)  # card-only keeps it under ~1 min
    assert 30 <= len(listings) <= 300, f"unexpected count: {len(listings)}"

    targets = set(DISTRICT_NAMES)
    for listing in listings:
        assert listing.source == "yungching"
        assert listing.district in targets  # district pre-filter applied
        assert listing.rent_ntd > 0
        assert listing.area_ping > 0
        assert str(listing.link).startswith("https://rent.yungching.com.tw/house/")
