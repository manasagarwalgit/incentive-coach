# The business rules of the Incentive Coach, in one place.
#
# Everything that decides a number lives here: how sales are counted, which
# segment a rep is in, and what a rep is paid. Both the MCP server
# (server/main.py) and the data check (data/check.py) import this file, so
# they can never disagree. The rules follow docs/PRD.md.
#
# Nothing in this file talks to the model or formats text. It only reads the
# database and does arithmetic.

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "incentive.db"

# A rep is "in reach" if the pace they need is at most this many times
# their current pace. Above it, they are "far behind". (PRD parameter.)
PACE_MULTIPLIER = 1.5

# The four segments, in the order the PRD lists them.
SEGMENTS = ["top_step", "on_track", "in_reach", "far_behind"]

# A single sales row worth at least this many times the rep's average day
# is flagged as a bulk order. The three bulk orders in the data are 4.3x to
# 6x; the largest ordinary row is about 1.4x, so 3x separates them cleanly.
BULK_ORDER_MULTIPLIER = 3


# ---------------------------------------------------------------------------
# Reading the database
# ---------------------------------------------------------------------------

def connect():
    """Open the database read-only, so no tool can ever change the data."""
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def load_scheme(db):
    """Return the scheme as a dict: calendar, steps and kicker rules."""
    rules = dict(db.execute("SELECT rule, value FROM scheme_rules"))
    steps = db.execute("SELECT step, threshold, payout FROM scheme ORDER BY threshold").fetchall()
    return {
        "today": rules["today"],
        "month_days": rules["month_days"],
        "days_left": rules["month_days"] - rules["today"],
        # List of (step number, rupee threshold, flat payout), lowest first.
        "steps": steps,
        "thresholds": [threshold for _, threshold, _ in steps],
        "kicker_per_piece": rules["focus_kicker_per_piece"],
        "kicker_unlock_step": rules["kicker_unlock_step"],
    }


def load_reps(db):
    """Return one dict per rep with their sales, focus pieces and margin.

    Returns are NOT netted for payout, so everything here counts only
    positive rows (quantity > 0). The returns stay in the data for the
    next-month return-rate guardrail, but they never reduce progress.
    """
    rows = db.execute("""
        SELECT r.rep_id, r.first_name, r.start_day,
               COALESCE(SUM(CASE WHEN s.quantity > 0 THEN s.value END), 0),
               COALESCE(SUM(CASE WHEN s.quantity > 0 AND k.is_focus = 1
                                 THEN s.quantity END), 0),
               COALESCE(SUM(CASE WHEN s.quantity > 0
                                 THEN s.value * k.margin_rate END), 0)
        FROM reps r
        LEFT JOIN sales s ON s.rep_id = r.rep_id
        LEFT JOIN skus k ON k.sku_id = s.sku_id
        GROUP BY r.rep_id ORDER BY r.rep_id
    """).fetchall()
    return [
        {
            "rep_id": rep_id,
            "name": name,
            "start_day": start_day,
            "sales": sales,
            "focus_pieces": focus_pieces,
            # Share of each rupee sold that is gross margin, based on this
            # rep's own product mix. Used to value the sales they still need.
            "margin_rate": margin / sales if sales else 0,
        }
        for rep_id, name, start_day, sales, focus_pieces, margin in rows
    ]


def find_bulk_orders(db, rep_id, average_per_day):
    """Return the rep's unusually large single orders as (day, value) pairs."""
    if average_per_day <= 0:
        return []
    return db.execute(
        "SELECT day, value FROM sales WHERE rep_id = ? AND quantity > 0 AND value >= ? "
        "ORDER BY day",
        (rep_id, BULK_ORDER_MULTIPLIER * average_per_day),
    ).fetchall()


# ---------------------------------------------------------------------------
# Segments
# ---------------------------------------------------------------------------

def segment(sales_so_far, start_day, today, days_left, thresholds):
    """Return (group, details) for one rep. All amounts in rupees."""
    # Pace is averaged over the days since THIS rep started, days included.
    # A late starter (e.g. R37, day 10) has fewer days, so a higher pace.
    days_worked = today - start_day + 1
    current_pace = sales_so_far / days_worked          # average per day so far
    projected = sales_so_far + current_pace * days_left

    # Top step: already past the highest step.
    if sales_so_far >= thresholds[-1]:
        return "top_step", {"pace": current_pace, "projected": projected}

    # Otherwise compare against the next step they have not reached yet.
    next_step = min(t for t in thresholds if t > sales_so_far)
    needed_pace = (next_step - sales_so_far) / days_left
    details = {"pace": current_pace, "projected": projected,
               "next_step": next_step, "needed_pace": needed_pace}

    if projected >= next_step:
        return "on_track", details
    if current_pace > 0 and needed_pace <= PACE_MULTIPLIER * current_pace:
        return "in_reach", details
    return "far_behind", details


# ---------------------------------------------------------------------------
# Payout
# ---------------------------------------------------------------------------

def payout(sales, focus_pieces, scheme):
    """Return the payout for a month's sales, split into its two parts.

    Payout = flat payout for the highest step crossed
             + (if the unlock step is crossed) kicker x ALL focus pieces.
    """
    highest_step, step_payout = 0, 0
    for step, threshold, flat in scheme["steps"]:
        if sales >= threshold:
            highest_step, step_payout = step, flat

    kicker_unlocked = highest_step >= scheme["kicker_unlock_step"]
    focus_kicker = focus_pieces * scheme["kicker_per_piece"] if kicker_unlocked else 0

    return {
        "highest_step_crossed": highest_step,   # 0 = no step crossed yet
        "step_payout": step_payout,
        "kicker_unlocked": kicker_unlocked,
        "focus_kicker": focus_kicker,
        "total": step_payout + focus_kicker,
    }


def payout_totals(reps, scheme):
    """Add up payouts across reps, now and if every in-reach rep crosses.

    "Crossing" means an in-reach rep sells exactly up to their next step,
    with no extra focus pieces. Reps in the other segments stay as they are,
    so for them the "if crossed" figure equals the "now" figure.

    Returns {"by_segment": {segment: totals}, "team": totals}, where each
    totals dict has rep_count, payout_now, payout_if_in_reach_cross and
    extra_cost (= payout_if_in_reach_cross - payout_now).
    """
    def empty():
        return {"rep_count": 0, "payout_now": 0, "payout_if_in_reach_cross": 0, "extra_cost": 0}

    by_segment = {name: empty() for name in SEGMENTS}
    team = empty()
    for rep in reps:
        group, details = segment(rep["sales"], rep["start_day"], scheme["today"],
                                 scheme["days_left"], scheme["thresholds"])
        now = payout(rep["sales"], rep["focus_pieces"], scheme)["total"]
        if group == "in_reach":
            after = payout(details["next_step"], rep["focus_pieces"], scheme)["total"]
        else:
            after = now
        for totals in (by_segment[group], team):
            totals["rep_count"] += 1
            totals["payout_now"] += now
            totals["payout_if_in_reach_cross"] += after
            totals["extra_cost"] += after - now
    return {"by_segment": by_segment, "team": team}
