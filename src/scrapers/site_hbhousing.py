"""住商不動產 (HB Housing) scraper.

HB Housing is a Nuxt SPA, but the listing cards are server-side rendered into the
initial HTML, so a plain `requests` GET returns full card data — no browser needed.

Pagination
----------
This site's pagination burned prior runs: the SSR always renders page 1 regardless
of `?page=`, `/page/2`, `/2`, etc. Reading the client bundle
(`_nuxt/CsYP6V7j.js`) reveals the route builder:

    return e.page && e.page > 1 && (t += `/${e.page}-page`), t.replace(/\\/$/, "")

i.e. the page number is a **path suffix segment** `/{N}-page` (page 1 has no
suffix). So page 2 is:

    .../area-35-70-area/2-page

This format IS server-rendered — GET-ing it returns page 2's ten cards. We loop
`/{N}-page` until a page yields no new cards (or we've seen the reported total).
"""

from __future__ import annotations

import re
import time
import urllib.parse

import requests
from bs4 import BeautifulSoup

from src.config import AREA_MAX_PING, AREA_MIN_PING
from src.models import Listing

# --- Search space (mirrors src.config; HB Housing uses its own URL grammar) -------
_BASE = "https://www.hbhousing.com.tw"
# 7 target districts by zip: 100中正 103大同 104中山 105松山 106大安 108萬華 110信義
_ZIPS = "100-103-104-105-106-108-110"
_STYLE = "elevator-other-style"          # 電梯大樓 + 其他 (excludes 公寓/透天)
_PRICE = "2.5-10-price"                   # 25k–100k, in units of 萬 (10k)
_TYPE = "shop-office-type"                # 店面 + 辦公
_AREA = f"area-{AREA_MIN_PING}-{AREA_MAX_PING}-area"  # 35–70 坪
_SEARCH_PATH = (
    f"/renthouse/{urllib.parse.quote('台北市')}/{_ZIPS}/{_STYLE}/{_PRICE}/{_TYPE}/{_AREA}"
)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

_MAX_PAGES = 50          # hard safety cap; the real catalog is a handful of pages
_DETAIL_SLEEP = 0.7      # seconds between detail-page fetches (politeness)
_TIMEOUT = 30


def _search_url(page: int) -> str:
    """Page 1 has no suffix; page N>1 appends the `/{N}-page` path segment."""
    suffix = "" if page <= 1 else f"/{page}-page"
    return _BASE + _SEARCH_PATH + suffix


def _clean(text: str) -> str:
    """Collapse whitespace and drop zero-width chars / UI affordance labels."""
    text = text.replace("​", "").replace("‎", "")
    text = re.sub(r"看地圖|看格局圖", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_rent(card_text: str) -> int | None:
    """`6.8 萬` -> 68000, `8.131 萬` -> 81310, `6 萬` -> 60000."""
    m = re.search(r"([\d.]+)\s*萬", card_text)
    if not m:
        return None
    try:
        return round(float(m.group(1)) * 10_000)
    except ValueError:
        return None


def _parse_district(address: str) -> str:
    """`台北市信義區市民大道六段` -> `信義`."""
    m = re.search(r"台北市(.+?)區", address)
    return m.group(1) if m else ""


def _parse_property_type(title: str, subtitle: str) -> str:
    """Classify 店面 vs 辦公 from the marketing text (search is 店面+辦公)."""
    text = f"{title} {subtitle}"
    if "店" in text:          # 店面 / 金店面 / 店辦
        return "店面"
    return "辦公"             # 辦公 / 商辦 / 純辦


def _parse_detail_row(row_text: str) -> dict:
    """`大樓 | 開放式 | 4.3年 | 1樓/12樓 | 建坪 | 40.21坪` -> structured fields."""
    tokens = [t.strip() for t in row_text.split("|") if t.strip()]
    out: dict = {"building_type": None, "layout": None, "floor": None, "area_ping": None}
    if tokens:
        out["building_type"] = tokens[0]
    if len(tokens) > 1:
        out["layout"] = tokens[1]
    for tok in tokens:
        if out["floor"] is None and "樓" in tok and re.search(r"\d", tok):
            # `1樓/12樓` -> `1F/12F`, `地下1樓/12樓` -> `地下1F/12F`
            out["floor"] = tok.replace("樓", "F")
        # area token has a digit + 坪 but is not the bare `建坪` label
        if out["area_ping"] is None and "坪" in tok and re.search(r"[\d.]+", tok):
            am = re.search(r"([\d.]+)\s*坪", tok)
            if am:
                out["area_ping"] = float(am.group(1))
    return out


def _clean_photo(src: str) -> str:
    """Decode the `%2f`-encoded slash and drop the `?TIMESTAMP` cache-buster."""
    src = urllib.parse.unquote(src)
    return src.split("?", 1)[0]


def _card_section(anchor):
    """Climb from a detail anchor to its enclosing <section> card container."""
    node = anchor
    while node is not None and getattr(node, "name", None) != "section":
        node = node.parent
    return node


def _fetch_description(session: requests.Session, code: str) -> tuple[str | None, list[str]]:
    """Enrich from the detail page: `meta[name=description]` + any extra tags."""
    url = f"{_BASE}/detail?sn={code}"
    try:
        resp = session.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException:
        return None, []
    soup = BeautifulSoup(resp.text, "lxml")
    description = None
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        description = _clean(meta["content"])
    extra = [_clean(t.get_text()) for t in soup.select("span.tag, .tag")]
    extra = [t for t in extra if t]
    return description, extra


def _parse_card(anchor, code: str) -> Listing | None:
    """Build a Listing from a single search-result card (detail added later)."""
    card = _card_section(anchor)
    if card is None:
        return None

    title_el = card.select_one('h3 a[href*="detail?sn="]') or anchor
    title = _clean(title_el.get_text())

    # subtitle / sales blurb (first <p> without the `attribute` class, if present)
    subtitle = ""
    for p in card.find_all("p"):
        classes = p.get("class") or []
        if "attribute" not in classes and "font-montserrat" not in classes:
            t = _clean(p.get_text())
            if t and "物件編號" not in t and t != title:
                subtitle = t
                break

    attr_rows = [_clean(p.get_text(" ")) for p in card.select("p.attribute")]
    address = ""
    detail_row = ""
    for row in attr_rows:
        if row.startswith("台北市") and not address:
            address = row
        elif "|" in row and not detail_row:
            detail_row = row

    fields = _parse_detail_row(detail_row)
    rent = _parse_rent(card.get_text(" "))

    if rent is None or fields["area_ping"] is None or not address:
        return None  # skip cards we can't map to the required Listing fields

    labels = [_clean(t.get_text()) for t in card.select(".tag")]
    labels = [t for t in labels if t]

    img = card.find("img")
    photo = _clean_photo(img["src"]) if img and img.get("src") else None

    return Listing(
        source="hbhousing",
        listing_id=code,
        link=f"{_BASE}/detail?sn={code}",
        title=title or code,
        rent_ntd=rent,
        area_ping=fields["area_ping"],
        floor=fields["floor"],
        district=_parse_district(address),
        address=address,
        property_type=_parse_property_type(title, subtitle),
        photo_url=photo,
        building_type=fields["building_type"],
        layout=fields["layout"],
        description=None,
        labels=labels,
    )


def _reported_total(html: str) -> int | None:
    """`共找到 <span>21</span> 筆` -> 21."""
    m = re.search(r"共找到\s*<span[^>]*>([0-9,]+)</span>", html)
    return int(m.group(1).replace(",", "")) if m else None


def fetch() -> list[Listing]:
    """Return unfiltered HB Housing listings across all result pages.

    Pagination: `/{N}-page` path suffix (see module docstring). Loops until a page
    returns no new listings or we've collected the site-reported total.
    """
    session = requests.Session()
    listings: list[Listing] = []
    seen: set[str] = set()
    total: int | None = None

    for page in range(1, _MAX_PAGES + 1):
        try:
            resp = session.get(_search_url(page), headers=_HEADERS, timeout=_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"[hbhousing] page {page} fetch failed: {e}")
            break

        html = resp.text
        if total is None:
            total = _reported_total(html)

        soup = BeautifulSoup(html, "lxml")
        anchors = soup.select('a[href*="detail?sn="]')

        new_on_page = 0
        for anchor in anchors:
            m = re.search(r"sn=([A-Za-z0-9]+)", anchor.get("href", ""))
            if not m:
                continue
            code = m.group(1)
            if code in seen:
                continue
            seen.add(code)
            try:
                listing = _parse_card(anchor, code)
            except Exception as e:  # noqa: BLE001 - one bad card shouldn't sink the page
                print(f"[hbhousing] card {code} parse failed: {e}")
                continue
            if listing is None:
                continue

            # Detail-page enrichment (best effort, polite).
            try:
                description, extra_labels = _fetch_description(session, code)
                if description:
                    listing.description = description
                for lbl in extra_labels:
                    if lbl not in listing.labels:
                        listing.labels.append(lbl)
            except Exception as e:  # noqa: BLE001
                print(f"[hbhousing] detail {code} failed: {e}")
            time.sleep(_DETAIL_SLEEP)

            listings.append(listing)
            new_on_page += 1

        if new_on_page == 0:
            break
        if total is not None and len(seen) >= total:
            break

    return listings


if __name__ == "__main__":
    import json

    results = fetch()
    print(f"\nfetched {len(results)} listings")

    by_district: dict[str, int] = {}
    for r in results:
        by_district[r.district] = by_district.get(r.district, 0) + 1
    print("per-district:", dict(sorted(by_district.items())))

    print("\nfirst 3 listings:")
    for r in results[:3]:
        print(json.dumps(json.loads(r.model_dump_json()), ensure_ascii=False, indent=2))
