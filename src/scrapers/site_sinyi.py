"""信義房屋 (Sinyi) scraper.

Sinyi is server-rendered: a plain HTTP GET on the search URL returns the first
page of listing cards as HTML. Two real-world quirks shape this module:

1. **Pagination is broken server-side.** The site's `/{N}-page/index.html`
   segment is honoured only by client-side JS; a raw GET on any page number
   returns page 1's 20 cards every time. But the server *does* honour the
   `{zip}-zip` filter. Since every target district holds <= 20 listings, we
   simply issue one request per district zip and union the results — full
   coverage without needing the (broken) page segment. If a district ever
   exceeds 20 we log a warning, because the tail is then unreachable via SSR.

2. **Flaky upstream.** Sinyi intermittently answers 200 with an
   "upstream connect error" body. `_get()` retries until it sees real HTML.

The list card carries most fields. `building_type`, `description` and extra
`labels` come from the detail page (`/rent/houseno/{code}`); enrichment failures
are logged and skipped, keeping the partial card.
"""

from __future__ import annotations

import logging
import random
import re
import time
from datetime import date

import requests
from bs4 import BeautifulSoup

from src.config import (
    AREA_MAX_PING,
    AREA_MIN_PING,
    DISTRICT_NAMES,
    DISTRICT_ZIPS,
    RENT_MAX_NTD,
    RENT_MIN_NTD,
)
from src.models import Listing

log = logging.getLogger(__name__)

_BASE = "https://www.sinyi.com.tw"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_HEADERS = {"User-Agent": _UA}
_PHOTO_TMPL = "https://res.sinyi.com.tw/rent/{code}/bigimg/A.JPG"

# The site renders at most this many cards per SSR response (pagination beyond
# it requires JS we can't drive here).
_PAGE_SIZE = 20

_session = requests.Session()
_session.headers.update(_HEADERS)


def _list_url(zip_code: str) -> str:
    """Search URL scoped to a single district zip (see module docstring)."""
    return (
        f"{_BASE}/rent/list/Taipei-city/{zip_code}-zip/"
        f"{RENT_MIN_NTD}-{RENT_MAX_NTD}-price/"
        f"{AREA_MIN_PING}-{AREA_MAX_PING}-area/"
        f"office-store-use/t2-t3-type/index.html"
    )


def _get(url: str, retries: int = 5) -> str | None:
    """GET `url`, retrying past Sinyi's intermittent upstream-error bodies.

    Returns real HTML, or None if every attempt failed.
    """
    for attempt in range(retries):
        try:
            resp = _session.get(url, timeout=30)
        except requests.RequestException as e:  # noqa: PERF203 - retry loop
            log.warning("GET %s failed (attempt %d): %s", url, attempt + 1, e)
            time.sleep(1.0 + attempt)
            continue
        body = resp.text
        if resp.status_code == 200 and len(body) > 2000 and "upstream connect error" not in body:
            return body
        log.warning(
            "GET %s returned bad body (status=%s len=%d, attempt %d)",
            url,
            resp.status_code,
            len(body),
            attempt + 1,
        )
        time.sleep(1.0 + attempt)
    log.error("GET %s: giving up after %d attempts", url, retries)
    return None


def _text(el) -> str | None:
    return el.get_text(" ", strip=True) if el else None


def _property_type(title: str) -> str:
    """Sinyi's `office-store-use` filter mixes 店面 and 辦公; disambiguate on
    the title, defaulting to 辦公 when ambiguous."""
    if any(kw in title for kw in ("店面", "店辦")) or "店" in title:
        return "店面"
    return "辦公"


def _parse_district(address: str, fallback: str) -> str:
    """`台北市松山區南京東路五段` -> `松山`. Falls back to the zip's district."""
    m = re.search(r"台北市\s*(\S{1,3}?)區", address)
    if m and m.group(1) in DISTRICT_NAMES:
        return m.group(1)
    return fallback


def _clean_address(raw: str) -> str:
    """Strip a leading community name: `中山大觀 / 台北市...` -> `台北市...`."""
    for part in re.split(r"\s*/\s*", raw):
        if "台北市" in part:
            return part.strip()
    return raw.strip()


def _parse_floor(token: str) -> str:
    """`12/14` -> `12F/14F`, `1/7` -> `1F/7F`, `B1-1/5` -> `B1-1F/5F`."""
    parts = [p for p in token.split("/") if p]
    return "/".join(f"{p}F" for p in parts) if parts else token


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", raw)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _parse_card(item, district_fallback: str) -> Listing | None:
    """Turn one `div.search_result_item` into a partial Listing (pre-enrichment).

    Returns None if the card is missing fields we can't do without.
    """
    code = item.get("id", "").replace("search_result_", "").strip()
    if not code:
        return None

    title = _text(item.select_one(".item_title")) or ""
    left = item.select_one(".detail_left")
    if left is None:
        log.warning("%s: no .detail_left, skipping", code)
        return None

    # First .detail_line2 holds "成屋 49.84 坪 12/14 樓 3房1廳1衛".
    lines = left.select(".detail_line2")
    spec = re.sub(r"\s+", " ", _text(lines[0]) or "") if lines else ""

    area_m = re.search(r"([\d.]+)\s*坪", spec)
    floor_m = re.search(r"坪\s*(\S+)\s*樓", spec)
    layout_m = re.search(r"樓\s*(.+)$", spec)

    if not area_m:
        log.warning("%s: no area in %r, skipping", code, spec)
        return None

    price_raw = _text(item.select_one(".detail_price .price_new .num")) or ""
    rent_m = re.search(r"[\d,]+", price_raw)
    if not rent_m:
        log.warning("%s: no rent in %r, skipping", code, price_raw)
        return None

    address_raw = _text(left.select_one(".num-ss .num-text")) or ""
    address = _clean_address(address_raw)

    card_tags = [_text(t) for t in left.select(".detail_tagGroup .detail_tag") if _text(t)]

    try:
        return Listing(
            source="sinyi",
            listing_id=code,
            link=f"{_BASE}/rent/houseno/{code}",
            title=title,
            rent_ntd=int(rent_m.group(0).replace(",", "")),
            area_ping=float(area_m.group(1)),
            floor=_parse_floor(floor_m.group(1)) if floor_m else None,
            district=_parse_district(address, district_fallback),
            address=address,
            property_type=_property_type(title),
            layout=layout_m.group(1).strip() if layout_m else None,
            labels=list(dict.fromkeys(card_tags)),
            posted_at=_parse_date(_text(left.select_one(".gray-date-1"))),
        )
    except Exception as e:  # noqa: BLE001 - one bad card shouldn't sink the batch
        log.warning("%s: failed to build Listing: %s", code, e)
        return None


def _enrich(listing: Listing) -> None:
    """Fetch the detail page and fill building_type, description, labels in place.

    Failures are logged and swallowed — the partial card survives.
    """
    html = _get(f"{_BASE}/rent/houseno/{listing.listing_id}", retries=3)
    if html is None:
        log.warning("%s: detail fetch failed, keeping partial data", listing.listing_id)
        return
    soup = BeautifulSoup(html, "lxml")

    # 建物型態: <li><span>型態：</span><p>大樓(11層含以上有電梯)</p></li>
    for li in soup.select("li"):
        span = li.find("span")
        if span and span.get_text(strip=True).startswith("型態"):
            p = li.find("p")
            if p:
                listing.building_type = p.get_text(strip=True)
            break

    feats = [
        re.sub(r"\s+", "", li.get_text(strip=True))
        for li in soup.select("ul.features-description li")
        if li.get_text(strip=True)
    ]
    env = [sp.get_text(strip=True) for sp in soup.select(".environment-title span") if sp.get_text(strip=True)]

    if feats:
        listing.description = "、".join(feats)
    listing.labels = list(dict.fromkeys([*listing.labels, *feats, *env]))


def _photo_url(code: str) -> str | None:
    """Verified Sinyi big-image URL, or None if it 404s."""
    url = _PHOTO_TMPL.format(code=code)
    try:
        resp = _session.head(url, timeout=15, allow_redirects=True)
    except requests.RequestException as e:
        log.warning("%s: photo HEAD failed: %s", code, e)
        return None
    if resp.status_code == 200 and resp.headers.get("Content-Type", "").startswith("image"):
        return url
    return None


def fetch() -> list[Listing]:
    """Return unfiltered Sinyi listings. Reads filters from `src.config`."""
    listings: list[Listing] = []
    seen: set[str] = set()

    for zip_code, name in zip(DISTRICT_ZIPS, DISTRICT_NAMES):
        html = _get(_list_url(zip_code))
        if html is None:
            log.error("district %s (%s): list fetch failed, skipping", name, zip_code)
            continue
        soup = BeautifulSoup(html, "lxml")

        count_el = soup.select_one("#search_result_count .num")
        count = int(count_el.get_text(strip=True)) if count_el and count_el.get_text(strip=True).isdigit() else None
        cards = soup.select("div.search_result_item")
        if count is not None and count > _PAGE_SIZE:
            log.warning(
                "district %s (%s): %d listings but SSR caps at %d — %d unreachable "
                "(pagination is JS-only)",
                name, zip_code, count, _PAGE_SIZE, count - len(cards),
            )

        for card in cards:
            listing = _parse_card(card, district_fallback=name)
            if listing is None or listing.listing_id in seen:
                continue
            seen.add(listing.listing_id)
            _enrich(listing)
            listing.photo_url = _photo_url(listing.listing_id)
            listings.append(listing)
            time.sleep(random.uniform(0.5, 1.0))  # politeness between detail fetches

        log.info("district %s (%s): %d cards", name, zip_code, len(cards))

    log.info("sinyi: %d listings total", len(listings))
    return listings


if __name__ == "__main__":
    import json

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    results = fetch()
    print(f"\nfetched {len(results)} listings\n")
    for r in results[:3]:
        print(json.dumps(r.model_dump(mode="json"), ensure_ascii=False, indent=2))
