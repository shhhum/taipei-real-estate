"""Tests for the 591 scraper.

The live test hits 591's undocumented API and is therefore opt-in: it only runs
when RUN_INTEGRATION=1 (and is capped via SITE_591_LIMIT so it stays polite).
Everything else is offline: it exercises the pure parsing/mapping helpers
against a captured detail-page fragment and a synthetic list item.
"""

import os

import pytest

from src.config import AREA_MAX_PING, AREA_MIN_PING
from src.models import Listing
from src.scrapers import site_591

# A trimmed but faithful fragment of a real business.591 detail page: the
# 用途 (住商用) / 型態 (公寓) label block, a 樓層 summary token, an og:image, and
# a description container.
_DETAIL_HTML = """
<html><head>
<meta property="og:image" content="https://img2.591.com.tw/house/x!730x460.water2.jpg">
<meta name="description" content="台北市中正區店面出租：租金58,000元/月，使用坪數15坪，位於中正區和平西路一段33巷，更多店面出租詳情，就在591房屋交易網。">
</head><body>
<div class="info-summary"><span>15 坪 使用坪數</span><span>1F / 4F 樓層</span></div>
<div class="label-container">
  <div class="label-item"><span class="label-name left">用途</span>
    <span class="purpose label-value left"><span class="length-limit">住商用</span></span></div>
  <div class="label-item"><span class="label-name left">型態</span>
    <span class="shape label-value left"><span class="length-limit">公寓</span></span></div>
  <div class="label-item"><span class="label-name left">權狀坪數</span>
    <span class="label-value left"><span class="length-limit">15坪</span></span></div>
</div>
<div class="house-condition-content">金店面長租可議，格局方正採光良好。</div>
</body></html>
"""

_LIST_ITEM = {
    "id": 21554857,
    "kind_name": "店面",
    "title": "和平西路/重慶南路15坪金店面長租可議",
    "url": "https://business.591.com.tw/rent/21554857",
    "price": "58,000",
    "area": 55,
    "area_name": "55坪",
    "layoutStr": "豪華裝潢",
    "address": "中正區-和平西路一段",
    "sectionid": "1",
    "regionid": "1",
    "tags": ["近捷運", "可餐飲"],
    "labels": [],
    "other": {"desc": "07-01發佈"},
    "photoList": ["https://img2.591.com.tw/house/a!710x388.jpg"],
}


def test_parse_detail_extracts_structured_fields():
    detail = site_591._parse_detail(_DETAIL_HTML)
    assert detail["purpose"] == "住商用"      # 用途 -> property_type (Rule 1)
    assert detail["shape"] == "公寓"          # 型態 -> building_type (Rule 7)
    assert detail["floor"] == "1F/4F"
    # Full address (incl. the 巷/弄 tail the list API drops) from the meta description.
    assert detail["address"] == "中正區和平西路一段33巷"
    assert detail["og_image"].endswith(".water2.jpg")
    assert "格局方正" in detail["description"]
    assert "權狀坪數:15坪" in detail["label_blob"]


def test_build_listing_maps_all_fields():
    detail = site_591._parse_detail(_DETAIL_HTML)
    listing = site_591._build_listing(_LIST_ITEM, detail)
    assert isinstance(listing, Listing)
    assert listing.source == "591"
    assert listing.listing_id == "21554857"
    assert str(listing.link) == "https://business.591.com.tw/rent/21554857"
    assert listing.rent_ntd == 58000
    assert listing.area_ping == 55.0
    assert listing.floor == "1F/4F"
    assert listing.district == "中正區"
    assert listing.address == "中正區和平西路一段33巷"   # detail beats truncated list address
    # property_type comes from 用途, building_type from 型態 — the fields the
    # filter rules (1 & 7) consume.
    assert listing.property_type == "住商用"
    assert listing.building_type == "公寓"
    assert listing.layout is None            # decor tag, not a room layout
    assert "近捷運" in listing.labels
    assert listing.posted_at is not None and listing.posted_at.month == 7


def test_build_listing_falls_back_to_kind_name_without_detail():
    listing = site_591._build_listing(_LIST_ITEM, {})
    assert listing is not None
    assert listing.property_type == "店面"   # falls back to kind_name
    assert listing.building_type is None
    assert listing.photo_url is not None      # from photoList
    assert listing.address == "中正區-和平西路一段"  # list address when no detail


def test_parse_rent_and_area_helpers():
    assert site_591._parse_rent("58,000") == 58000
    assert site_591._parse_rent("元/月 100,000") == 100000
    assert site_591._parse_rent(None) is None
    assert site_591._parse_area("55") == 55.0
    assert site_591._parse_area(None) is None


def test_pick_layout_only_surfaces_room_counts():
    assert site_591._pick_layout({"layoutStr": "豪華裝潢"}) is None
    assert site_591._pick_layout({"layoutStr": "3房2廳"}) == "3房2廳"
    assert site_591._pick_layout({"layoutStr": "OPEN"}) == "OPEN"


def test_looks_like_detail_rejects_stub_accepts_full_page():
    # A ~20KB blocked stub with neither the label block nor 樓層 is degraded.
    assert not site_591._looks_like_detail("<html><body>Too many requests</body></html>")
    # A short body is degraded even if it happens to contain a marker.
    assert not site_591._looks_like_detail("<div class='label-item'></div>")
    # A full-length page with the markers is accepted.
    full = "<div class='label-item'></div>樓層" + "x" * 40_000
    assert site_591._looks_like_detail(full)


class _FakeResp:
    def __init__(self, text: str, status: int = 200) -> None:
        self.text = text
        self.status_code = status

    def raise_for_status(self) -> None:  # 200s never raise
        pass


def test_get_rent_detail_retries_then_raises_on_persistent_stub(monkeypatch):
    client = site_591._Client591()
    calls = {"n": 0}

    def fake_get(url, timeout=25):
        calls["n"] += 1
        return _FakeResp("stub body", status=200)  # 200 but degraded

    monkeypatch.setattr(client._web, "get", fake_get)
    monkeypatch.setattr(site_591.time, "sleep", lambda *_a, **_k: None)

    with pytest.raises(site_591.requests.RequestException):
        client.get_rent_detail_html("123", retries=2)
    assert calls["n"] == 3  # initial attempt + 2 retries


def test_get_rent_detail_returns_valid_page(monkeypatch):
    client = site_591._Client591()
    good = "<div class='label-item'></div>樓層" + "x" * 40_000
    monkeypatch.setattr(client._web, "get", lambda url, timeout=25: _FakeResp(good))
    assert client.get_rent_detail_html("123") == good


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="live 591 API test; set RUN_INTEGRATION=1 (and SITE_591_LIMIT to cap) to run",
)
def test_fetch_live_shape():
    os.environ.setdefault("SITE_591_LIMIT", "3")
    listings = site_591.fetch()
    assert listings, "expected at least one live listing"
    for lst in listings:
        assert isinstance(lst, Listing)
        assert lst.source == "591"
        assert str(lst.link).startswith("https://business.591.com.tw/rent/")
        assert lst.rent_ntd > 0
        assert lst.area_ping > 0
        assert AREA_MIN_PING <= lst.area_ping <= AREA_MAX_PING
        assert lst.district.endswith("區")
