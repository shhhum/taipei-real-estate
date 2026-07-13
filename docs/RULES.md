# Filter rules

Exclusion rules ported from the original SKILL. These are **hard rejects** unless
noted otherwise. The filter (`src/filters/rules.py`) applies them to every listing
and returns `(accepted, [(rejected, reason), ...])`.

The regex patterns below are given exactly. All matches are Chinese-aware
substring / `re.search` matches (not anchored) unless a pattern says otherwise.

---

## Rule 1 — 住辦 family (mixed residential-office not allowed)

Reject if the title, description, or 型態 field matches **any** of:

```
(?<!商)住辦
住辦大樓
純住辦
住商辦
(?<!商)住辦混合
(?<!商)住辦兼用
可住可辦
用途:住辦
住商用
用途:住商用
住商混合
住商兼用
```

Note the **negative lookbehind** on `住辦`: `商住辦均可` is fine — it's an
"OR" listing style tolerated in a commercial context. The lookbehind ensures a
preceding `商` (as in `商住辦`) does not trigger the reject.

## Rule 2 — Residential (住宅 / 住家)

Reject if it matches:

```
住宅
住家
整層住家
住宅大樓
用途:住宅/住家
```

Plus zoning codes — reject on any of the CJK-numeral forms **and** their Arabic
variants:

```
住一   住二   住三   住四
住1    住2    住3    住4
```

## Rule 3 — Bedroom-based layout

Reject if the layout matches `[1-9]+房` (any bedroom count). Commercial units are
`OPEN` / open-plan; a bedroom count signals a residential unit.

## Rule 4 — Industrial

Reject:

```
廠房   廠辦   工廠
```

## Rule 5 — Basement (relaxed)

Reject **only** pure basement listings:

- Floor field is `B1` **alone** (basement with no above-ground portion).

Hybrid floors **pass**:

- `B1+1F`, `B1~1F` and similar (basement **with** an above-ground portion) are OK.

`防空避難室` (air-raid shelter) anywhere in the description is a **hard reject**
regardless of the floor field.

## Rule 6 — 透天厝

Reject if `building_type` or `description` contains:

```
透天厝
```

## Rule 7 — 公寓 (walk-up apartment)

Reject if `building_type` is exactly `公寓`, **or** the description matches any of:

```
老公寓
公寓住宅
[0-9]+樓公寓
```

## Rule 8 — Shared bathroom

Reject if the description matches any of:

```
公共衛生間
公用廁所
共用廁所
公廁
廁所在外
```

If **both** a private-bathroom signal (e.g. `獨立衛生間`, `私人衛浴`) **and** a
shared-bathroom signal appear, still **REJECT**.

## Rule 9 — Price / area sanity

Reject if:

- `rent_ntd` not in `[25000, 150000]`, **or**
- `area_ping` not in `[35, 70]`.

Apply this **after** the site-level filters (post-filter).

## Rule 10 — Districts

Only accept if `district` is one of:

```
中正   大安   大同   萬華   中山   松山   信義
```

(The `district` field may carry a `區` suffix, e.g. `中正區` — match on the
district stem.)

## Rule 11 — Building height (max 10 floors)

Reject if the building's **total** floor count exceeds **10**.

The `floor` field is usually `unit/total` — `2F/10F`, `5/12樓`, `B1~1/7F`,
`整棟/3F` — so the total is the number in the segment after the **last** `/`
(`2F/10F` → 10 ✓ pass; `6F/11F` → 11 ✗ reject). Normalise before parsing:
strip whitespace, uppercase, treat `樓` as `F`.

When there is no `/total` part, there is no explicit height; fall back to the
highest floor number mentioned, which is a lower bound on the building height
(`12F` alone → 12 ✗ reject; `1-3F` → 3 ✓ pass). If no number can be parsed at
all (`路邊/臨街門面`, `N/A`, empty), the rule **fails closed** — an unverifiable
height is a **reject**, not a pass. A blank floor is usually the symptom of a
degraded detail-page fetch; accepting it would let tall buildings through the
cap unchecked, so the listing is rejected rather than trusted.

Upstream, the 591 scraper also pre-filters this at the API for **辦公**
(offices): it caps the queried unit floor at the height limit (`multiFloor=1_10`),
since a unit above the cap is necessarily in an over-cap building. Storefronts
(**店面**) are left unfiltered — they are ground-floor with no high-floor units,
and the numeric range would drop legitimate basement (B1) storefronts.

## Rule 12 — Lane/alley address

Reject if the **address** contains either character:

```
巷   弄
```

A 巷 (lane) or 弄 (alley) address means the unit sits off the main road with
no street visibility. This rule checks only the `address` field — 巷/弄 in a
title or description (e.g. "巷口第一間") does not reject on its own.

---

## How to apply

- Check the **title AND description AND** — where available — the structured
  **型態 / 用途** fields. A listing can be residential/mixed-use per the structured
  fields while looking clean in the title, and vice-versa.
- For **591** specifically, the structured 用途 / 型態 fields live in the NUXT
  payload under `baseInfo.labelInfo.left[]` / `baseInfo.labelInfo.right[]`. The
  591 sibling agent extracts those arrays and feeds their text into Rules 1, 2,
  and 4 alongside the title/description.
- Rules 1–8 are content/type rejects and should run against whatever text and
  structured fields a given site provides. Rule 9 (price/area) and Rule 10
  (district) are the final numeric/categorical gate.
- On rejection, record a short human-readable reason (e.g. `"Rule 2: 住宅"`) so
  the orchestrator can log why a listing was dropped.
