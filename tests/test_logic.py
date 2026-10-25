# Tests for the business rules in server/logic.py.
#
# How to run (from the project folder):
#     uv run pytest -v
#
# These tests never call the model. They check the arithmetic that every
# tool relies on. The expected numbers below are worked out by hand from the
# scheme in docs/PRD.md, so a test fails if logic.py ever drifts from it:
#     step 1 = ₹50,000 -> ₹1,000    step 2 = ₹75,000 -> ₹2,500
#     step 3 = ₹1,00,000 -> ₹5,000  kicker = ₹75 per focus piece,
#     paid on ALL focus pieces, but only once step 1 is crossed.

import sys
from pathlib import Path

import pytest

# Let Python find server/logic.py (same trick as data/check.py).
sys.path.insert(0, str(Path(__file__).parent.parent / "server"))
from logic import connect, load_reps, load_scheme, payout, payout_totals, segment  # noqa: E402


# ---------------------------------------------------------------------------
# Shared setup: read the real scheme and reps from data/incentive.db once.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def scheme():
    return load_scheme(connect())


@pytest.fixture(scope="module")
def reps():
    return {rep["rep_id"]: rep for rep in load_reps(connect())}


# ---------------------------------------------------------------------------
# Payout maths for three real reps
# ---------------------------------------------------------------------------

def test_payout_isha_r14(scheme, reps):
    # Isha: ₹76,640 sales, 40 focus pieces.
    # Past step 2 (₹75,000) -> ₹2,500, kicker unlocked: 40 x ₹75 = ₹3,000.
    isha = reps["R14"]
    assert (isha["sales"], isha["focus_pieces"]) == (76640, 40)
    result = payout(isha["sales"], isha["focus_pieces"], scheme)
    assert result["highest_step_crossed"] == 2
    assert result["step_payout"] == 2500
    assert result["focus_kicker"] == 3000
    assert result["total"] == 5500


def test_payout_vidya_r37(scheme, reps):
    # Vidya: ₹33,280 sales, 18 focus pieces. Below step 1, so she earns
    # nothing yet, and her 18 pieces don't count until she crosses.
    vidya = reps["R37"]
    assert (vidya["sales"], vidya["focus_pieces"]) == (33280, 18)
    result = payout(vidya["sales"], vidya["focus_pieces"], scheme)
    assert result["highest_step_crossed"] == 0
    assert result["kicker_unlocked"] is False
    assert result["total"] == 0


def test_payout_arjun_r05(scheme, reps):
    # Arjun: ₹95,435 sales, 42 focus pieces.
    # Past step 2 -> ₹2,500, kicker 42 x ₹75 = ₹3,150, total ₹5,650.
    arjun = reps["R05"]
    assert (arjun["sales"], arjun["focus_pieces"]) == (95435, 42)
    result = payout(arjun["sales"], arjun["focus_pieces"], scheme)
    assert result["highest_step_crossed"] == 2
    assert result["focus_kicker"] == 3150
    assert result["total"] == 5650


# ---------------------------------------------------------------------------
# Late starter: pace uses days since THIS rep started, not the whole month
# ---------------------------------------------------------------------------

def test_late_starter_pace_uses_days_since_start(scheme, reps):
    # Vidya started on day 10. Today is day 24, so she has worked
    # 24 - 10 + 1 = 15 days (both ends included), not 24.
    vidya = reps["R37"]
    assert vidya["start_day"] == 10
    _, details = segment(vidya["sales"], vidya["start_day"], scheme["today"],
                         scheme["days_left"], scheme["thresholds"])
    assert details["pace"] == pytest.approx(33280 / 15)
    # Dividing by 24 would make her look much slower than she is.
    assert details["pace"] != pytest.approx(33280 / 24)


# ---------------------------------------------------------------------------
# Kicker stays locked until step 1 is crossed
# ---------------------------------------------------------------------------

def test_kicker_locked_below_step_1(scheme):
    # ₹1 short of step 1: no step, no kicker, even with 100 focus pieces.
    below = payout(49999, 100, scheme)
    assert below["kicker_unlocked"] is False
    assert below["focus_kicker"] == 0
    assert below["total"] == 0

    # Exactly on step 1: ₹1,000 plus the kicker on ALL 100 pieces (₹7,500).
    at_step = payout(50000, 100, scheme)
    assert at_step["kicker_unlocked"] is True
    assert at_step["focus_kicker"] == 7500
    assert at_step["total"] == 8500


# ---------------------------------------------------------------------------
# The 1.5x boundary between in_reach and far_behind
# ---------------------------------------------------------------------------
# Made-up inputs, chosen so the numbers come out round:
#   today = 10, start_day = 1 -> 10 days worked, days_left = 10
#   sales = ₹10,000 -> current pace = ₹1,000 a day
#   The one step is set so the pace NEEDED is 1.49x, 1.50x or 1.51x that:
#     step ₹24,900 -> need (24,900 - 10,000) / 10 = ₹1,490 a day = 1.49x
#     step ₹25,100 -> need ₹1,510 a day = 1.51x
# Projected month-end is ₹20,000, below the step, so none are "on_track".

def boundary_case(step):
    group, _ = segment(sales_so_far=10000, start_day=1, today=10,
                       days_left=10, thresholds=[step])
    return group


def test_boundary_1_49x_is_in_reach():
    assert boundary_case(24900) == "in_reach"


def test_boundary_1_50x_exactly_is_in_reach():
    # The PRD rule is "at most 1.5x", so exactly 1.5x is still in reach.
    assert boundary_case(25000) == "in_reach"


def test_boundary_1_51x_is_far_behind():
    assert boundary_case(25100) == "far_behind"


# ---------------------------------------------------------------------------
# Pace multiple: pace needed / current pace
# ---------------------------------------------------------------------------

def test_pace_multiple_isha_r14(scheme, reps):
    # Isha: ₹76,640 over 24 days -> pace ₹3,193.33 a day.
    # Gap to ₹1,00,000 is ₹23,360 over 6 days -> needs ₹3,893.33 a day.
    # 3,893.33 / 3,193.33 = 1.2192... -> 1.22 to 2 decimals.
    isha = reps["R14"]
    _, details = segment(isha["sales"], isha["start_day"], scheme["today"],
                         scheme["days_left"], scheme["thresholds"])
    assert round(details["pace_multiple"], 2) == 1.22


def test_pace_multiple_matches_boundary_inputs():
    # The 1.49x boundary inputs above: need ₹1,490 a day, pace ₹1,000 a day.
    _, details = segment(sales_so_far=10000, start_day=1, today=10,
                         days_left=10, thresholds=[24900])
    assert details["pace_multiple"] == pytest.approx(1.49)


# ---------------------------------------------------------------------------
# Payout totals across reps
# ---------------------------------------------------------------------------
# Five made-up reps, all starting on day 1, with the real calendar
# (today = 24, 6 days left). Pace = sales / 24, projected = sales x 30/24.
#
#   rep  sales     pieces  segment     payout now          if in-reach cross
#   A    1,10,000  20      top_step    5,000 + 1,500 = 6,500   6,500
#   B      96,000  30      on_track    2,500 + 2,250 = 4,750   4,750
#          (pace 4,000, projected 1,20,000 >= 1,00,000)
#   C      57,600  20      in_reach    1,000 + 1,500 = 2,500   at 75,000: 2,500 + 1,500 = 4,000
#          (pace 2,400, projected 72,000; needs 2,900/day <= 1.5 x 2,400)
#   D      38,400  10      in_reach    0 (below step 1)        at 50,000: 1,000 + 750 = 1,750
#          (pace 1,600, projected 48,000; needs 1,934/day <= 1.5 x 1,600)
#   E      24,000   8      far_behind  0                       0
#          (pace 1,000; needs 4,334/day > 1.5 x 1,000)
#
# Team now = 6,500 + 4,750 + 2,500 + 0 + 0 = 13,750
# Team if crossed = 6,500 + 4,750 + 4,000 + 1,750 + 0 = 17,000, extra 3,250.

def test_payout_totals_hand_worked(scheme):
    assert (scheme["today"], scheme["days_left"]) == (24, 6)
    reps = [
        {"sales": 110000, "focus_pieces": 20, "start_day": 1},
        {"sales": 96000, "focus_pieces": 30, "start_day": 1},
        {"sales": 57600, "focus_pieces": 20, "start_day": 1},
        {"sales": 38400, "focus_pieces": 10, "start_day": 1},
        {"sales": 24000, "focus_pieces": 8, "start_day": 1},
    ]
    totals = payout_totals(reps, scheme)

    assert totals["team"] == {"rep_count": 5, "payout_now": 13750,
                              "payout_if_in_reach_cross": 17000, "extra_cost": 3250}
    assert totals["by_segment"]["in_reach"] == {"rep_count": 2, "payout_now": 2500,
                                                "payout_if_in_reach_cross": 5750,
                                                "extra_cost": 3250}
    # Only in-reach reps change.
    for group, now in [("top_step", 6500), ("on_track", 4750), ("far_behind", 0)]:
        assert totals["by_segment"][group]["payout_now"] == now
        assert totals["by_segment"][group]["extra_cost"] == 0
