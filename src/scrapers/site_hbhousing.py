"""住商不動產 (HB Housing) scraper.

TODO(HB Housing sibling agent): implement `fetch()`.
"""

from src.models import Listing


def fetch() -> list[Listing]:
    """Return unfiltered listings from HB Housing. Reads filters from `src.config`."""
    raise NotImplementedError("site_hbhousing.fetch() not yet implemented")
