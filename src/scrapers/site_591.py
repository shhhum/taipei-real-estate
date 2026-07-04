"""591.com.tw scraper — API-based commercial-rental scraper for Taipei.

Ported from the open-source project https://github.com/asgard-ai-platform/mcp-591
(MIT licensed) — specifically the `Client591` HTTP client and its region/section/
kind ID mappings (`mcp_591/client.py`, `mcp_591/constants.py`). The MCP server
layer is not used; only the undocumented-API calls and response parsing are.

Target search space (see `src.config`):
  - region 台北市 (region_id=1)
  - 7 districts: 中正 大同 中山 松山 大安 萬華 信義
  - property kinds 店面 (kind=5) AND 辦公 (kind=6), fetched separately
  - rent band NT$25,000–100,000, applied at the API via `rentprice`
  - area band 35–70坪, post-filtered in code (the API ignores area filters for
    commercial listings — see "API surprises" below)

API surprises discovered while porting (documented for the next maintainer):
  1. Commercial rentals (店面/辦公) are served by the *residential* rent-list
     endpoint `v3/web/rent/list` when `kind` is a KINDS code (5/6). The list
     item already carries `area` (坪), `price`, `photoList`, `url`, `tags`, and
     `address`, so most of a Listing can be built without the detail page.
  2. The residential detail endpoint (`v2/web/rent/detail`) *rejects* commercial
     listings with `{"msg":"非住宅物件","isBusiness":1}`. The structured
     用途 / 型態 fields (needed by filter Rules 1 & 7) instead live in the
     server-rendered HTML at `https://business.591.com.tw/rent/{id}`, under
     `div.label-item` (label-name / label-value pairs). We parse that HTML.
  3. `area` / `acreage` query params are silently ignored by the commercial
     rent list (totals are unchanged), so the 35–70坪 band is applied in code
     against the list item's `area` value *before* fetching details — this keeps
     the number of detail requests polite (hundreds, not thousands).

`fetch()` returns Listings narrowed to the configured *search space* (district /
kind / rent / area) but NOT run through the content-exclusion rules — those
(住辦, 住宅, 公寓, 透天厝, …) are the filter module's job (`src.filters.rules`).
"""

from __future__ import annotations

import logging
import os
import random
import re
import time
import uuid
import warnings
from datetime import date

import requests
from bs4 import BeautifulSoup
from urllib3.exceptions import InsecureRequestWarning

from src.config import (
    AREA_MAX_PING,
    AREA_MIN_PING,
    RENT_MAX_NTD,
    RENT_MIN_NTD,
)
from src.models import Listing

logger = logging.getLogger(__name__)

# --- ID mappings ported from mcp-591 (mcp_591/constants.py) -----------------
# Region 台北市 and its 7 target districts. {name: section_id} and the reverse
# {section_id: "<name>區"} for populating Listing.district.
REGION_ID = 1
KINDS: dict[int, str] = {5: "店面", 6: "辦公"}
TAIPEI_SECTIONS: dict[str, int] = {
    "中正": 1,
    "大同": 2,
    "中山": 3,
    "松山": 4,
    "大安": 5,
    "萬華": 6,
    "信義": 7,
}
SECTION_DISTRICT: dict[int, str] = {sid: f"{name}區" for name, sid in TAIPEI_SECTIONS.items()}

# --- HTTP config ------------------------------------------------------------
# The list API is fronted by the mobile BFF; mimic the m.591 touch client
# (device headers + token cookie) exactly as mcp-591's Client591 does.
_MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Mobile Safari/537.36"
)
# The business detail pages are plain web HTML; use a desktop UA per the brief.
_DESKTOP_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

_RENT_LIST_URL = "https://bff-house.591.com.tw/v3/web/rent/list"
_BUSINESS_DETAIL_URL = "https://business.591.com.tw/rent/{id}"

_PAGE_SIZE = 24  # the BFF returns 24 items/page regardless of requested size
_MAX_PAGES = 200  # safety valve against pagination loops

# Optional dev/test cap on how many detail pages to fetch (0/unset = no cap).
_LIMIT_ENV = "SITE_591_LIMIT"

# 591's TLS cert is missing a Subject Key Identifier; requests warns loudly.
warnings.filterwarnings("ignore", category=InsecureRequestWarning)


class _Client591:
    """Minimal port of mcp-591's Client591, specialised for commercial rentals."""

    def __init__(self, device_id: str | None = None) -> None:
        self._device_id = device_id or uuid.uuid4().hex

        # Session for the JSON list API (mobile BFF).
        self._api = requests.Session()
        self._api.headers.update(
            {
                "user-agent": _MOBILE_UA,
                "device": "touch",
                "deviceid": self._device_id,
                "origin": "https://m.591.com.tw",
                "referer": "https://m.591.com.tw/",
            }
        )
        self._api.cookies.set("T591_TOKEN", self._device_id)
        self._api.verify = False

        # Session for the business detail HTML (desktop web).
        self._web = requests.Session()
        self._web.headers.update({"user-agent": _DESKTOP_UA})
        self._web.verify = False

    def search_rent(
        self,
        *,
        section_id: int,
        kind: int,
        first_row: int = 0,
        rentprice: str | None = None,
    ) -> dict:
        """One page of the commercial rent list for a (section, kind).

        `kind` is a KINDS code (5=店面, 6=辦公) — despite living on the
        residential rent-list endpoint. Returns the parsed `data` sub-object
        (with `items` and `total`).
        """
        params: dict = {
            "regionid": REGION_ID,
            "sectionid": section_id,
            "kind": kind,
            "firstRow": first_row,
            "timestamp": int(time.time() * 1000),
        }
        if rentprice is not None:
            params["rentprice"] = rentprice
        resp = self._api.get(_RENT_LIST_URL, params=params, timeout=25)
        resp.raise_for_status()
        return resp.json().get("data", {}) or {}

    def get_rent_detail_html(self, post_id: str | int, *, retries: int = 1) -> str:
        """Fetch the business detail page HTML for a commercial listing.

        The JSON detail endpoint rejects commercial listings ("非住宅物件"), so
        the structured 用途 / 型態 fields are read from this HTML instead. A
        transient failure here would silently drop those fields (leaving the
        filter rules unable to inspect 型態/用途), so retry once on error.
        """
        url = _BUSINESS_DETAIL_URL.format(id=post_id)
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = self._web.get(url, timeout=25)
                resp.raise_for_status()
                return resp.text
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < retries:
                    time.sleep(random.uniform(0.5, 1.0))
        raise last_exc  # type: ignore[misc]


# --- detail parsing ---------------------------------------------------------

def _parse_detail(html: str) -> dict:
    """Extract structured fields from a business.591 detail page.

    Returns a dict with keys: purpose (用途), shape (型態), floor, description,
    og_image, and label_blob (all 基礎資訊 label text joined, so the filter
    module can also match against fields we don't map 1:1).
    """
    soup = BeautifulSoup(html, "lxml")
    out: dict = {
        "purpose": None,
        "shape": None,
        "floor": None,
        "description": None,
        "og_image": None,
        "label_blob": None,
    }

    labels: dict[str, str] = {}
    for item in soup.select("div.label-item"):
        name_el = item.select_one(".label-name")
        val_el = item.select_one(".label-value")
        if not name_el or not val_el:
            continue
        name = name_el.get_text(strip=True)
        value = re.sub(r"\s+", " ", val_el.get_text(" ", strip=True)).strip()
        if name and value:
            labels[name] = value
    if labels:
        out["purpose"] = labels.get("用途")
        out["shape"] = labels.get("型態")
        out["label_blob"] = " ".join(f"{k}:{v}" for k, v in labels.items())

    # 樓層 lives in the top summary, not the label list, e.g. "1F / 4F 樓層".
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    m = re.search(r"([B0-9][B0-9F/~+\-\s]*?)樓層", text)
    if m:
        floor = re.sub(r"\s+", "", m.group(1)).strip("/-")
        out["floor"] = floor or None

    desc_el = soup.select_one(".house-condition-content") or soup.select_one(".descrip")
    if desc_el:
        out["description"] = re.sub(r"\s+", " ", desc_el.get_text(" ", strip=True)).strip() or None

    og = soup.select_one('meta[property="og:image"]')
    if og and og.get("content"):
        out["og_image"] = og["content"]

    return out


# --- field helpers ----------------------------------------------------------

def _parse_rent(price: object) -> int | None:
    if price is None:
        return None
    m = re.search(r"[\d,]+", str(price))
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _parse_area(area: object) -> float | None:
    try:
        return float(area)
    except (TypeError, ValueError):
        return None


def _parse_posted_at(item: dict) -> date | None:
    """Parse the list item's publish stamp, e.g. `{"desc": "07-01發佈"}`.

    591 gives month-day only. Assume the current year; if that lands in the
    future, roll back one year.
    """
    desc = ((item.get("other") or {}).get("desc")) or ""
    m = re.search(r"(\d{1,2})-(\d{1,2})", desc)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    today = date.today()
    try:
        d = date(today.year, month, day)
    except ValueError:
        return None
    if d > today:
        try:
            d = date(today.year - 1, month, day)
        except ValueError:
            return None
    return d


def _pick_photo(item: dict, detail: dict) -> str | None:
    photos = item.get("photoList") or []
    for p in photos:
        if isinstance(p, str) and p.startswith("http"):
            return p
    og = detail.get("og_image")
    if isinstance(og, str) and og.startswith("http"):
        return og
    return None


def _pick_layout(item: dict) -> str | None:
    """Commercial units are open-plan; only surface a layout if it names rooms.

    Keeps filter Rule 3 (`[1-9]+房`) meaningful without polluting the field with
    decor tags like `豪華裝潢` that 591 stuffs into `layoutStr`.
    """
    raw = item.get("layoutStr")
    if isinstance(raw, str) and re.search(r"\d+房|OPEN|開放", raw, re.IGNORECASE):
        return raw.strip()
    return None


def _build_listing(item: dict, detail: dict) -> Listing | None:
    listing_id = item.get("id")
    link = item.get("url")
    rent = _parse_rent(item.get("price"))
    area = _parse_area(item.get("area"))
    if listing_id is None or not link or rent is None or area is None:
        logger.warning("591: skipping listing with missing core fields: id=%s", listing_id)
        return None

    section_id = int(item.get("sectionid") or 0)
    district = SECTION_DISTRICT.get(section_id, "")

    tags = [t for t in (item.get("tags") or []) if isinstance(t, str)]
    labels = tags + [t for t in (item.get("labels") or []) if isinstance(t, str)]

    property_type = detail.get("purpose") or item.get("kind_name") or ""

    try:
        return Listing(
            source="591",
            listing_id=str(listing_id),
            link=link,
            title=item.get("title") or "",
            rent_ntd=rent,
            area_ping=area,
            floor=detail.get("floor"),
            district=district,
            address=(item.get("address") or "").strip(),
            property_type=property_type,
            photo_url=_pick_photo(item, detail),
            building_type=detail.get("shape"),
            layout=_pick_layout(item),
            description=detail.get("description"),
            labels=labels,
            posted_at=_parse_posted_at(item),
        )
    except Exception as exc:  # pydantic validation etc. — never crash the run
        logger.warning("591: failed to build Listing id=%s: %s", listing_id, exc)
        return None


# --- search-space collection ------------------------------------------------

def _collect_candidates(client: _Client591) -> list[dict]:
    """Paginate every (kind, section) and return area-filtered, deduped items.

    Applies the rent band at the API and the area band in code (the API ignores
    area filters for commercial listings). Details are NOT fetched here.
    """
    rentprice = f"{RENT_MIN_NTD}_{RENT_MAX_NTD}"
    seen: set[str] = set()
    candidates: list[dict] = []

    for kind in KINDS:
        for name, section_id in TAIPEI_SECTIONS.items():
            first_row = 0
            for _ in range(_MAX_PAGES):
                try:
                    data = client.search_rent(
                        section_id=section_id,
                        kind=kind,
                        first_row=first_row,
                        rentprice=rentprice,
                    )
                except requests.RequestException as exc:
                    logger.warning(
                        "591: list request failed kind=%s section=%s row=%s: %s",
                        kind, name, first_row, exc,
                    )
                    break

                items = data.get("items") or []
                if not items:
                    break
                try:
                    total = int(data.get("total") or 0)
                except (TypeError, ValueError):
                    total = 0

                for item in items:
                    area = _parse_area(item.get("area"))
                    if area is None or not (AREA_MIN_PING <= area <= AREA_MAX_PING):
                        continue
                    lid = str(item.get("id"))
                    if lid in seen:
                        continue
                    seen.add(lid)
                    candidates.append(item)

                first_row += len(items)
                if first_row >= total:
                    break

            logger.info("591: %s %s — %d candidates so far", KINDS[kind], name, len(candidates))

    return candidates


def fetch() -> list[Listing]:
    """Fetch all Taipei 店面 + 辦公 rentals in the configured filters.

    Returns unfiltered Listing objects (search-space filters applied; content
    exclusion rules left to `src.filters.rules`).
    """
    client = _Client591()
    candidates = _collect_candidates(client)
    logger.info("591: %d candidates after rent+area filter; fetching details", len(candidates))

    limit_raw = os.environ.get(_LIMIT_ENV)
    limit = int(limit_raw) if (limit_raw and limit_raw.isdigit() and int(limit_raw) > 0) else None
    if limit:
        logger.info("591: %s=%d — capping detail fetches for this run", _LIMIT_ENV, limit)
        candidates = candidates[:limit]

    listings: list[Listing] = []
    for i, item in enumerate(candidates):
        detail: dict = {}
        try:
            html = client.get_rent_detail_html(item["id"])
            detail = _parse_detail(html)
        except requests.RequestException as exc:
            logger.warning("591: detail fetch failed id=%s: %s — using list data only",
                           item.get("id"), exc)
        except Exception as exc:  # parsing surprises shouldn't kill the run
            logger.warning("591: detail parse failed id=%s: %s", item.get("id"), exc)

        listing = _build_listing(item, detail)
        if listing is not None:
            listings.append(listing)

        # Politeness: sleep between detail fetches (not after the last one).
        if i < len(candidates) - 1:
            time.sleep(random.uniform(0.5, 1.0))

    logger.info("591: built %d listings", len(listings))
    return listings


if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    results = fetch()
    print(f"\nTotal listings: {len(results)}")

    # Per-district breakdown.
    by_district: dict[str, int] = {}
    for lst in results:
        by_district[lst.district] = by_district.get(lst.district, 0) + 1
    print("Per-district:")
    for district in sorted(by_district):
        print(f"  {district}: {by_district[district]}")

    print("\nFirst 3 listings:")
    for lst in results[:3]:
        print(json.dumps(lst.model_dump(mode="json"), ensure_ascii=False, indent=2))
