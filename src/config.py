"""Search filters shared by every scraper.

These constants define the target search space (districts, rent band, area band,
property types). Each scraper reads from here so the four sites stay in sync.
"""

# 591 uses region.section numeric codes. The exact section codes are TBD by the
# 591 sibling agent — these are placeholders in section-code order matching the
# district lists below.
DISTRICTS_591 = ["1", "2", "3", "4", "5", "6", "7"]

# Zip codes and human-readable names are index-aligned:
#   100=中正, 106=大安, 103=大同, 108=萬華, 104=中山, 105=松山, 110=信義
DISTRICT_ZIPS = ["100", "106", "103", "108", "104", "105", "110"]
DISTRICT_NAMES = ["中正", "大安", "大同", "萬華", "中山", "松山", "信義"]

# Rent band (monthly, NT$).
RENT_MIN_NTD = 40_000
RENT_MAX_NTD = 100_000

# Area band (坪 / ping).
AREA_MIN_PING = 35
AREA_MAX_PING = 70

# Building height cap (total floors). Listings in buildings taller than this are
# out of scope (filter Rule 11). Kept here as the single source of truth so the
# 591 scraper can pre-filter high-floor units at the API and the filter can
# enforce the same limit downstream.
BUILDING_FLOORS_MAX = 10

# Accepted property types. Explicitly excludes 廠房 (industrial), 住宅
# (residential), and 住辦 (mixed residential-office).
PROPERTY_TYPES = ["店面", "辦公"]
