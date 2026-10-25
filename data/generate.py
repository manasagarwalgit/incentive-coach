# Builds the synthetic (fake) sales data for Incentive Coach.
#
# How to run (from the project folder):
#     uv run python data/generate.py
#
# What it makes: data/incentive.db, a SQLite database file with five tables:
#     skus          - the 20 products reps sell
#     reps          - the 50 sales reps
#     sales         - what each rep sold, per day, per product (days 1-24)
#     scheme        - the incentive steps (sales target -> flat payout)
#     scheme_rules  - the other scheme settings (focus kicker, returns rule, calendar)
#
# Every number is made up. Running this again produces the exact same file,
# because the random numbers come from a fixed "seed" (see SEED below).
#
# Every parameter is explained in docs/DATA.md.

import math
import random
import sqlite3
from pathlib import Path


# ---------------------------------------------------------------------------
# 1. SETTINGS
# ---------------------------------------------------------------------------

# The seed makes the "random" numbers repeat exactly on every run.
# Change it and you get a different (but equally valid) set of fake data.
SEED = 42

# Where the database is written: next to this script.
DB_PATH = Path(__file__).parent / "incentive.db"

# The calendar. The month has 30 days and "today" is day 24,
# so we have sales for days 1-24 and there are 6 days left to sell.
# (Day 24, not 20, so that reps chasing steps 2 and 3 can be "in reach":
# see section 2 below.)
MONTH_DAYS = 30
TODAY = 24
DAYS_LEFT = MONTH_DAYS - TODAY

# The incentive scheme.
# Each step: (step number, monthly sales needed in rupees, flat payout in rupees).
# A rep is paid only the payout of the HIGHEST step they cross.
STEPS = [
    (1, 50_000, 1_000),
    (2, 75_000, 2_500),
    (3, 100_000, 5_000),
]
FOCUS_KICKER_PER_PIECE = 75  # rupees per focus-SKU piece...
KICKER_UNLOCK_STEP = 1       # ...paid only once the rep crosses this step,
                             # and then on ALL focus pieces sold that month.
RETURNS_NETTED = 0           # 0 = returns are NOT subtracted for payout.

# Products: 5 categories x 4 products = 20 SKUs.
# Within each category the names are listed cheapest first, because we
# hand out the random prices in ascending order (so "Chips 50g" is never
# the most expensive item in Snacks).
CATEGORIES = {
    "Snacks": ["Salted Potato Chips 50g", "Roasted Peanuts 200g",
               "Mixed Namkeen 400g", "Snack Gift Box 1kg"],
    "Beverages": ["Lemon Drink 250ml", "Tea Leaves 250g",
                  "Instant Coffee 200g", "Juice Pack 1L x12"],
    "Personal Care": ["Bath Soap 100g", "Herbal Shampoo 180ml",
                      "Face Cream 50g", "Grooming Kit"],
    "Home Care": ["Dish Wash Bar 200g", "Floor Cleaner 1L",
                  "Detergent Powder 2kg", "Mop and Bucket Set"],
    "Staples": ["Lentils 1kg", "Wheat Flour 5kg",
                "Basmati Rice 5kg", "Cooking Oil 5L"],
}
PRICE_MIN, PRICE_MAX = 50, 2_000
MARGIN_MIN, MARGIN_MAX = 0.15, 0.30              # normal products
FOCUS_MARGIN_MIN, FOCUS_MARGIN_MAX = 0.30, 0.40  # focus products earn more
FOCUS_SKU_COUNT = 4
# Focus SKUs are picked only from products priced at least this much, so
# each focus piece earns more margin (>= 0.30 x 300 = 90) than the 75-rupee
# kicker costs. Otherwise the company would lose money on every focus piece.
FOCUS_MIN_PRICE = 300

# Reps: 50 common first names, no surnames. They do not refer to anyone.
FIRST_NAMES = [
    "Aarav", "Aditi", "Akash", "Ananya", "Arjun", "Bhavna", "Chetan", "Deepa",
    "Dev", "Divya", "Farhan", "Gauri", "Harish", "Isha", "Jatin", "Kavya",
    "Kiran", "Lata", "Mohit", "Meera", "Nikhil", "Neha", "Om", "Pooja",
    "Pranav", "Rahul", "Riya", "Rohan", "Sanya", "Sameer", "Shreya", "Suresh",
    "Tanvi", "Tarun", "Uma", "Varun", "Vidya", "Vikram", "Yash", "Zoya",
    "Anil", "Bina", "Gopal", "Hema", "Imran", "Jaya", "Kunal", "Leela",
    "Naveen", "Ritu",
]

# One rep joins late, on day 10. Everyone else starts on day 1.
# Their average daily sales must be worked out over days 10-24 only.
LATE_STARTER = "R37"
LATE_START_DAY = 10

# One rep always gets a bulk order on a fixed day, so the "one big order
# inflates the projection" test case doesn't move when other settings change.
BULK_REP = "R05"
BULK_REP_DAY = 17

# Messy-data ingredients, so the coach is tested on realistic data.
NO_SALES_SHARE = 0.05     # about 5% of rep-days have no sales at all
RETURN_ROW_SHARE = 0.02   # about 2% of sales rows get a matching return row
BULK_ORDER_DAYS = 3       # 3 rep-days with one unusually large order
BULK_SIZE_MIN, BULK_SIZE_MAX = 4, 6  # a bulk order is 4-6x a normal day

# ---------------------------------------------------------------------------
# 2. THE SEGMENT MIX
# ---------------------------------------------------------------------------
# We want, at day 24:
#     20% top step   (10 reps)  - already past the highest step
#     30% on track   (15 reps)  - projected to cross their next step
#     30% in reach   (15 reps)  - short, needing 1.0x-1.5x their current pace;
#                                 5 chasing each of steps 1, 2 and 3
#     20% far behind (10 reps)  - needing more than 1.5x their current pace
#
# The projection is: sales so far + (average daily sales x days left).
# For a rep who started on day 1, average = sales / 24, so
#     projected = sales + sales/24 x 6 = 1.25 x sales so far.
# Needing more than 1.0x your pace is the same as being projected to miss
# the next step. So for each step:
#     on track:  1.25 x sales >= step               -> sales >= step / 1.25
#     in reach:  (step - sales)/6 <= 1.5 x sales/24 -> sales >= step / 1.375
# which gives these ranges for sales so far:
#     chasing step 1 (50,000):  in reach 36,364-39,999, on track 40,000+
#     chasing step 2 (75,000):  in reach 54,545-59,999, on track 60,000+
#     chasing step 3 (100,000): in reach 75,000-79,999, on track 80,000+
# (At day 20 the projection was 1.5 x sales, which put every rep past
# step 1 on track - so only day 23 or later allows in-reach reps for steps 2-3.)
#
# We give each rep a target "sales so far" that sits safely INSIDE their
# group's range (not on the edge), and build their daily sales to hit it.
#
# Each line: (group, number of reps, lowest target, highest target) in rupees.
# R37 and R05 are planned separately below, so they are not counted here.
SEGMENT_PLAN = [
    ("top_step",   10, 105_000, 125_000),  # past 100,000
    ("on_track",    5,  41_000,  48_000),  # heading for step 1 (needs >= 40,000)
    ("on_track",    5,  62_000,  73_000),  # heading for step 2 (needs >= 60,000)
    ("on_track",    4,  82_000,  96_000),  # heading for step 3 (needs >= 80,000); R05 is the 5th
    ("in_reach",    4,  36_900,  39_500),  # chasing step 1; R37 is the 5th
    ("in_reach",    5,  55_200,  59_300),  # chasing step 2
    ("in_reach",    5,  75_600,  79_400),  # chasing step 3
    ("far_behind", 10,  14_000,  34_000),  # below 36,364, chasing step 1
]
# The late starter is the 5th "in reach" rep chasing step 1. They have 15
# working days (days 10-24), so their projection is sales x 21/15 and their
# in-reach range is 31,250 to 35,714. Judged wrongly over 24 days, they
# would look far behind - a deliberate trap for the coach's maths.
LATE_STARTER_PLAN = ("in_reach", 32_000, 35_000)
# The bulk-order rep is the 5th "on track" rep heading for step 3.
BULK_REP_PLAN = ("on_track", 82_000, 96_000)


# ---------------------------------------------------------------------------
# 3. BUILD THE PRODUCTS
# ---------------------------------------------------------------------------

def make_skus(rng):
    """Return a list of 20 products, each a dictionary of its details."""
    skus = []
    number = 1
    for category, names in CATEGORIES.items():
        # Draw 4 random prices. We draw on a "log scale" so cheap items are
        # common and expensive ones rarer, like a real price list.
        prices = []
        for _ in names:
            raw = math.exp(rng.uniform(math.log(PRICE_MIN), math.log(PRICE_MAX)))
            prices.append(round(raw / 5) * 5)  # round to the nearest 5 rupees
        prices.sort()  # cheapest price goes to the first (cheapest) name

        for name, price in zip(names, prices):
            skus.append({
                "sku_id": f"S{number:02d}",
                "name": name,
                "category": category,
                "price": price,
                "margin_rate": round(rng.uniform(MARGIN_MIN, MARGIN_MAX), 2),
                "is_focus": 0,
            })
            number += 1

    # Pick 4 products to be focus SKUs and give them a higher margin.
    pricey_enough = [s for s in skus if s["price"] >= FOCUS_MIN_PRICE]
    for sku in rng.sample(pricey_enough, FOCUS_SKU_COUNT):
        sku["is_focus"] = 1
        sku["margin_rate"] = round(rng.uniform(FOCUS_MARGIN_MIN, FOCUS_MARGIN_MAX), 2)
    return skus


# ---------------------------------------------------------------------------
# 4. BUILD THE REPS AND DECIDE WHERE EACH ONE SHOULD LAND
# ---------------------------------------------------------------------------

def make_reps(rng):
    """Return 50 reps, each with a planned group and target sales so far."""
    reps = []
    for i, name in enumerate(FIRST_NAMES, start=1):
        rep_id = f"R{i:02d}"
        start_day = LATE_START_DAY if rep_id == LATE_STARTER else 1
        reps.append({"rep_id": rep_id, "first_name": name, "start_day": start_day})

    # Turn the plan into one slot per rep, e.g. 10 x "top_step", ...
    slots = []
    for group, count, low, high in SEGMENT_PLAN:
        for _ in range(count):
            slots.append((group, low, high))
    rng.shuffle(slots)  # so groups are not in rep-number order

    # Hand the shuffled slots to every rep except the two fixed test cases.
    for rep in reps:
        if rep["rep_id"] == LATE_STARTER:
            group, low, high = LATE_STARTER_PLAN
        elif rep["rep_id"] == BULK_REP:
            group, low, high = BULK_REP_PLAN
        else:
            group, low, high = slots.pop()
        rep["planned_group"] = group
        rep["target_low"], rep["target_high"] = low, high
        rep["target"] = rng.uniform(low, high)
    return reps


# ---------------------------------------------------------------------------
# 5. CHOOSE THE SPECIAL DAYS (bulk orders and no-sales days)
# ---------------------------------------------------------------------------

def pick_bulk_days(rng, reps):
    """Choose 3 (rep, day) pairs that get one unusually large order.

    We only give bulk orders to high sellers (top step, or on track for
    step 3), because a bulk order that size would swamp a small seller's
    whole month. R05 always gets one, on a fixed day; the rest are random.
    """
    bulk_days = {BULK_REP: BULK_REP_DAY}
    big_sellers = [r for r in reps
                   if r["rep_id"] != BULK_REP
                   and (r["planned_group"] == "top_step"
                        or (r["planned_group"] == "on_track" and r["target_low"] >= 80_000))]
    for rep in rng.sample(big_sellers, BULK_ORDER_DAYS - 1):
        bulk_days[rep["rep_id"]] = rng.randint(2, TODAY)
    return bulk_days


def pick_no_sales_days(rng, reps, bulk_days):
    """Choose about 5% of all working rep-days to have zero sales."""
    possible = []
    for rep in reps:
        for day in range(rep["start_day"], TODAY + 1):
            if bulk_days.get(rep["rep_id"]) != day:  # never blank a bulk day
                possible.append((rep["rep_id"], day))
    how_many = round(len(possible) * NO_SALES_SHARE)
    # A sorted list (not a set) keeps the result identical on every run.
    return sorted(rng.sample(possible, how_many))


# ---------------------------------------------------------------------------
# 6. BUILD EACH REP'S DAILY SALES
# ---------------------------------------------------------------------------

def make_rep_sales(rng, rep, skus, selling_days, bulk_day):
    """Create sales rows for one rep that add up to their target.

    Returns a dictionary: (day, sku_id) -> quantity sold.
    """
    price = {s["sku_id"]: s["price"] for s in skus}
    sold = {}  # (day, sku_id) -> quantity
    budget = rep["target"]

    # Bulk order first: one cheap product in big quantity on one day.
    if bulk_day is not None:
        normal_day = rep["target"] / len(selling_days)
        bulk_value = normal_day * rng.uniform(BULK_SIZE_MIN, BULK_SIZE_MAX)
        cheap = [s for s in skus if s["price"] <= 400]
        sku = rng.choice(cheap)
        quantity = round(bulk_value / sku["price"])
        sold[(bulk_day, sku["sku_id"])] = quantity
        budget -= quantity * sku["price"]

    # Split the rest of the budget across selling days. Some days are
    # busier than others: each day gets a random weight between 0.5 and 1.5.
    weights = [rng.uniform(0.5, 1.5) for _ in selling_days]
    total_weight = sum(weights)

    for day, weight in zip(selling_days, weights):
        day_budget = budget * weight / total_weight
        # Fill the day like a shopping basket: keep adding one random piece
        # the rep can still "afford" until no product fits the budget left.
        while True:
            affordable = [s for s in skus if s["price"] <= day_budget]
            if not affordable:
                break
            sku = rng.choice(affordable)
            key = (day, sku["sku_id"])
            sold[key] = sold.get(key, 0) + 1
            day_budget -= sku["price"]

    # Each day stops a little short of its budget (the leftover is smaller
    # than the cheapest product). If that leaves the month total below the
    # rep's range, top it up one cheapest piece at a time.
    cheapest = min(skus, key=lambda s: s["price"])
    while sum(q * price[sku_id] for (_, sku_id), q in sold.items()) < rep["target_low"]:
        key = (rng.choice(selling_days), cheapest["sku_id"])
        sold[key] = sold.get(key, 0) + 1

    return sold


def make_returns(rng, sales_rows, selling_days_by_rep, bulk_days):
    """Create return rows (negative quantity) for about 2% of sales rows.

    A return happens on a later day than the sale, for the same product,
    and is never more than the quantity originally sold (max 3 pieces).
    """
    how_many = round(len(sales_rows) * RETURN_ROW_SHARE)
    taken = {(r["rep_id"], r["day"], r["sku_id"]) for r in sales_rows}
    returns = []
    while len(returns) < how_many:
        sale = rng.choice(sales_rows)
        if sale["day"] == TODAY or bulk_days.get(sale["rep_id"]) == sale["day"]:
            continue  # nothing can be returned after today; leave bulk orders alone
        later_days = [d for d in selling_days_by_rep[sale["rep_id"]] if d > sale["day"]]
        if not later_days:
            continue
        day = rng.choice(later_days)
        key = (sale["rep_id"], day, sale["sku_id"])
        if key in taken:
            continue  # keep one row per rep per day per product
        taken.add(key)
        quantity = -rng.randint(1, min(3, sale["quantity"]))
        returns.append({
            "rep_id": sale["rep_id"], "day": day, "sku_id": sale["sku_id"],
            "quantity": quantity, "value": quantity * sale["price"],
        })
    return returns


# ---------------------------------------------------------------------------
# 7. WRITE EVERYTHING TO THE DATABASE
# ---------------------------------------------------------------------------

def write_database(skus, reps, sales_rows):
    if DB_PATH.exists():
        DB_PATH.unlink()  # start fresh every time
    db = sqlite3.connect(DB_PATH)

    db.execute("""CREATE TABLE skus (
        sku_id TEXT PRIMARY KEY,
        name TEXT,
        category TEXT,
        price INTEGER,        -- rupees per piece
        margin_rate REAL,     -- gross margin as a share of price, e.g. 0.22
        is_focus INTEGER      -- 1 = focus SKU (earns the kicker), 0 = normal
    )""")
    db.executemany("INSERT INTO skus VALUES (?, ?, ?, ?, ?, ?)",
                   [(s["sku_id"], s["name"], s["category"], s["price"],
                     s["margin_rate"], s["is_focus"]) for s in skus])

    db.execute("""CREATE TABLE reps (
        rep_id TEXT PRIMARY KEY,
        first_name TEXT,
        start_day INTEGER     -- first day of the month the rep worked
    )""")
    db.executemany("INSERT INTO reps VALUES (?, ?, ?)",
                   [(r["rep_id"], r["first_name"], r["start_day"]) for r in reps])

    db.execute("""CREATE TABLE sales (
        sale_id INTEGER PRIMARY KEY,
        rep_id TEXT REFERENCES reps(rep_id),
        day INTEGER,          -- day of the month, 1-24
        sku_id TEXT REFERENCES skus(sku_id),
        quantity INTEGER,     -- pieces; negative = a return
        value INTEGER         -- rupees = quantity x price; negative = a return
    )""")
    db.executemany(
        "INSERT INTO sales (rep_id, day, sku_id, quantity, value) VALUES (?, ?, ?, ?, ?)",
        [(r["rep_id"], r["day"], r["sku_id"], r["quantity"], r["value"]) for r in sales_rows])

    db.execute("""CREATE TABLE scheme (
        step INTEGER PRIMARY KEY,
        threshold INTEGER,    -- monthly sales in rupees needed to cross this step
        payout INTEGER        -- flat rupees paid if this is the highest step crossed
    )""")
    db.executemany("INSERT INTO scheme VALUES (?, ?, ?)", STEPS)

    db.execute("""CREATE TABLE scheme_rules (
        rule TEXT PRIMARY KEY,
        value INTEGER,
        meaning TEXT
    )""")
    db.executemany("INSERT INTO scheme_rules VALUES (?, ?, ?)", [
        ("focus_kicker_per_piece", FOCUS_KICKER_PER_PIECE,
         "Rupees paid per focus-SKU piece sold in the month"),
        ("kicker_unlock_step", KICKER_UNLOCK_STEP,
         "The kicker is paid only after crossing this step, then on ALL focus pieces that month"),
        ("returns_netted", RETURNS_NETTED,
         "0 = returns are NOT subtracted from sales or pieces for payout"),
        ("month_days", MONTH_DAYS, "Days in the scheme month"),
        ("today", TODAY, "The current day; sales exist for days 1 to today"),
    ])

    db.commit()
    db.close()


# ---------------------------------------------------------------------------
# 8. RUN IT ALL
# ---------------------------------------------------------------------------

def main():
    rng = random.Random(SEED)  # the one source of randomness, seeded

    skus = make_skus(rng)
    reps = make_reps(rng)
    bulk_days = pick_bulk_days(rng, reps)
    no_sales_days = pick_no_sales_days(rng, reps, bulk_days)
    price = {s["sku_id"]: s["price"] for s in skus}

    sales_rows = []
    selling_days_by_rep = {}
    for rep in reps:
        rep_id = rep["rep_id"]
        selling_days = [d for d in range(rep["start_day"], TODAY + 1)
                        if (rep_id, d) not in no_sales_days]
        selling_days_by_rep[rep_id] = selling_days

        sold = make_rep_sales(rng, rep, skus, selling_days, bulk_days.get(rep_id))
        for (day, sku_id), quantity in sorted(sold.items()):
            sales_rows.append({
                "rep_id": rep_id, "day": day, "sku_id": sku_id,
                "quantity": quantity, "value": quantity * price[sku_id],
                "price": price[sku_id],
            })

    returns = make_returns(rng, sales_rows, selling_days_by_rep, bulk_days)
    all_rows = sorted(sales_rows + returns, key=lambda r: (r["rep_id"], r["day"], r["sku_id"]))
    write_database(skus, reps, all_rows)

    # A short receipt of what was written.
    rep_days = sum(TODAY - r["start_day"] + 1 for r in reps)
    print(f"Wrote {DB_PATH}")
    print(f"  {len(skus)} SKUs ({FOCUS_SKU_COUNT} focus), {len(reps)} reps")
    print(f"  {len(all_rows)} sales rows, of which {len(returns)} returns "
          f"({len(returns) / len(all_rows):.1%})")
    print(f"  {len(no_sales_days)} of {rep_days} rep-days with no sales "
          f"({len(no_sales_days) / rep_days:.1%})")
    print("  Bulk-order days: " + ", ".join(
        f"{rep_id} day {day}" for rep_id, day in sorted(bulk_days.items())))
    print("Run data/check.py to see the segment mix.")


if __name__ == "__main__":
    main()
