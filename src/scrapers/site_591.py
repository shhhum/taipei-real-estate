"""591 租屋 scraper.

Structured 用途 / 型態 fields live in the NUXT payload under
`baseInfo.labelInfo.left[]` / `.right[]` — extract those for filtering.

TODO(591 sibling agent): implement `fetch()`.
"""

from src.models import Listing


def fetch() -> list[Listing]:
    """Return unfiltered listings from 591. Reads filters from `src.config`."""
    raise NotImplementedError("site_591.fetch() not yet implemented")
