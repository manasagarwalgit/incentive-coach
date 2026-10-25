# Re-scores saved results files with the CURRENT checks in evals/cases.py,
# without calling the API.
#
# How to run (from the project folder):
#     uv run python evals/rescore.py evals/results/2026-10-25_175405.json ...
#
# For each saved run:
#   - code checks are run again on the saved answer and tool calls;
#   - judge checks reuse the verdict saved in the file, matched by check
#     name (asking the judge again would be a new API call);
#   - "either" checks run the code part and fall back to the saved verdict;
#   - runs that hit an API error stay "error".
# A judge check that has no saved verdict can't be scored offline, so the
# run is reported as "unscored" and left out of the pass rate.
#
# Nothing is written; a table is printed, one row per file, plus every run
# whose outcome changed.

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "evals"))

from cases import CASES  # noqa: E402

CASES_BY_ID = {case["id"]: case for case in CASES}


def rescore_record(record):
    """Return (outcome, failed check names) for one saved run under the current checks."""
    if record["outcome"] == "error":
        return "error", []
    saved = {check["name"]: check for check in record["checks"]}
    failed = []
    for check in CASES_BY_ID[record["case_id"]]["checks"]:
        if check["method"] in ("code", "either"):
            passed, _ = check["fn"](record)
            if passed or check["method"] == "code":
                if not passed:
                    failed.append(check["name"])
                continue
        # judge, or "either" whose code part said no: use the saved verdict
        if check["name"] not in saved:
            return "unscored", [check["name"]]
        if not saved[check["name"]]["passed"]:
            failed.append(check["name"])
    return ("pass" if not failed else "fail"), failed


def rate(outcomes):
    scored = [outcome for outcome in outcomes if outcome in ("pass", "fail")]
    return f"{scored.count('pass')}/{len(scored)}"


def main():
    rows, changes = [], []
    for name in sys.argv[1:]:
        path = Path(name)
        data = json.loads(path.read_text())
        before, after = [], []
        for record in data["records"]:
            outcome, failed = rescore_record(record)
            before.append(record["outcome"])
            after.append(outcome)
            if outcome != record["outcome"]:
                changes.append(f"{path.stem}  case {record['case_id']:>2} run {record['run']}: "
                               f"{record['outcome']} -> {outcome}  {failed}")
        extra = []
        for label in ("error", "unscored"):
            if label in after:
                extra.append(f"{after.count(label)} {label}")
        rows.append((path.stem, data["set"], data.get("system_prompt", "agent/CLAUDE.md"),
                     rate(before), rate(after), ", ".join(extra)))

    print(f"{'file':<18} {'set':<9} {'prompt':<24} {'original':>8} {'re-scored':>9}")
    for stem, set_name, prompt, before, after, extra in rows:
        print(f"{stem:<18} {set_name:<9} {prompt:<24} {before:>8} {after:>9}  {extra}")
    print("\nRuns whose outcome changed:" if changes else "\nNo outcomes changed.")
    for line in changes:
        print("  " + line)


if __name__ == "__main__":
    main()
