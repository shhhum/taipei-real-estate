"""永慶房屋 (Yungching) rent scraper — Taipei commercial listings.

Yungching's rent site (``rent.yungching.com.tw``) is an Angular SPA: a plain
``requests`` GET returns an empty shell, so we drive a real headless Chromium via
Playwright and read the hydrated DOM.

One-time setup (install the browser binary Playwright drives)::

    uv run playwright install chromium

Then run this module directly to smoke-test::

    uv run python -m src.scrapers.site_yungching

Search space comes from ``src.config`` (rent band, area band, districts). The
site URL encodes city / price / use / building-type / area as path segments::

    /list/台北市-_c/25000-150000_price/店面,辦公_use/電梯大廈_type/35-70_pin

Notes learned from inspecting the live DOM (2026-07):

* The comma-joined ``店面,辦公_use`` filter WORKS — one crawl returns both
  property types (全部(160)), so we do a single crawl rather than two.
* Yungching's ``_use`` and ``_type`` path filters are *loose*: the result set
  includes 住辦 / 住宅 / 整層住家 and 電梯大樓/華廈 rows even though we asked for
  店面,辦公 + 電梯大廈. We therefore read the REAL per-listing type from each card's
  ``.purpose`` field into ``property_type`` (NOT the requested filter value) so the
  downstream filter rules (Rule 1 住辦, Rule 2 住宅, etc.) see the true type. This
  intentionally deviates from the "property_type = loop iteration" hint in the
  original spec, which would have mislabelled every row as 店面/辦公.
* Listing cards carry every field we need except description + building_type,
  which we enrich from each detail page.
"""

from __future__ import annotations

import os
import random
import re
import urllib.parse
from pathlib import Path

from src import config
from src.models import Listing

BASE = "https://rent.yungching.com.tw"

# City stays 台北市 (whole city); districts are filtered client-side against
# config.DISTRICT_NAMES because the path syntax doesn't cleanly do multi-district.
# Use is comma-joined (verified working); building-type 電梯大廈 excludes 公寓/透天.
_SEARCH_PATH = (
    f"/list/台北市-_c/{config.RENT_MIN_NTD}-{config.RENT_MAX_NTD}_price/"
    f"{','.join(config.PROPERTY_TYPES)}_use/電梯大廈_type/"
    f"{config.AREA_MIN_PING}-{config.AREA_MAX_PING}_pin"
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

CARD_SELECTOR = "a.link[href^='house/']"
_MAX_PAGES = 25  # runaway guard; real result set is ~6 pages


def _proxy_settings() -> dict | None:
    """Pass the environment's HTTPS proxy to Chromium explicitly.

    Unlike ``requests``, Chromium does not read ``HTTPS_PROXY`` from the
    environment, so in proxied containers (e.g. Claude Code remote, where all
    egress must go through an agent proxy) an unconfigured launch dials direct
    and gets ERR_CONNECTION_RESET. Returns None when no proxy is set.
    """
    server = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if not server:
        return None
    proxy: dict = {"server": server}
    bypass = os.environ.get("NO_PROXY") or os.environ.get("no_proxy")
    if bypass:
        proxy["bypass"] = bypass
    return proxy


def _chromium_executable(chromium) -> str | None:
    """Executable override for environments with a pre-installed Chromium.

    ``CHROMIUM_EXECUTABLE`` always wins. Otherwise, if the revision this
    Playwright version expects is absent but the host ships a version-agnostic
    binary at ``$PLAYWRIGHT_BROWSERS_PATH/chromium`` (the Claude Code remote
    container convention), use that instead of demanding a re-download.
    Returns None to let Playwright resolve its own default.
    """
    override = os.environ.get("CHROMIUM_EXECUTABLE")
    if override:
        return override
    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if browsers_path and not Path(chromium.executable_path).exists():
        fallback = Path(browsers_path) / "chromium"
        if fallback.exists():
            return str(fallback)
    return None


def _search_url(page_no: int = 1) -> str:
    """Percent-encoded search URL for the given 1-based page number."""
    url = BASE + urllib.parse.quote(_SEARCH_PATH, safe="/-_,")
    if page_no > 1:
        url += f"?pg={page_no}"
    return url


# ---------------------------------------------------------------------------
# Field parsing helpers (operate on the raw strings pulled from the DOM)
# ---------------------------------------------------------------------------

def _parse_int(text: str) -> int | None:
    """'72,800' / '月租金 68,000元' -> 72800."""
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


def _parse_area(text: str) -> float | None:
    """'45.23坪' -> 45.23."""
    m = re.search(r"(\d+(?:\.\d+)?)", text or "")
    return float(m.group(1)) if m else None


def _parse_floor(text: str) -> str | None:
    """Normalise the card's '5/14樓' (current/total) to '5F/14F'.

    Basement tokens are preserved so filter Rule 5 can tell pure basements from
    hybrids: 'B1~1/5樓' -> 'B1~1F/5F' (hybrid, passes), 'B1/5樓' -> 'B1F/5F'.
    """
    raw = (text or "").replace("樓", "").strip()
    if not raw:
        return None
    parts = [p.strip() for p in raw.split("/") if p.strip()]
    if not parts:
        return None
    return "/".join(f"{p}F" for p in parts)


def _parse_district(address: str) -> str | None:
    """'台北市士林區中山北路六段' -> '士林' (stem, no 區 suffix)."""
    m = re.search(r"台北市(.+?)區", address or "")
    return m.group(1) if m else None


def _canonical_photo(img_src: str | None) -> str | None:
    """Rebuild a yccdn img src into the canonical 1024x768 URL.

    Card thumbnails come through as '//yccdn.../v1/image/?key=KEY&width=480&height=0';
    we lift the ``key`` param and request the full-size canonical variant.
    """
    if not img_src or "yccdn" not in img_src:
        return None
    src = img_src.replace("&amp;", "&")
    if src.startswith("//"):
        src = "https:" + src
    m = re.search(r"[?&]key=([^&]+)", src)
    if not m:
        return src if src.startswith("http") else None
    key = m.group(1)
    return f"https://yccdn.yungching.com.tw/v1/image/?key={key}&width=1024&height=768"


# ---------------------------------------------------------------------------
# DOM extraction
# ---------------------------------------------------------------------------

# Runs in the browser: pull each card on the current page into a flat dict.
_CARD_JS = """
els => els.map(a => {
  const q = (sel) => { const e = a.querySelector(sel); return e ? e.innerText.trim() : ""; };
  const img = a.querySelector("img[src*='yccdn']");
  return {
    href: a.getAttribute('href'),
    photo: img ? (img.getAttribute('src') || img.getAttribute('srcset') || '') : '',
    title: q('.caseName'),
    address: q('.address'),
    community: q('.community'),
    purpose: q('.purpose'),
    area: q('.regArea'),
    floor: q('.floor'),
    layout: q('.room'),
    tags: Array.from(a.querySelectorAll('.tag-list .tag-item'))
      .map(t => t.innerText.replace(/\\s+/g, ' ').trim()).filter(Boolean),
    price: q('.price'),
  };
})
"""

# Runs on a detail page: building_type + a description block.
_DETAIL_JS = """
() => {
  const t = document.querySelector('.case-type');
  const feat = document.querySelector('.detail-block.feature');
  const og = document.querySelector('meta[property="og:description"]');
  let desc = feat ? feat.innerText.replace(/\\s+\\n/g, '\\n').trim() : '';
  if (!desc && og) desc = (og.getAttribute('content') || '').trim();
  return {
    building_type: t ? t.innerText.trim() : '',
    description: desc,
  };
}
"""


def _card_to_listing(card: dict) -> Listing | None:
    """Build a Listing from one card dict, or None if a required field is unusable."""
    href = (card.get("href") or "").strip()
    listing_id = href.rstrip("/").split("/")[-1]
    if not listing_id:
        return None

    rent = _parse_int(card.get("price", ""))
    area = _parse_area(card.get("area", ""))
    address = (card.get("address") or "").strip()
    district = _parse_district(address)
    if rent is None or area is None or not district:
        return None

    # Agency name is not a feature label; drop it from the badge list.
    labels = [t for t in card.get("tags", []) if t and t != "永慶房屋"]

    return Listing(
        source="yungching",
        listing_id=listing_id,
        link=f"{BASE}/house/{listing_id}",
        title=(card.get("title") or "").strip() or listing_id,
        rent_ntd=rent,
        area_ping=area,
        floor=_parse_floor(card.get("floor", "")),
        district=district,
        address=address,
        # Real per-listing type from .purpose (see module docstring), not the filter.
        property_type=(card.get("purpose") or "").strip() or "店面",
        photo_url=_canonical_photo(card.get("photo")),
        building_type=None,  # enriched from the detail page
        layout=(card.get("layout") or "").strip() or None,
        description=None,    # enriched from the detail page
        labels=labels,
    )


def _collect_cards(page) -> list[dict]:
    """Return the raw card dicts on the currently-loaded search page."""
    try:
        page.wait_for_selector(CARD_SELECTOR, timeout=15000)
    except Exception:  # noqa: BLE001 - empty/last page: no cards rendered
        return []
    return page.evaluate(_CARD_JS, page.query_selector_all(CARD_SELECTOR))


def _last_page(page) -> int:
    """Read the highest page number from the pagination control (default 1)."""
    try:
        items = page.eval_on_selector_all(
            ".paginationPageListItem", "els => els.map(e => e.innerText.trim())"
        )
    except Exception:  # noqa: BLE001
        return 1
    nums = [int(x) for x in items if x.isdigit()]
    return max(nums) if nums else 1


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def fetch(enrich: bool = True) -> list[Listing]:
    """Return unfiltered Yungching listings (district pre-filtered to the 7 targets).

    Reads the search band from ``src.config``. ``enrich`` visits each detail page
    for description + building_type; pass ``False`` for a fast card-only crawl.
    """
    from playwright.sync_api import sync_playwright

    listings: dict[str, Listing] = {}  # listing_id -> Listing (dedup across pages)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path=_chromium_executable(p.chromium),
            proxy=_proxy_settings(),
        )
        try:
            ctx = browser.new_context(user_agent=USER_AGENT, locale="zh-TW")
            page = ctx.new_page()

            # --- crawl all search pages -> card-level Listings ---------------
            page.goto(_search_url(1), wait_until="domcontentloaded", timeout=60000)
            cards = _collect_cards(page)
            total_pages = min(_last_page(page), _MAX_PAGES)
            print(f"[yungching] {total_pages} search page(s)")

            for page_no in range(1, total_pages + 1):
                if page_no > 1:
                    page.goto(_search_url(page_no), wait_until="domcontentloaded",
                              timeout=60000)
                    cards = _collect_cards(page)
                page.wait_for_timeout(random.uniform(500, 1000))

                for card in cards:
                    try:
                        listing = _card_to_listing(card)
                    except Exception as e:  # noqa: BLE001 - skip one bad card
                        print(f"[yungching] card parse failed: {e}")
                        continue
                    if listing and listing.listing_id not in listings:
                        listings[listing.listing_id] = listing
                print(f"[yungching] page {page_no}: {len(listings)} unique so far")

            # --- district pre-filter (drop the 4 non-target districts) -------
            targets = set(config.DISTRICT_NAMES)
            before = len(listings)
            kept = {lid: v for lid, v in listings.items() if v.district in targets}
            print(f"[yungching] district filter: {before} -> {len(kept)} "
                  f"(kept {sorted(targets)})")

            # --- enrich survivors from their detail pages -------------------
            if enrich:
                for i, listing in enumerate(kept.values(), 1):
                    try:
                        page.goto(str(listing.link), wait_until="domcontentloaded",
                                  timeout=60000)
                        page.wait_for_timeout(random.uniform(500, 1000))
                        detail = page.evaluate(_DETAIL_JS)
                        if detail.get("building_type"):
                            listing.building_type = detail["building_type"]
                        if detail.get("description"):
                            listing.description = detail["description"]
                    except Exception as e:  # noqa: BLE001 - keep card-only listing
                        print(f"[yungching] detail {listing.listing_id} failed: {e}")
                    if i % 20 == 0:
                        print(f"[yungching] enriched {i}/{len(kept)}")

            return list(kept.values())
        finally:
            browser.close()


if __name__ == "__main__":
    import json

    results = fetch()
    print(f"\n=== yungching: {len(results)} listings after district filter ===")

    from collections import Counter
    by_district = Counter(item.district for item in results)
    print("per-district:", dict(by_district.most_common()))

    print("\nfirst 3:")
    for listing in results[:3]:
        print(json.dumps(listing.model_dump(mode="json"), ensure_ascii=False, indent=2))
