"""Shared data model for scraped listings.

Every scraper returns a list of `Listing`. The filter layer, Airtable client, and
orchestrator all consume this exact shape — keep it stable so the four sibling
scraper agents can conform to a single contract.
"""

from datetime import date

from pydantic import BaseModel, HttpUrl


class Listing(BaseModel):
    source: str                       # "591" | "sinyi" | "hbhousing" | "yungching"
    listing_id: str                   # site-specific ID
    link: HttpUrl                     # canonical URL (Airtable dedup key)
    title: str
    rent_ntd: int                     # monthly rent in NT$
    area_ping: float
    floor: str | None = None          # e.g. "5F/11F", "1F", "B1+1F"
    district: str                     # "中正區" etc.
    address: str
    property_type: str                # "店面" | "辦公" | "住辦" | ...
    photo_url: HttpUrl | None = None
    building_type: str | None = None  # 電梯大樓/公寓/透天厝 etc.
    layout: str | None = None         # "2房2廳2衛" or "OPEN"
    description: str | None = None
    labels: list[str] = []            # tags: 近捷運, 可登記, etc.
    posted_at: date | None = None
