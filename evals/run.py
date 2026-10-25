# Runs the eval cases in evals/cases.py against the Incentive Coach agent.
#
# How to run (from the project folder):
#     uv run python evals/run.py --dry-run              # no API calls, just checks the setup
#     uv run python evals/run.py --set dev              # dev cases, 3 runs each
#     uv run python evals/run.py --set held_out --runs 5
#
# What one run of one case does:
#   1. Send the manager's question to the agent model, with agent/CLAUDE.md
#      as the system prompt and the four MCP tools from server/main.py.
#   2. Whenever the agent asks for a tool, call that Python function
#      directly (no MCP server process needed) and send back the result.
#   3. When the agent stops asking for tools, take its final answer.
#   4. Score every check: code checks in Python, judge checks by asking a
#      judge model a yes/no question.
#
# Results are saved to evals/results/<timestamp>.json (committed as
# evidence) and a pass-rate table is printed.
#
# Each run ends as "pass", "fail" or "error". "error" means an API call broke
# (overloaded, rate limited, network...), so the agent was never really
# tested; those runs are counted separately and left out of the pass rate.
# If the very first API call fails on the key, permissions or billing, the
# whole run stops at once, since every other case would fail the same way.
#
# The API key is read from ANTHROPIC_API_KEY in .env and is never printed.

import argparse
import asyncio
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "server"))   # so main.py can `from logic import ...`
sys.path.insert(0, str(ROOT / "evals"))

import main as server  # noqa: E402  (server/main.py: the four tool functions)
from cases import CASES, cases_for  # noqa: E402

AGENT_MODEL = "claude-sonnet-5-5"
JUDGE_MODEL = "claude-opus-5-5"
SYSTEM_PROMPT = (ROOT / "agent" / "CLAUDE.md").read_text()
RESULTS_DIR = ROOT / "evals" / "results"

# Stop a run if the agent keeps calling tools without ever answering.
MAX_TURNS = 10

# How many API calls have succeeded so far (see create_message).
api_calls_ok = 0

# The four tools, by name, as plain Python functions.
TOOL_FUNCTIONS = {
    fn.__name__: fn
    for fn in [server.get_team_status, server.get_rep_detail,
               server.calculate_payout, server.fill_nudges]
}


# ---------------------------------------------------------------------------
# Tool definitions for the API
# ---------------------------------------------------------------------------

def tool_definitions():
    """Ask the MCP server for its tool list and turn it into API tool definitions.

    Taking names, descriptions and input schemas from the server itself means
    the eval agent sees exactly what Claude Code sees, word for word.
    """
    mcp_tools = asyncio.run(server.mcp.list_tools())
    return [
        {"name": tool.name, "description": tool.description, "input_schema": tool.input_schema}
        for tool in mcp_tools
    ]


def call_tool(name, tool_input):
    """Run one tool function and return (result text, is_error)."""
    if name not in TOOL_FUNCTIONS:
        return f"Unknown tool '{name}'.", True
    try:
        result = TOOL_FUNCTIONS[name](**tool_input)
    except Exception as e:  # a bad argument, e.g. a missing rep_id
        return f"{type(e).__name__}: {e}", True
    # Tools return {"error": ...} for problems the agent can fix; pass those
    # back as normal results, the way the MCP server does.
    return json.dumps(result, ensure_ascii=False), False


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------

def create_message(client, **kwargs):
    """Call the Messages API and count the call if it succeeds."""
    global api_calls_ok
    response = client.messages.create(**kwargs)
    api_calls_ok += 1
    return response


def is_account_error(e):
    """True for errors no retry will fix: a bad key, no permission, or no credit.

    The SDK has no class for billing errors, so those are recognised by
    status 402, the "billing_error" type, or the low-credit message.
    """
    if isinstance(e, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return True
    if not isinstance(e, anthropic.APIStatusError):
        return False
    body = e.body if isinstance(e.body, dict) else {}
    error = body.get("error") if isinstance(body.get("error"), dict) else {}
    return (e.status_code == 402
            or error.get("type") == "billing_error"
            or "credit balance" in str(e).lower())


# ---------------------------------------------------------------------------
# Running the agent on one question
# ---------------------------------------------------------------------------

def run_agent(client, tools, question):
    """Run the agent until it gives a final answer.

    Returns {"tool_calls": [{"name", "input"}...], "answer": str, "stop_reason": str}.
    """
    messages = [{"role": "user", "content": question}]
    tool_calls = []

    for _ in range(MAX_TURNS):
        # Thinking and effort are left at the model's defaults.
        response = create_message(
            client,
            model=AGENT_MODEL,
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )
        # Keep the full content (text, thinking and tool_use blocks) in the history.
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            break  # end_turn, max_tokens or refusal: the agent is done

        # Run every tool the agent asked for and send ALL results back in one message.
        results = []
        for block in response.content:
            if block.type == "tool_use":
                tool_calls.append({"name": block.name, "input": block.input})
                text, is_error = call_tool(block.name, block.input)
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": text, "is_error": is_error})
        messages.append({"role": "user", "content": results})

    answer = "\n".join(block.text for block in response.content if block.type == "text")
    return {"tool_calls": tool_calls, "answer": answer, "stop_reason": response.stop_reason}


# ---------------------------------------------------------------------------
# The judge
# ---------------------------------------------------------------------------

JUDGE_INSTRUCTIONS = """You grade one response from a sales-incentive assistant.
Answer the question about the response with only "yes" or "no".
Judge only what the response actually says. The drafted messages are part of the response."""


def ask_judge(client, case, result, rubric):
    """Ask the judge model a yes/no question about the agent's answer.

    Returns (passed, detail). Every rubric is worded so that "yes" = pass.
    The judge sees the question, the tool calls the agent made (so it can
    read the templates sent to fill_nudges) and the final answer.
    """
    prompt = (
        f"Manager's question:\n{case['question']}\n\n"
        f"Tool calls the assistant made:\n{json.dumps(result['tool_calls'], ensure_ascii=False)}\n\n"
        f"Assistant's final response:\n{result['answer']}\n\n"
        f"Question: {rubric}"
    )
    response = create_message(
        client,
        model=JUDGE_MODEL,
        max_tokens=16000,
        system=JUDGE_INSTRUCTIONS,
        messages=[{"role": "user", "content": prompt}],
        # Structured output: the reply is JSON with "answer" set to exactly "yes" or "no".
        output_config={"format": {
            "type": "json_schema",
            "schema": {
                "type": "object",
                "properties": {"answer": {"type": "string", "enum": ["yes", "no"]}},
                "required": ["answer"],
                "additionalProperties": False,
            },
        }},
    )
    if response.stop_reason == "refusal":
        return False, "judge refused"
    text = next(block.text for block in response.content if block.type == "text")
    verdict = json.loads(text)["answer"]
    return verdict == "yes", f"judge said {verdict}"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score(client, case, result):
    """Run every check for one case. Returns a list of check results."""
    scored = []
    for check in case["checks"]:
        if check["method"] == "code":
            passed, detail = check["fn"](result)
        elif check["method"] == "judge":
            passed, detail = ask_judge(client, case, result, check["rubric"])
        else:  # "either": code first, judge only if code says no
            passed, detail = check["fn"](result)
            if not passed:
                passed, judge_detail = ask_judge(client, case, result, check["rubric"])
                detail = f"{detail}; {judge_detail}"
        scored.append({"name": check["name"], "method": check["method"],
                       "passed": passed, "detail": detail})
    return scored


def print_table(records):
    """Print the pass rate per case, per category and in total.

    A run passes when every one of its checks passes. Runs that hit an API
    error are not counted in the pass rate; they get their own column.
    """
    by_case = defaultdict(list)
    by_category = defaultdict(list)
    for record in records:
        by_case[(record["case_id"], record["category"], record["question"])].append(record["outcome"])
        by_category[record["category"]].append(record["outcome"])

    def rate(outcomes):
        scored = [outcome for outcome in outcomes if outcome != "error"]
        passes = scored.count("pass")
        if not scored:
            return f"{'0/0':>5}     -"
        return f"{passes}/{len(scored)}  {passes / len(scored):>4.0%}"

    def errors(outcomes):
        return outcomes.count("error") or ""

    print(f"\n{'case':>4}  {'category':<11} {'pass':>9} {'errors':>6}  question")
    for (case_id, category, question), outcomes in sorted(by_case.items()):
        print(f"{case_id:>4}  {category:<11} {rate(outcomes):>9} {errors(outcomes):>6}  {question}")

    print(f"\n{'category':<11} {'pass':>9} {'errors':>6}")
    for category, outcomes in sorted(by_category.items()):
        print(f"{category:<11} {rate(outcomes):>9} {errors(outcomes):>6}")

    everything = [record["outcome"] for record in records]
    print(f"\n{'TOTAL':<11} {rate(everything):>9} {errors(everything):>6}")
    if "error" in everything:
        print(f"\n{everything.count('error')} run(s) hit an API error and are not in the pass rate.")


# ---------------------------------------------------------------------------
# Dry run: check the setup without calling the API
# ---------------------------------------------------------------------------

def dry_run(cases, tools):
    """Validate the cases and tools. Makes no API calls and writes no files."""
    problems = []

    # 1. The cases are well-formed.
    ids = [case["id"] for case in CASES]
    if ids != list(range(1, 14)):
        problems.append(f"case ids should be 1-13, got {ids}")
    for case in CASES:
        expected_set = "dev" if case["id"] <= 10 else "held_out"
        if case["set"] != expected_set:
            problems.append(f"case {case['id']} is in '{case['set']}', expected '{expected_set}'")
        if not case["checks"]:
            problems.append(f"case {case['id']} has no checks")
        for check in case["checks"]:
            needs = {"code": ["fn"], "judge": ["rubric"], "either": ["fn", "rubric"]}.get(check["method"])
            if needs is None:
                problems.append(f"case {case['id']}: unknown method '{check['method']}'")
            elif any(key not in check for key in needs):
                problems.append(f"case {case['id']}: '{check['name']}' needs {needs}")

    # 2. The tools load from the server and each one runs.
    names = sorted(tool["name"] for tool in tools)
    if names != sorted(TOOL_FUNCTIONS):
        problems.append(f"server tools {names} don't match {sorted(TOOL_FUNCTIONS)}")
    samples = {
        "get_team_status": {},
        "get_rep_detail": {"rep_id": "R14"},
        "calculate_payout": {"rep_id": "R14", "extra_sales": 10000},
        "fill_nudges": {"segment": "far_behind", "template": "Hi {name}, you're {gap} from {next_step}."},
    }
    print("Tools (definitions from the MCP server, called directly in Python):")
    for name, tool_input in samples.items():
        text, is_error = call_tool(name, tool_input)
        ok = not is_error and "error" not in json.loads(text)
        if not ok:
            problems.append(f"{name}({tool_input}) failed: {text[:200]}")
        print(f"  {'ok  ' if ok else 'FAIL'} {name}({tool_input}) -> {len(text):,} chars of JSON")

    # 3. Every code check runs on a fake result (an empty answer). They
    #    should fail on it, but they must not crash. This also computes
    #    every expected value from logic.py.
    fake = {"tool_calls": [], "answer": "", "stop_reason": "end_turn"}
    print(f"\nCases in this set ({len(cases)}), with expected values from logic.py:")
    for case in cases:
        print(f"  {case['id']:>2} [{case['category']}] {case['question']}")
        for check in case["checks"]:
            if "fn" in check:
                try:
                    _, detail = check["fn"](fake)
                    print(f"       code:  {check['name']}  ({detail})")
                except Exception as e:
                    problems.append(f"case {case['id']} '{check['name']}' crashed: {e!r}")
            if "rubric" in check:
                print(f"       judge: {check['rubric']}")

    # 4. The system prompt and API key are in place (the key is never printed).
    print(f"\nSystem prompt: agent/CLAUDE.md ({len(SYSTEM_PROMPT):,} chars)")
    print(f"Agent model: {AGENT_MODEL}   Judge model: {JUDGE_MODEL}")
    print(f"ANTHROPIC_API_KEY in environment: {'yes' if os.environ.get('ANTHROPIC_API_KEY') else 'no'}")

    if problems:
        print("\nDRY RUN FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        sys.exit(1)
    print("\nDry run OK: no API calls made, nothing written.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Run the Incentive Coach evals.")
    parser.add_argument("--set", choices=["dev", "held_out"], default="dev")
    parser.add_argument("--runs", type=int, default=3, help="runs per case (default 3)")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate cases and tools without calling the API")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")   # puts ANTHROPIC_API_KEY into os.environ, if .env exists
    cases = cases_for(args.set)
    tools = tool_definitions()

    if args.dry_run:
        dry_run(cases, tools)
        return

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. Add it to .env (which is git-ignored).")
    client = anthropic.Anthropic()   # reads the key from the environment

    records = []
    for case in cases:
        for run in range(1, args.runs + 1):
            print(f"case {case['id']:>2} run {run}/{args.runs} ...", end=" ", flush=True)
            try:
                result = run_agent(client, tools, case["question"])
                checks = score(client, case, result)
                error = None
            except anthropic.APIError as e:
                if api_calls_ok == 0 and is_account_error(e):
                    # The very first call failed on the key, permissions or
                    # billing: every other run would fail the same way.
                    print("stopped")
                    sys.exit(f"\nThe first API call failed, so no cases were run.\n"
                             f"{type(e).__name__}: {e}\n"
                             f"Check ANTHROPIC_API_KEY in .env, the key's access to "
                             f"{AGENT_MODEL} and {JUDGE_MODEL}, and the account's credit balance.")
                # An infrastructure problem, not an agent failure: record it
                # as "error" and carry on with the other runs.
                result = {"tool_calls": [], "answer": "", "stop_reason": None}
                checks, error = [], f"{type(e).__name__}: {e}"
            if error:
                outcome = "error"
                print(f"ERROR {error}")
            elif all(check["passed"] for check in checks):
                outcome = "pass"
                print("pass")
            else:
                outcome = "fail"
                print(f"FAIL {[c['name'] for c in checks if not c['passed']]}")
            records.append({
                "case_id": case["id"], "set": case["set"], "category": case["category"],
                "question": case["question"], "run": run, **result,
                "checks": checks, "error": error, "outcome": outcome,
            })

    # Save everything, then print the summary.
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{datetime.now():%Y-%m-%d_%H%M%S}.json"
    path.write_text(json.dumps({
        "set": args.set, "runs": args.runs,
        "agent_model": AGENT_MODEL, "judge_model": JUDGE_MODEL,
        "records": records,
    }, indent=2, ensure_ascii=False))
    print_table(records)
    print(f"\nSaved {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
