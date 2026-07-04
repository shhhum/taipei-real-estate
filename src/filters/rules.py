"""Exclusion-rule filter.

Applies the hard-reject rules documented in `docs/RULES.md` to a batch of
listings and partitions them into accepted / rejected.

Each rule is a `check_rule_N(listing) -> str | None` function: it returns `None`
when the listing passes, or a short human-readable reason string when it fails.
`apply()` runs the rules in `RULES` order and rejects on the first failure.
"""

import re

from src.models import Listing

# ---------------------------------------------------------------------------
# Text corpus
# ---------------------------------------------------------------------------


def _corpus(listing: Listing) -> str:
    """Combined free-text + structured-field blob used by the text-scan rules."""
    parts = [
        listing.title,
        listing.description or "",
        listing.building_type or "",
        listing.property_type or "",
        listing.layout or "",
    ]
    parts.extend(listing.labels)
    return " ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Compiled patterns (compiled once at import)
# ---------------------------------------------------------------------------

# Rule 1 — 住辦 family. Negative lookbehind on 住辦 so 商住辦 (an "OR" style
# listing tolerated in a commercial context) does not trip the reject.
RULE1_PATTERNS = [
    r"(?<!商)住辦",
    r"住辦大樓",
    r"純住辦",
    r"住商辦",
    r"(?<!商)住辦混合",
    r"(?<!商)住辦兼用",
    r"可住可辦",
    r"用途:\s*住辦",
    r"住商用",
    r"用途:\s*住商用",
    r"住商混合",
    r"住商兼用",
]
_RULE1_RE = [re.compile(p) for p in RULE1_PATTERNS]

# Rule 2 — residential zoning / usage. Both CJK-numeral and Arabic-numeral
# zoning variants (591 sometimes formats 住三 as 住3).
RULE2_PATTERNS = [
    r"住宅",
    r"住家",
    r"整層住家",
    r"住宅大樓",
    r"用途:\s*住[宅家]",
    r"住[一二三四]",
    r"住[1-4]",
]
_RULE2_RE = [re.compile(p) for p in RULE2_PATTERNS]

# Rule 3 — bedroom-based layout.
_RULE3_RE = re.compile(r"[1-9]+房")

# Rule 4 — industrial.
RULE4_TERMS = ["廠房", "廠辦", "工廠"]

# Rule 5 — air-raid shelter (hard reject regardless of floor).
_RULE5_SHELTER = "防空避難室"
# Pure-basement floor tokens after normalisation (whitespace stripped, uppercased,
# 樓 treated as F).
_RULE5_PURE_BASEMENT = {"B1", "B1F"}

# Rule 6 — 透天厝 (townhouse).
_RULE6_TERM = "透天厝"

# Rule 7 — 公寓 walk-up. Bare "公寓" in prose is NOT enough (電梯大樓 listings
# mention it in comparisons); require the exact building_type or a stronger cue.
RULE7_PATTERNS = [r"老公寓", r"公寓住宅", r"\d+樓公寓"]
_RULE7_RE = [re.compile(p) for p in RULE7_PATTERNS]

# Rule 8 — shared bathroom. A private-bathroom signal alongside a shared one
# still rejects (mixed use).
RULE8_TERMS = ["公共衛生間", "公用廁所", "共用廁所", "公廁", "廁所在外"]

# Rule 10 — accepted districts (matched on the district stem, 區 suffix stripped).
RULE10_DISTRICTS = {"中正", "大安", "大同", "萬華", "中山", "松山", "信義"}

# Rule 11 — building height cap. Floor text is usually "unit/total" ("2F/10F",
# "5/12樓", "B1~1/7F", "整棟/3F"); the part after the last "/" is the building's
# total floors.
RULE11_MAX_FLOORS = 10

# Rule 12 — lane/alley addresses. 巷 (lane) / 弄 (alley) in the address means
# the unit is off the main road — no storefront visibility.
RULE12_ADDRESS_TERMS = ["巷", "弄"]
_FLOOR_INT_RE = re.compile(r"\d+")


def _normalize_floor(floor: str) -> str:
    """Strip whitespace, uppercase, and treat 樓 as equivalent to F."""
    return floor.strip().upper().replace("樓", "F").replace(" ", "")


def total_floors(floor: str) -> int | None:
    """Best-effort total building floors from a floor string, or None if unknown.

    With a "/" the number in the last segment is the total ("2F/10F" -> 10,
    "1F/5F + 地下室" -> 5). Without one there is no explicit total, so fall back
    to the highest floor number mentioned — a lower bound that still catches a
    bare "12F" unit implying a 12F+ building ("1-3F" -> 3, "B1" -> 1).
    """
    norm = _normalize_floor(floor)
    if "/" in norm:
        tail = norm.rsplit("/", 1)[1]
        m = _FLOOR_INT_RE.search(tail)
        return int(m.group(0)) if m else None
    numbers = [int(n) for n in _FLOOR_INT_RE.findall(norm)]
    return max(numbers) if numbers else None


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def check_rule_1(listing: Listing) -> str | None:
    corpus = _corpus(listing)
    for pat, rx in zip(RULE1_PATTERNS, _RULE1_RE):
        if rx.search(corpus):
            return f"Rule 1: 住辦 family ({pat})"
    return None


def check_rule_2(listing: Listing) -> str | None:
    corpus = _corpus(listing)
    for pat, rx in zip(RULE2_PATTERNS, _RULE2_RE):
        if rx.search(corpus):
            return f"Rule 2: 住宅 ({pat})"
    return None


def check_rule_3(listing: Listing) -> str | None:
    # Prefer the structured layout field, fall back to the full corpus.
    target = listing.layout or _corpus(listing)
    m = _RULE3_RE.search(target)
    if m:
        return f"Rule 3: bedroom layout ({m.group(0)})"
    return None


def check_rule_4(listing: Listing) -> str | None:
    corpus = _corpus(listing)
    for term in RULE4_TERMS:
        if term in corpus:
            return f"Rule 4: industrial ({term})"
    return None


def check_rule_5(listing: Listing) -> str | None:
    corpus = _corpus(listing)
    if _RULE5_SHELTER in corpus:
        return f"Rule 5: basement ({_RULE5_SHELTER})"
    if listing.floor:
        norm = _normalize_floor(listing.floor)
        if norm in _RULE5_PURE_BASEMENT:
            return f"Rule 5: pure basement (floor={listing.floor})"
    return None


def check_rule_6(listing: Listing) -> str | None:
    if listing.building_type and _RULE6_TERM in listing.building_type:
        return "Rule 6: 透天厝 (building_type)"
    if _RULE6_TERM in _corpus(listing):
        return "Rule 6: 透天厝"
    return None


def check_rule_7(listing: Listing) -> str | None:
    if listing.building_type == "公寓":
        return "Rule 7: 公寓 walk-up (building_type)"
    corpus = _corpus(listing)
    for pat, rx in zip(RULE7_PATTERNS, _RULE7_RE):
        if rx.search(corpus):
            return f"Rule 7: 公寓 walk-up ({pat})"
    return None


def check_rule_8(listing: Listing) -> str | None:
    corpus = _corpus(listing)
    for term in RULE8_TERMS:
        if term in corpus:
            return f"Rule 8: shared bathroom ({term})"
    return None


def check_rule_9(listing: Listing) -> str | None:
    if listing.rent_ntd < 25_000 or listing.rent_ntd > 100_000:
        return f"Rule 9: rent out of band ({listing.rent_ntd})"
    if listing.area_ping < 35 or listing.area_ping > 70:
        return f"Rule 9: area out of band ({listing.area_ping})"
    return None


def check_rule_10(listing: Listing) -> str | None:
    stem = listing.district.rstrip("區")
    if stem not in RULE10_DISTRICTS:
        return f"Rule 10: district ({listing.district})"
    return None


def check_rule_11(listing: Listing) -> str | None:
    if not listing.floor:
        return None
    total = total_floors(listing.floor)
    if total is not None and total > RULE11_MAX_FLOORS:
        return f"Rule 11: building over {RULE11_MAX_FLOORS}F (floor={listing.floor})"
    return None


def check_rule_12(listing: Listing) -> str | None:
    for term in RULE12_ADDRESS_TERMS:
        if term in listing.address:
            return f"Rule 12: lane/alley address ({term})"
    return None


RULES = [
    check_rule_1,
    check_rule_2,
    check_rule_3,
    check_rule_4,
    check_rule_5,
    check_rule_6,
    check_rule_7,
    check_rule_8,
    check_rule_9,
    check_rule_10,
    check_rule_11,
    check_rule_12,
]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def apply(listings: list[Listing]) -> tuple[list[Listing], list[tuple[Listing, str]]]:
    """Partition listings into (accepted, [(rejected, reason), ...]).

    Rules run in `RULES` order; the first failing rule rejects the listing and
    records its reason. See `docs/RULES.md` for the exact patterns.
    """
    accepted: list[Listing] = []
    rejected: list[tuple[Listing, str]] = []
    for listing in listings:
        reason: str | None = None
        for rule in RULES:
            reason = rule(listing)
            if reason is not None:
                break
        if reason is None:
            accepted.append(listing)
        else:
            rejected.append((listing, reason))
    return accepted, rejected
