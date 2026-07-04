"""Tests for the 信義房屋 (Sinyi) scraper.

The integration test hits the live site and is gated on RUN_INTEGRATION=1 so the
default `pytest` run stays offline. Unit tests for the pure parsing helpers run
unconditionally.
"""

import os

import pytest

from src.config import DISTRICT_NAMES
from src.models import Listing
from src.scrapers import site_sinyi


# --- unit tests: pure helpers (no network) ----------------------------------

@pytest.mark.parametrize(
    "title,expected",
    [
        ("捷運延三金店面", "店面"),
        ("光南面寬一樓店辦", "店面"),
        ("南京三民站辦公大樓", "辦公"),
        ("民生東路有管理辦公", "辦公"),
        ("大安區優質空間", "辦公"),  # ambiguous -> default
    ],
)
def test_property_type(title, expected):
    assert site_sinyi._property_type(title) == expected


@pytest.mark.parametrize(
    "token,expected",
    [
        ("12/14", "12F/14F"),
        ("1/7", "1F/7F"),
        ("15/15", "15F/15F"),
        ("B1-1/5", "B1-1F/5F"),
        ("3", "3F"),
    ],
)
def test_parse_floor(token, expected):
    assert site_sinyi._parse_floor(token) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("台北市松山區南京東路五段", "松山"),
        ("中山大觀 / 台北市中山區民族東路", "中山"),
    ],
)
def test_parse_district(raw, expected):
    assert site_sinyi._parse_district(raw, fallback="XX") == expected


def test_parse_district_fallback():
    # No parseable 台北市…區 -> use the zip's known district.
    assert site_sinyi._parse_district("地址保留", fallback="信義") == "信義"


def test_clean_address():
    assert site_sinyi._clean_address("中山大觀 / 台北市中山區民族東路") == "台北市中山區民族東路"
    assert site_sinyi._clean_address("台北市大同區延平北路三段") == "台北市大同區延平北路三段"


def test_parse_date():
    d = site_sinyi._parse_date("2026/07/04 07:08")
    assert (d.year, d.month, d.day) == (2026, 7, 4)
    assert site_sinyi._parse_date(None) is None
    assert site_sinyi._parse_date("no date here") is None


# --- integration test: live site --------------------------------------------

@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="set RUN_INTEGRATION=1 to run")
def test_fetch_live():
    listings = site_sinyi.fetch()

    # The target search space returns roughly 60-100 commercial units.
    assert 20 <= len(listings) <= 200, f"unexpected count: {len(listings)}"

    ids = [x.listing_id for x in listings]
    assert len(ids) == len(set(ids)), "listing_ids must be unique"

    for x in listings:
        assert isinstance(x, Listing)
        assert x.source == "sinyi"
        assert str(x.link).startswith("https://www.sinyi.com.tw/rent/houseno/")
        assert x.property_type in ("店面", "辦公")
        assert x.district in DISTRICT_NAMES
        assert 25_000 <= x.rent_ntd <= 100_000
        assert 35 <= x.area_ping <= 70
