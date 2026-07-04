"""Tests for the HB Housing scraper.

Unit tests run offline (URL grammar, parsing helpers). The live network test is
gated behind `RUN_INTEGRATION=1` so the default suite stays hermetic.
"""

import os

import pytest

from src.models import Listing
from src.scrapers import site_hbhousing as hb


# --- offline unit tests -----------------------------------------------------------

def test_search_url_pagination_format():
    """Page 1 has no suffix; page N>1 uses the `/{N}-page` path segment."""
    p1 = hb._search_url(1)
    p2 = hb._search_url(2)
    p3 = hb._search_url(3)
    assert p1.endswith("/area-35-70-area")
    assert not p1.endswith("-page")
    assert p2 == p1 + "/2-page"
    assert p3 == p1 + "/3-page"
    # sanity: the filter grammar is intact in the base path
    assert "shop-office-type" in p1
    assert "2.5-10-price" in p1
    assert "100-103-104-105-106-108-110" in p1


@pytest.mark.parametrize(
    "text,expected",
    [
        ("6.8 萬", 68_000),
        ("9.8 萬", 98_000),
        ("8.131 萬", 81_310),
        ("6 萬", 60_000),
        ("每月租金 12.5 萬 含管理費", 125_000),
        ("洽詢", None),
    ],
)
def test_parse_rent(text, expected):
    assert hb._parse_rent(text) == expected


@pytest.mark.parametrize(
    "address,expected",
    [
        ("台北市信義區市民大道六段", "信義"),
        ("台北市中山區長安東路二段", "中山"),
        ("台北市大安區復興南路一段", "大安"),
    ],
)
def test_parse_district(address, expected):
    assert hb._parse_district(address) == expected


def test_parse_detail_row():
    row = "大樓 | 開放式 | 4.3年 | 1樓/12樓 | 建坪 | 40.21坪"
    fields = hb._parse_detail_row(row)
    assert fields["building_type"] == "大樓"
    assert fields["layout"] == "開放式"
    assert fields["floor"] == "1F/12F"
    assert fields["area_ping"] == 40.21


def test_parse_property_type():
    assert hb._parse_property_type("信義JOYCE金店面", "低總價金店面") == "店面"
    assert hb._parse_property_type("松江南京優質設計商辦", "純辦大樓") == "辦公"
    assert hb._parse_property_type("中山國中捷運辦公2", "知名純辦大樓") == "辦公"


def test_clean_photo_strips_cachebuster_and_decodes_slash():
    raw = "https://img.hbhousing.com.tw/pictures/A550%2fA550ZS156038a.jpg?1783133087"
    assert hb._clean_photo(raw) == "https://img.hbhousing.com.tw/pictures/A550/A550ZS156038a.jpg"


def test_reported_total():
    html = '<p>共找到 <span class="x">21</span> 筆</p>'
    assert hb._reported_total(html) == 21
    assert hb._reported_total("<p>no total here</p>") is None


# --- live integration test --------------------------------------------------------

@pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="live network test; set RUN_INTEGRATION=1 to run",
)
def test_fetch_live():
    listings = hb.fetch()
    # HB Housing's commercial catalog for these filters is small (~20).
    assert len(listings) >= 5
    assert all(isinstance(x, Listing) for x in listings)

    # pagination worked: more than one page's worth of cards.
    assert len(listings) > 10, "only page 1 reachable — pagination regressed"

    # no duplicate listing IDs across pages.
    ids = [x.listing_id for x in listings]
    assert len(ids) == len(set(ids))

    for x in listings:
        assert x.source == "hbhousing"
        assert str(x.link).startswith("https://www.hbhousing.com.tw/detail?sn=")
        assert x.rent_ntd > 0
        assert x.area_ping > 0
        assert x.district
        assert x.address.startswith("台北市")
        assert x.property_type in ("店面", "辦公")
