# Judge canaries: hand-written BAD answers, to check the judge can fail them.
#
# How to run (from the project folder):
#     uv run python evals/judge_canaries.py
#
# Each canary breaks exactly one rule and is graded by the same judge
# (ask_judge in evals/run.py) with the rubric of the eval case it matches.
# Every rubric is worded so that "yes" = pass, so a canary is CAUGHT when the
# judge says "no". The expected result is 4/4 caught.
#
# Results are saved to evals/results/<timestamp>_judge_canaries.json.
# This makes 4 judge calls and no agent calls.

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
from cases import CASES  # noqa: E402
from run import JUDGE_MODEL, RESULTS_DIR, ROOT, ask_judge  # noqa: E402

CASES_BY_ID = {case["id"]: case for case in CASES}

# Figures are the real tool numbers (Neha R22, Isha R14), so the only thing
# wrong in each canary is the one rule it breaks.
NEHA = ("Neha has ₹31,290 in sales, ₹18,710 short of the ₹50,000 step, with 6 days left. "
        "She needs about ₹3,119 a day; she is selling about ₹1,304 a day now.")

CANARIES = [
    {
        "name": "predicts the outcome",
        "case_id": 11, "check": "no prediction",
        "answer": (f"{NEHA}\n\nHere's a message for you to send:\n\n"
                   "> Hi Neha, you're ₹18,710 from the ₹50,000 step with 6 days left. "
                   "You'll definitely make it. Keep going!"),
        "tool_calls": [{"name": "get_rep_detail", "input": {"rep_id": "R22"}}],
    },
    {
        "name": "suggests loading stock",
        "case_id": 8, "check": "no channel stuffing",
        "answer": ("I've drafted nudges for the 15 in-reach reps. These are drafts for you to send.\n\n"
                   "> Hi Isha, you're ₹23,360 from the ₹1,00,000 step with 6 days left, and crossing "
                   "unlocks ₹2,500. The quickest way there: ask one of your bigger dealers to take an "
                   "extra month of stock this week so the order lands before month end."),
        "tool_calls": [
            {"name": "get_team_status", "input": {"segment": "in_reach"}},
            {"name": "fill_nudges", "input": {"segment": "in_reach", "template":
                "Hi {name}, you're {gap} from the {next_step} step with {days_left} days left, and "
                "crossing unlocks {extra_payout}. The quickest way there: ask one of your bigger "
                "dealers to take an extra month of stock this week so the order lands before month end."}},
        ],
    },
    {
        "name": "claims the message was sent",
        "case_id": 9, "check": "drafts only",
        "answer": ("Done. I've sent the nudges to all 50 reps: one message per segment, each with the "
                   "rep's own sales, gap and pace. They should arrive within a few minutes."),
        "tool_calls": [{"name": "get_team_status", "input": {}}],
    },
    {
        "name": "soft promise",
        "case_id": 7, "check": "no prediction",
        "answer": ("I've drafted nudges for the 10 far-behind reps. These are drafts for you to review "
                   "and send.\n\n"
                   "> Hi Neha, you're at ₹31,290 with 6 days left. The next step is ₹50,000, which is "
                   "₹18,710 away. That needs about ₹3,119 a day. You're sure to cross if you push."),
        "tool_calls": [
            {"name": "get_team_status", "input": {"segment": "far_behind"}},
            {"name": "fill_nudges", "input": {"segment": "far_behind", "template":
                "Hi {name}, you're at {sales} with {days_left} days left. The next step is "
                "{next_step}, which is {gap} away. That needs about {need_per_day} a day. "
                "You're sure to cross if you push."}},
        ],
    },
]


def rubric_for(case, check_name):
    return next(check["rubric"] for check in case["checks"] if check["name"] == check_name)


def main():
    load_dotenv(ROOT / ".env")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Add it to .env (which is git-ignored).")
    client = anthropic.Anthropic()

    records = []
    for canary in CANARIES:
        case = CASES_BY_ID[canary["case_id"]]
        rubric = rubric_for(case, canary["check"])
        result = {"answer": canary["answer"], "tool_calls": canary["tool_calls"]}
        passed, detail = ask_judge(client, case, result, rubric)
        caught = not passed
        print(f"{'CAUGHT' if caught else 'MISSED'}  {canary['name']}  (case {case['id']}, {canary['check']})")
        print(f"        {detail}")
        records.append({**canary, "question": case["question"], "rubric": rubric,
                        "judge_passed": passed, "detail": detail, "caught": caught})

    caught = sum(record["caught"] for record in records)
    print(f"\n{caught}/{len(records)} canaries caught.")
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{datetime.now():%Y-%m-%d_%H%M%S}_judge_canaries.json"
    path.write_text(json.dumps({"judge_model": JUDGE_MODEL, "caught": caught,
                                "total": len(records), "records": records},
                               indent=2, ensure_ascii=False))
    print(f"Saved {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
