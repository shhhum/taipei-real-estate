"""Exclusion-rule filter.

Applies the hard-reject rules documented in `docs/RULES.md` to a batch of
listings and partitions them into accepted / rejected.

TODO(filter sibling agent): implement `apply()`.
"""

from src.models import Listing


def apply(listings: list[Listing]) -> tuple[list[Listing], list[tuple[Listing, str]]]:
    """Partition listings into (accepted, [(rejected, reason), ...]).

    See `docs/RULES.md` for the exact patterns to enforce.
    """
    raise NotImplementedError("filters.rules.apply() not yet implemented")
