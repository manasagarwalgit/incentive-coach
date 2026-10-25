# Checks the segment mix in data/incentive.db.
#
# How to run (from the project folder):
#     uv run python data/check.py            # just the counts
#     uv run python data/check.py --detail   # also one line per rep
#
# It puts every rep into one of four groups, using the rules in docs/PRD.md,
# and prints how many reps landed in each group next to the target mix.
# This is a test of the DATA, not the coach: the real segmenting tool will
# be written separately and should agree with these numbers.

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).parent / "incentive.db"

# A rep is "in reach" if the pace they need is at most this many times
# their current pace. Above it, they are "far behind". (PRD parameter.)
PACE_MULTIPLIER = 1.5

# The mix we were aiming for, as a share of all reps.
TARGET_MIX = {"top_step": 0.20, "on_track": 0.30, "in_reach": 0.30, "far_behind": 0.20}


def segment(sales_so_far, start_day, today, days_left, thresholds):
    """Return (group, details) for one rep. All amounts in rupees."""
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


def main():
    db = sqlite3.connect(DB_PATH)

    # Calendar and steps come from the database, so this script follows
    # any change made in generate.py.
    rules = dict(db.execute("SELECT rule, value FROM scheme_rules"))
    today = rules["today"]
    days_left = rules["month_days"] - today
    thresholds = [t for (t,) in db.execute("SELECT threshold FROM scheme ORDER BY threshold")]

    # Sales so far per rep. Returns are NOT netted for payout, so progress
    # toward a step counts only positive rows (quantity > 0).
    reps = db.execute("""
        SELECT r.rep_id, r.first_name, r.start_day,
               COALESCE(SUM(CASE WHEN s.quantity > 0 THEN s.value END), 0) AS sales
        FROM reps r LEFT JOIN sales s ON s.rep_id = r.rep_id
        GROUP BY r.rep_id ORDER BY r.rep_id
    """).fetchall()

    counts = {group: 0 for group in TARGET_MIX}
    in_reach_by_step = {t: 0 for t in thresholds}  # next step -> in-reach reps
    show_detail = "--detail" in sys.argv
    if show_detail:
        print(f"{'rep':<4} {'name':<8} {'start':>5} {'sales':>9} {'pace/day':>9} "
              f"{'projected':>10} {'next step':>9} {'need/day':>9} {'need/pace':>9}  group")

    for rep_id, name, start_day, sales in reps:
        group, d = segment(sales, start_day, today, days_left, thresholds)
        counts[group] += 1
        if group == "in_reach":
            in_reach_by_step[d["next_step"]] += 1
        if show_detail:
            next_step = f"{d['next_step']:,}" if "next_step" in d else "-"
            needed = f"{d['needed_pace']:,.0f}" if "needed_pace" in d else "-"
            # How many times their current pace they need to sell at.
            ratio = (f"{d['needed_pace'] / d['pace']:.2f}x"
                     if "needed_pace" in d and d["pace"] > 0 else "-")
            print(f"{rep_id:<4} {name:<8} {start_day:>5} {sales:>9,} {d['pace']:>9,.0f} "
                  f"{d['projected']:>10,.0f} {next_step:>9} {needed:>9} {ratio:>9}  {group}")

    total = len(reps)
    print(f"\nSegment mix at day {today} of {rules['month_days']} ({total} reps)")
    print(f"{'group':<11} {'reps':>4} {'share':>6} {'target':>7}")
    for group, target in TARGET_MIX.items():
        print(f"{group:<11} {counts[group]:>4} {counts[group] / total:>6.0%} {target:>7.0%}")

    print("\nIn-reach reps by the step they are chasing")
    for step_number, threshold in enumerate(thresholds, start=1):
        print(f"step {step_number} ({threshold:>7,}) {in_reach_by_step[threshold]:>4}")


if __name__ == "__main__":
    main()
