# Synthetic data

*Every product, rep and number here is made up. No real companies, brands or people.*

## How to use it

```
uv run python data/generate.py          # builds data/incentive.db
uv run python data/check.py             # prints the segment mix
uv run python data/check.py --detail    # also one line per rep
```

`generate.py` deletes and rebuilds `data/incentive.db` on every run. Because the seed is fixed, every run produces a byte-identical file.

## What's in the database

| Table | Rows | One row is |
|---|---|---|
| `skus` | 20 | A product: id, name, category, price, margin rate, focus flag |
| `reps` | 50 | A rep: id (R01–R50), first name, start day |
| `sales` | ~9,160 | One rep, one day, one product: quantity and ₹ value. Negative = return |
| `scheme` | 3 | An incentive step: ₹ threshold and flat payout |
| `scheme_rules` | 5 | Focus kicker, unlock step, returns rule, month length, today |

Each rep, day and product appears at most once in `sales`. A return gets its own row on a later day than the sale.

## Parameters

All of these are set at the top of `data/generate.py`.

### Calendar

| Parameter | Value | Why |
|---|---|---|
| `MONTH_DAYS` | 30 | One scheme month |
| `TODAY` | 24 | Leaves 6 days for the month-end push. Sales exist for days 1–24 only. Day 24 rather than day 20 is what lets in-reach reps chase steps 2 and 3, not just step 1. See [Why day 24](#why-day-24) |
| `SEED` | 42 | Fixes the random numbers so the data is reproducible and the evals stay stable. Any other value gives different data that is just as valid |

### Scheme

| Parameter | Value | Why |
|---|---|---|
| `STEPS` | ₹50,000 → ₹1,000; ₹75,000 → ₹2,500; ₹100,000 → ₹5,000 | Stepped cliffs, per the PRD. A rep is paid only the payout for the highest step crossed |
| `FOCUS_KICKER_PER_PIECE` | ₹75 | Per-piece bonus on focus SKUs |
| `KICKER_UNLOCK_STEP` | 1 | The kicker is paid only after step 1 is crossed, and then on **all** focus pieces sold that month, not just the ones sold after crossing. This is what makes crossing step 1 worth more than ₹1,000 |
| `RETURNS_NETTED` | 0 | Returns are not subtracted for payout. Progress toward a step uses gross sales (positive rows only). Returns are still in the data so the next-month return-rate guardrail can be measured |

### Products

| Parameter | Value | Why |
|---|---|---|
| `CATEGORIES` | 5 categories × 4 generic product names | Gives a realistic range of products. Within each category, names are listed cheapest first |
| `PRICE_MIN`, `PRICE_MAX` | ₹50 – ₹2,000 | Spans everyday items to big-ticket items. Prices are drawn on a log scale, so cheap items are more common. Within each category the drawn prices are sorted, so the cheapest name always gets the cheapest price. Prices are rounded to ₹5 |
| `MARGIN_MIN`, `MARGIN_MAX` | 0.15 – 0.30 | Gross margin as a share of price for normal products. Used to calculate margin gained for call ranking |
| `FOCUS_SKU_COUNT` | 4 | The products the company wants pushed |
| `FOCUS_MARGIN_MIN`, `FOCUS_MARGIN_MAX` | 0.30 – 0.40 | Focus products earn more margin, which is why a kicker on them makes sense |
| `FOCUS_MIN_PRICE` | ₹300 | Focus SKUs are picked only from products priced ₹300 or more. That guarantees at least ₹90 margin per piece (0.30 × 300), more than the ₹75 kicker. Without this rule, an early run made three cheap items focus SKUs where the kicker was larger than the margin, so the company would lose money on every focus piece |

With seed 42 the focus SKUs are S04, S08, S18 and S20, with ₹124–706 margin per piece.

### Reps

| Parameter | Value | Why |
|---|---|---|
| `FIRST_NAMES` | 50 common first names, no surnames | Makes the drafted messages read naturally. They don't refer to anyone |
| `LATE_STARTER`, `LATE_START_DAY` | R37 starts on day 10 | Tests that average pace is calculated from the rep's start day. R37 has 15 working days, not 24. See [the late-starter trap](#test-cases-built-in) |
| `BULK_REP`, `BULK_REP_DAY` | R05, day 17 | R05 always gets one of the bulk orders on day 17, so this test case stays put when other settings change |

### Messy data

Real sales exports are messy, and the coach has to handle that.

| Parameter | Value | Why |
|---|---|---|
| `NO_SALES_SHARE` | 5% of rep-days | Days with leave, travel or simply no orders (59 of 1,191 rep-days). No-sales days still count as days worked in the average, so a rep's pace isn't inflated |
| `RETURN_ROW_SHARE` | 2% of sales rows | Each return reverses 1–3 pieces of an earlier sale, on a later day (180 rows). Because returns aren't netted, a tool that subtracts them will get payouts wrong, which makes them a useful test |
| `BULK_ORDER_DAYS` | 3 | One rep-day each with a single very large order of a cheap product (₹400 or less) |
| `BULK_SIZE_MIN`, `BULK_SIZE_MAX` | 4–6× a normal day | Large enough to stand out as an outlier. Bulk orders go only to top-step reps or reps on track for step 3, so a single order can't swamp a small seller's month |

With seed 42 the bulk days are R05 on day 17 (fixed), and R19 and R34 on day 4 (random). The bulk orders aren't flagged in the database, because a real export wouldn't flag them. They are listed here and printed by `generate.py`.

## The segment mix

### Target

| Segment | Share | Reps | Rule (from the PRD) |
|---|---|---|---|
| Top step | 20% | 10 | Already past ₹100,000 |
| On track | 30% | 15 | Projected month-end ≥ next step |
| In reach | 30% | 15 | Short of the next step, and pace needed is 1.0–1.5× current pace. 5 reps chasing each of steps 1, 2 and 3 |
| Far behind | 20% | 10 | Pace needed > 1.5× current pace |

- **Projected month-end** = sales so far + current pace × days left
- **Current pace** = sales so far ÷ days since the rep's start day (days included)
- **Pace needed** = (next step − sales so far) ÷ days left

Needing more than 1.0× your current pace is the same thing as being projected to miss the next step. So the 1.0× floor of "in reach" is simply the boundary with "on track".

### How the data hits it

For a rep who started on day 1, current pace = sales ÷ 24, so **projected = 1.25 × sales so far**. That sets every boundary:

| Chasing | Far behind | In reach | On track |
|---|---|---|---|
| Step 1 (₹50,000) | < ₹36,364 | ₹36,364 – ₹39,999 | ₹40,000 – ₹49,999 |
| Step 2 (₹75,000) | ₹50,000 – ₹54,544 | ₹54,545 – ₹59,999 | ₹60,000 – ₹74,999 |
| Step 3 (₹100,000) | none possible | ₹75,000 – ₹79,999 | ₹80,000 – ₹99,999 |

- **On track** from step ÷ 1.25.
- **In reach** from (step − s) ÷ 6 ≤ 1.5 × s ÷ 24, which gives s ≥ step ÷ 1.375.
- **Step 3 has no far-behind range:** ₹75,000 is already above 100,000 ÷ 1.375 = ₹72,727.

`SEGMENT_PLAN` gives each rep a target for sales so far, drawn safely inside one of these bands and away from the edges:

| Group | Reps | Target range (₹) |
|---|---|---|
| Top step | 10 | 105,000 – 125,000 |
| On track for step 1 | 5 | 41,000 – 48,000 |
| On track for step 2 | 5 | 62,000 – 73,000 |
| On track for step 3 | 4 + R05 | 82,000 – 96,000 |
| In reach for step 1 | 4 | 36,900 – 39,500 |
| In reach for step 1: late starter R37 | 1 | 32,000 – 35,000 |
| In reach for step 2 | 5 | 55,200 – 59,300 |
| In reach for step 3 | 5 | 75,600 – 79,400 |
| Far behind (all chasing step 1) | 10 | 14,000 – 34,000 |

R37's range is different because, with 15 working days, their projection is 1.4 × sales. That makes their in-reach band ₹31,250 – ₹35,714.

The 48 ordinary targets are shuffled across reps, so segment doesn't follow rep number. Each rep's target is then split across their working days. Every day gets a random weight from 0.5 to 1.5, so some days are busier than others. Each day is filled one random product at a time until the day's budget is used up. If rounding leaves a rep's month total below their range, the script tops it up with the cheapest product.

`check.py` recalculates every segment from the raw rows. It doesn't use the plan. It also prints how many times their current pace each rep needs, and counts in-reach reps by the step they're chasing.

The current result is exactly 10 / 15 / 15 / 10, with in-reach split 5 / 5 / 5. In-reach reps need between 1.08× and 1.42× their current pace.

### Why day 24

The first version used day 20, and there in-reach reps for steps 2 and 3 are mathematically impossible. At day 20 the projection is 1.5 × sales.
- **Chasing step 2:** sales are at least ₹50,000, so the projection is at least ₹75,000. Always on track.
- **Chasing step 3:** sales are at least ₹75,000, so the projection is at least ₹112,500. Always on track.

**The general rule:** a step can only have in-reach reps if its threshold divided by the previous one is larger than the projection multiplier. Here that's 1.5 for step 2 and 1.33 for step 3. So "today" has to be day 23 or later (multiplier 30 ÷ 23 = 1.30).

Day 24 keeps the scheme unchanged, leaves 6 days for the push, and gives every in-reach band at least ₹3,600 of room. The other option, spreading the steps further apart, would have changed the scheme itself.

### Test cases built in

1. **The late-starter trap (R37).** R37 has ₹33,280 over 15 days, a pace of ₹2,219 a day. They need ₹2,787 a day (1.26×), so they're in reach for step 1. A tool that wrongly divides by 24 days gets a pace of ₹1,387 a day, needs 2.01×, and labels R37 far behind.
2. **The bulk-order distortion (R05).** R05 is on track for step 3 at ₹95,435. But ₹17,105 of that is one order of 311 pieces on day 17. Without it they'd have ₹78,330, which is in reach for step 3. The order alone moves R05 from "in reach" (who gets a call) to "on track" (who doesn't), because the average-pace projection treats it as repeatable. That's a known limit of the PRD's projection formula. These rows let the evals check that messages to R05 stay honest.
3. **Narrow in-reach bands.** In-reach bands are ₹3,600 – ₹5,500 wide. A small change to the 1.5× multiplier moves many reps between in reach and far behind. This supports the PRD's assumption that the threshold needs validating.

## Changing the data

- To get a different but equally valid dataset, change `SEED`. R37 and R05 keep their roles whatever the seed.
- To change the mix, edit `SEGMENT_PLAN`. Keep each range inside its band from the table above, then run `check.py`.
- If you change `STEPS`, `TODAY` or `MONTH_DAYS`, the bands move. Recalculate them with the formulas above. `check.py` reads the steps and calendar from the database, so it always applies the current rules.
