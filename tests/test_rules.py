"""Tests for the exclusion-rule filter (`src.filters.rules`).

Each rule gets at least one rejection case and one acceptance case (usually the
edge just past the rule). No network / no I/O — these run instantly.
"""

import pytest

from src.filters.rules import (
    RULES,
    apply,
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
    total_floors,
)
from src.models import Listing


def make_listing(**overrides) -> Listing:
    """A baseline listing that PASSES every rule; override one field per test."""
    base = dict(
        source="591",
        listing_id="abc123",
        link="https://rent.591.com.tw/abc123",
        title="優質店面出租",
        rent_ntd=50_000,
        area_ping=50.0,
        floor="2F/10F",
        district="大安區",
        address="台北市大安區xx路1號",
        property_type="店面",
        building_type="電梯大樓",
        layout="OPEN",
        description="採光佳，適合開店",
        labels=["近捷運", "可登記"],
    )
    base.update(overrides)
    return Listing(**base)


def test_baseline_passes_all_rules():
    listing = make_listing()
    for rule in RULES:
        assert rule(listing) is None, f"{rule.__name__} wrongly rejected baseline"


# --- Rule 1: 住辦 family ----------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["住辦大樓", "純住辦", "住商辦", "可住可辦", "住商混合", "住商兼用", "住商用"],
)
def test_rule_1_rejects(text):
    assert check_rule_1(make_listing(description=text)) is not None


def test_rule_1_bare_zhuban_rejects():
    assert check_rule_1(make_listing(description="本案為住辦空間")) is not None


def test_rule_1_negative_lookbehind_passes():
    # 商住辦 (preceded by 商) must PASS — the negative lookbehind protects it.
    assert check_rule_1(make_listing(description="商住辦均可")) is None


def test_rule_1_usage_prefix_with_space():
    assert check_rule_1(make_listing(description="用途: 住辦")) is not None


# --- Rule 2: residential ----------------------------------------------------


@pytest.mark.parametrize(
    "text", ["住宅", "住家", "整層住家", "住宅大樓", "住三", "住3", "住一", "住4"]
)
def test_rule_2_rejects(text):
    assert check_rule_2(make_listing(description=text)) is not None


def test_rule_2_arabic_and_cjk_both_reject():
    assert check_rule_2(make_listing(description="住3")) is not None
    assert check_rule_2(make_listing(description="住三")) is not None


def test_rule_2_passes_clean():
    assert check_rule_2(make_listing(description="全新裝潢辦公空間")) is None


# --- Rule 3: bedroom layout -------------------------------------------------


def test_rule_3_rejects_layout():
    assert check_rule_3(make_listing(layout="2房2廳2衛")) is not None


def test_rule_3_open_passes():
    assert check_rule_3(make_listing(layout="OPEN")) is None


def test_rule_3_falls_back_to_corpus():
    assert check_rule_3(make_listing(layout=None, description="格局3房")) is not None


# --- Rule 4: industrial -----------------------------------------------------


@pytest.mark.parametrize("text", ["廠房", "廠辦", "工廠"])
def test_rule_4_rejects(text):
    assert check_rule_4(make_listing(description=text)) is not None


def test_rule_4_passes():
    assert check_rule_4(make_listing(description="辦公空間")) is None


# --- Rule 5: basement -------------------------------------------------------


def test_rule_5_pure_basement_rejects():
    assert check_rule_5(make_listing(floor="B1")) is not None


def test_rule_5_hybrid_floor_passes():
    assert check_rule_5(make_listing(floor="B1+1F")) is None
    assert check_rule_5(make_listing(floor="B1~1F")) is None
    assert check_rule_5(make_listing(floor="B1-1F")) is None


def test_rule_5_shelter_rejects_even_above_ground():
    assert check_rule_5(make_listing(floor="5F", description="含防空避難室")) is not None


def test_rule_5_normalizes_whitespace_and_case():
    assert check_rule_5(make_listing(floor=" b1 ")) is not None


# --- Rule 6: 透天厝 ---------------------------------------------------------


def test_rule_6_building_type_rejects():
    assert check_rule_6(make_listing(building_type="透天厝")) is not None


def test_rule_6_corpus_rejects():
    assert check_rule_6(make_listing(description="整棟透天厝")) is not None


def test_rule_6_passes():
    assert check_rule_6(make_listing()) is None


# --- Rule 7: 公寓 walk-up ---------------------------------------------------


def test_rule_7_building_type_exact_rejects():
    assert check_rule_7(make_listing(building_type="公寓")) is not None


def test_rule_7_old_apartment_rejects():
    assert check_rule_7(make_listing(description="這是老公寓改建")) is not None


@pytest.mark.parametrize("text", ["公寓住宅", "5樓公寓"])
def test_rule_7_patterns_reject(text):
    assert check_rule_7(make_listing(description=text)) is not None


def test_rule_7_bare_mention_passes():
    # Bare 公寓 in prose (e.g. a comparison) must NOT reject.
    assert check_rule_7(make_listing(description="比鄰近公寓更寬敞")) is None


# --- Rule 8: shared bathroom ------------------------------------------------


@pytest.mark.parametrize("text", ["公共衛生間", "公用廁所", "共用廁所", "公廁", "廁所在外"])
def test_rule_8_rejects(text):
    assert check_rule_8(make_listing(description=text)) is not None


def test_rule_8_mixed_signals_still_rejects():
    # Private + shared signals together → still REJECT.
    assert check_rule_8(make_listing(description="獨立衛生間，另有公共衛生間")) is not None


def test_rule_8_private_only_passes():
    assert check_rule_8(make_listing(description="獨立衛生間")) is None


# --- Rule 9: price / area sanity --------------------------------------------


def test_rule_9_rent_below_band_rejects():
    assert check_rule_9(make_listing(rent_ntd=24_999)) is not None


def test_rule_9_rent_above_band_rejects():
    assert check_rule_9(make_listing(rent_ntd=100_001)) is not None


def test_rule_9_area_above_band_rejects():
    assert check_rule_9(make_listing(area_ping=70.5)) is not None


def test_rule_9_area_below_band_rejects():
    assert check_rule_9(make_listing(area_ping=34.9)) is not None


def test_rule_9_band_edges_pass():
    assert check_rule_9(make_listing(rent_ntd=25_000, area_ping=35.0)) is None
    assert check_rule_9(make_listing(rent_ntd=100_000, area_ping=70.0)) is None


# --- Rule 10: districts -----------------------------------------------------


def test_rule_10_outside_district_rejects():
    assert check_rule_10(make_listing(district="新莊")) is not None
    assert check_rule_10(make_listing(district="新莊區")) is not None


def test_rule_10_suffix_normalization_passes():
    assert check_rule_10(make_listing(district="中正區")) is None


def test_rule_10_bare_stem_passes():
    assert check_rule_10(make_listing(district="信義")) is None


# --- Rule 11: building height -------------------------------------------------


@pytest.mark.parametrize(
    ("floor", "expected"),
    [
        ("2F/10F", 10),
        ("6F/11F", 11),
        ("5/12樓", 12),
        ("B1~1/7F", 7),
        ("整棟/3F", 3),
        ("1F/5F + 地下室", 5),
        ("4/10F\n", 10),
        ("12F", 12),      # no total — highest floor mentioned is a lower bound
        ("1-3F", 3),
        ("B1", 1),
        ("路邊/臨街門面", None),
        ("N/A", None),
    ],
)
def test_total_floors_parsing(floor, expected):
    assert total_floors(floor) == expected


def test_rule_11_tall_building_rejects():
    assert check_rule_11(make_listing(floor="6F/11F")) is not None
    assert check_rule_11(make_listing(floor="5/12樓")) is not None
    assert check_rule_11(make_listing(floor="12F")) is not None


def test_rule_11_at_most_10_floors_passes():
    assert check_rule_11(make_listing(floor="2F/10F")) is None
    assert check_rule_11(make_listing(floor="1F/7F")) is None
    assert check_rule_11(make_listing(floor="整棟/3F")) is None


def test_rule_11_unknown_height_passes():
    assert check_rule_11(make_listing(floor=None)) is None
    assert check_rule_11(make_listing(floor="路邊/臨街門面")) is None


# --- apply() integration ----------------------------------------------------


def test_apply_empty():
    assert apply([]) == ([], [])


def test_apply_partitions_and_reports_reason():
    good = make_listing(listing_id="good")
    bad = make_listing(listing_id="bad", district="新莊")
    accepted, rejected = apply([good, bad])
    assert accepted == [good]
    assert len(rejected) == 1
    rejected_listing, reason = rejected[0]
    assert rejected_listing is bad
    assert reason.startswith("Rule 10")


def test_apply_first_rule_wins():
    # A listing failing Rule 2 (residential) and Rule 10 (district) reports Rule 2.
    listing = make_listing(description="住宅大樓", district="新莊")
    _, rejected = apply([listing])
    assert rejected[0][1].startswith("Rule 2")
