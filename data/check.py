# Checks the segment mix in data/incentive.db.
#
# How to run (from the project folder):
#     uv run python data/check.py            # just the counts
#     uv run python data/check.py --detail   # also one line per rep
#
# It puts every rep into one of four groups, using the rules in docs/PRD.md,
# and prints how many reps landed in each group next to the target mix.
# The segment rules live in server/logic.py, the same file the MCP server
# uses, so this check and the coach always agree.

import sys
from pathlib import Path

# Let Python find server/logic.py from inside the data folder.
sys.path.insert(0, str(Path(__file__).parent.parent / "server"))
from logic import connect, load_reps, load_scheme, segment  # noqa: E402

# The mix we were aiming for, as a share of all reps.
TARGET_MIX = {"top_step": 0.20, "on_track": 0.30, "in_reach": 0.30, "far_behind": 0.20}


def main():
    db = connect()

    # Calendar and steps come from the database, so this script follows
    # any change made in generate.py.
    scheme = load_scheme(db)
    today = scheme["today"]
    days_left = scheme["days_left"]
    thresholds = scheme["thresholds"]
    rules = {"month_days": scheme["month_days"]}

    # Sales so far per rep (returns not netted; see load_reps).
    reps = [(r["rep_id"], r["name"], r["start_day"], r["sales"]) for r in load_reps(db)]

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
