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
from logic import connect, load_reps, load_scheme, payout, segment  # noqa: E402


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
