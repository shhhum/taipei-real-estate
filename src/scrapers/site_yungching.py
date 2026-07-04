"""永慶房屋 (Yung Ching) scraper.

Uses Playwright — the listing pages are JS-rendered.

TODO(Yung Ching sibling agent): implement `fetch()`.
"""

from src.models import Listing


def fetch() -> list[Listing]:
    """Return unfiltered listings from Yung Ching. Reads filters from `src.config`."""
    raise NotImplementedError("site_yungching.fetch() not yet implemented")
