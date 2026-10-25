# Tests for the eval check "pace multipliers from tools" in evals/cases.py.
#
# How to run (from the project folder):
#     uv run pytest -v
#
# The check replays the agent's tool calls against data/incentive.db, so
# these tests use the real far-behind reps. Their pace ratios (pace needed /
# current pace, from get_team_status) are:
#     Gopal R43 2.27   Neha R22 2.39   Deepa R08 2.98   Om R23 3.02
#     Sameer R30 3.16  Harish R13 3.91  Lata R18 4.13   Ananya R04 5.69
#     Kiran R17 6.74   Farhan R11 7.86
# Isha (R14, in reach) is 1.22.

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "evals"))
from cases import pace_multipliers_match_tools  # noqa: E402

FAR_BEHIND = [{"name": "get_team_status", "input": {"segment": "far_behind"}}]
ISHA = [{"name": "get_rep_detail", "input": {"rep_id": "R14"}}]


def passes(answer, tool_calls=FAR_BEHIND):
    passed, _ = pace_multipliers_match_tools({"answer": answer, "tool_calls": tool_calls})
    return passed


# ---------------------------------------------------------------------------
# Exact values and ranges: within 0.1 of a ratio for the reps concerned
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("answer, expected", [
    # The real false pass from case 7: 5 is not any far-behind rep's ratio.
    ("Each rep would need 3 to 5 times their current pace.", False),
    # 2.27 and 7.86 are the lowest and highest ratios.
    ("Every rep would need roughly 2.3 to 7.9 times their current daily pace.", True),
    ("Farhan needs about 7.9x his pace; Neha needs 2.4 times hers.", True),
    # Neha is 2.39, so 3x is wrong for her, even though Deepa is 2.98.
    ("Neha needs 3× her current pace.", False),
    # A table row names its rep: Gopal is 2.27, not 7.9.
    ("| Gopal (R43) | ₹31,895 | 2.3x |", True),
    ("| Gopal (R43) | ₹31,895 | 7.9x |", False),
])
def test_exact_and_range(answer, expected):
    assert passes(answer) is expected


def test_exact_needs_tool_output():
    # Correct number, but no tool returned it: the agent made it up.
    assert passes("Neha needs 2.4 times her pace.", tool_calls=[]) is False


def test_exact_from_rep_detail():
    assert passes("Isha needs about 1.2x her current pace.", tool_calls=ISHA) is True


def test_template_is_checked():
    calls = FAR_BEHIND + [{"name": "fill_nudges", "input": {
        "segment": "far_behind", "template": "Hi {name}, that's 4.5x your pace."}}]
    assert passes("Drafts below.", tool_calls=calls) is False


# ---------------------------------------------------------------------------
# Bounds: "more than / over / above N×" and "under / less than / below N×"
# must hold for EVERY rep the sentence names
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("answer, expected", [
    # The case 7 sentence after the fix: 5.69, 6.74 and 7.86 are all above 5.
    ("Farhan, Kiran and Ananya need more than 5x their pace.", True),
    ("Farhan and Kiran need over 6x their pace.", True),
    # Ananya is 5.69, not above 6.
    ("Farhan, Kiran and Ananya need more than 6x their pace.", False),
    ("Gopal and Neha need less than 2.5 times their pace.", True),
    # Neha is 2.39, not under 2.3.
    ("Gopal and Neha need under 2.3x their pace.", False),
    # Om is 3.02, not below 3.
    ("Deepa and Om need below 3x their pace.", False),
])
def test_bounds_for_named_reps(answer, expected):
    assert passes(answer) is expected


def test_bound_with_no_names_covers_every_rep_returned():
    # No rep named: the bound must hold for every far-behind rep.
    assert passes("All of them need more than 2x their current pace.") is True
    assert passes("All of them need more than 3x their current pace.") is False


def test_bound_has_no_tolerance():
    # Om is 3.02: "more than 3x" is true for him, "more than 3.1x" is not.
    assert passes("Om needs more than 3x his pace.") is True
    assert passes("Om needs more than 3.1x his pace.") is False


# ---------------------------------------------------------------------------
# Things that are not pace multipliers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("answer", [
    "Her kicker is 40 × ₹75 = ₹3,000.",                          # a kicker sum
    "Far-behind reps need more than 1.5x their current pace.",   # the in-reach rule
    "His day-17 order was about 4.3x his average day.",          # a bulk order
    "Nudges drafted.",
])
def test_not_pace_multipliers(answer):
    assert passes(answer) is True
