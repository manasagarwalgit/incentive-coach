# The Incentive Coach MCP server.
#
# It gives the agent four tools. Each tool reads data/incentive.db, does all
# the arithmetic in code (via server/logic.py) and returns a dict, which the
# agent receives as structured JSON. The tools never write prose for reps;
# the agent writes messages, and every rupee figure it quotes must come from
# these tools.
#
# How to run it by hand (Claude Code normally starts it for you):
#     uv run python server/main.py

import math
import string
from typing import Any

from mcp.server.mcpserver import MCPServer

from logic import (
    SEGMENTS,
    connect,
    find_bulk_orders,
    load_reps,
    load_scheme,
    payout,
)
# Renamed on import so it isn't confused with the tools' `segment` argument.
from logic import segment as find_segment

# Create the server and give it a name.
mcp = MCPServer("incentive-coach")

# The only words allowed inside {curly brackets} in a nudge template.
ALLOWED_PLACEHOLDERS = [
    "name", "sales", "next_step", "gap", "extra_payout", "need_per_day",
    "pace_per_day", "days_left", "focus_pieces", "kicker_per_piece",
]

# Placeholders that only make sense for a rep who still has a step ahead.
# Top-step reps have crossed the highest step, so these have no value.
NEXT_STEP_PLACEHOLDERS = ["next_step", "gap", "extra_payout", "need_per_day"]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def rupees(amount):
    """Format a rupee amount the Indian way, e.g. 23360 -> '₹23,360'
    and 100000 -> '₹1,00,000'.

    Indian grouping puts a comma after the last three digits, then after
    every two digits (thousands, lakhs, crores).
    """
    sign = "-" if amount < 0 else ""
    digits = str(round(abs(amount)))
    last_three, rest = digits[-3:], digits[:-3]
    groups = []
    while rest:
        groups.insert(0, rest[-2:])   # take two digits at a time from the right
        rest = rest[:-2]
    return f"{sign}₹" + ",".join(groups + [last_three])


def error(message, **more):
    """Every tool returns errors in this one shape, so the agent can spot them."""
    return {"error": message, **more}


def clean_rep_id(rep_id):
    """Accept 'r5', ' R05 ' etc. and turn them into the stored form 'R05'."""
    text = str(rep_id).strip().upper()
    if text.startswith("R") and text[1:].isdigit():
        return f"R{int(text[1:]):02d}"
    return text


def clean_segment(name):
    """Accept 'In reach', 'in-reach' etc. and turn them into 'in_reach'."""
    return str(name).strip().lower().replace(" ", "_").replace("-", "_")


def rep_numbers(rep, scheme):
    """Work out every number the tools report for one rep.

    All rounding happens here, once:
    - pace_per_day is rounded to the nearest rupee.
    - need_per_day is rounded UP, so a rep who sells that much each day
      really does reach the step (rounding down could leave them ₹1 short).
    """
    group, d = find_segment(rep["sales"], rep["start_day"], scheme["today"],
                       scheme["days_left"], scheme["thresholds"])
    now = payout(rep["sales"], rep["focus_pieces"], scheme)
    numbers = {
        "rep_id": rep["rep_id"],
        "name": rep["name"],
        "segment": group,
        "sales": rep["sales"],
        "next_step": None,
        "gap": None,
        "pace_per_day": round(d["pace"]),
        "need_per_day": None,
        "incentive_unlocked": None,
        "margin_if_cross": None,
    }
    if group != "top_step":
        gap = d["next_step"] - rep["sales"]
        # What crossing the next step adds to their payout. For step 1 this
        # includes the kicker on ALL focus pieces already sold this month.
        at_next_step = payout(d["next_step"], rep["focus_pieces"], scheme)
        numbers.update({
            "next_step": d["next_step"],
            "gap": gap,
            "need_per_day": math.ceil(d["needed_pace"]),
            "incentive_unlocked": at_next_step["total"] - now["total"],
            # Gross margin the company earns on the extra sales needed to
            # cross, at this rep's own margin rate.
            "margin_if_cross": round(gap * rep["margin_rate"]),
        })
    return numbers, now, d


def load_everything():
    """Read the scheme and all reps in one go."""
    db = connect()
    scheme = load_scheme(db)
    reps = {rep["rep_id"]: rep for rep in load_reps(db)}
    return db, scheme, reps


def check_rep(rep_id, reps):
    """Return (clean id, None) if the rep exists, else (None, error dict)."""
    clean = clean_rep_id(rep_id)
    if clean in reps:
        return clean, None
    ids = sorted(reps)
    return None, error(
        f"Unknown rep_id '{rep_id}'. Rep ids run from {ids[0]} to {ids[-1]}. "
        "Call get_team_status to see every rep and their id.",
    )


def check_segment(name):
    """Return (clean name, None) if the segment is valid, else (None, error dict)."""
    clean = clean_segment(name)
    if clean in SEGMENTS:
        return clean, None
    return None, error(f"Unknown segment '{name}'.", allowed_segments=SEGMENTS)


# ---------------------------------------------------------------------------
# Tool 1: the whole team
# ---------------------------------------------------------------------------

@mcp.tool()
def get_team_status(segment: str | None = None) -> dict[str, Any]:
    """Segment every rep and rank the in-reach reps for a phone call.

    Use this first, to see where the team stands: who has crossed the top
    step, who is on track, who is in reach and who is far behind. Use it to
    answer "who should I call?" and to find rep ids for the other tools.

    Args:
        segment: optional. One of top_step, on_track, in_reach, far_behind.
            Leave empty to get every rep.

    Returns, for every rep: segment, sales so far, next step, gap to it,
    current pace per day and pace needed per day (rupees). In-reach reps are
    listed in call order, with margin_if_cross, incentive_unlocked and
    call_priority = margin_if_cross / incentive_unlocked (higher = call
    first). Only in-reach reps are ranked: on-track reps should cross anyway
    and far-behind reps need more than 1.5x their current pace.
    """
    if segment is not None:
        segment, problem = check_segment(segment)
        if problem:
            return problem

    db, scheme, reps = load_everything()
    groups = {name: [] for name in SEGMENTS}
    for rep in reps.values():
        numbers, _, _ = rep_numbers(rep, scheme)
        row = {key: numbers[key] for key in
               ["rep_id", "name", "segment", "sales", "next_step", "gap",
                "pace_per_day", "need_per_day"]}
        if numbers["segment"] == "in_reach":
            row["margin_if_cross"] = numbers["margin_if_cross"]
            row["incentive_unlocked"] = numbers["incentive_unlocked"]
            row["call_priority"] = round(numbers["margin_if_cross"] / numbers["incentive_unlocked"], 2)
        groups[numbers["segment"]].append(row)

    # Highest call priority first, then number them 1, 2, 3...
    groups["in_reach"].sort(key=lambda row: row["call_priority"], reverse=True)
    for rank, row in enumerate(groups["in_reach"], start=1):
        row["call_rank"] = rank

    if segment is not None:
        groups = {segment: groups[segment]}
    return {
        "today": scheme["today"],
        "month_days": scheme["month_days"],
        "days_left": scheme["days_left"],
        "rep_count": {name: len(rows) for name, rows in groups.items()},
        "call_priority_formula": "margin_if_cross / incentive_unlocked (in-reach reps only)",
        "segments": groups,
    }


# ---------------------------------------------------------------------------
# Tool 2: one rep in full
# ---------------------------------------------------------------------------

@mcp.tool()
def get_rep_detail(rep_id: str) -> dict[str, Any]:
    """Everything about one rep, plus notes the manager should know.

    Use this before writing to or calling a single rep, or when the manager
    asks about one person. Returns sales so far, focus pieces, current payout
    (with breakdown), next step, gap, pace now and pace needed, the extra
    payout unlocked by crossing, the segment, and flags.

    Flags are notes for the manager, not for the rep:
    - late_starter: the rep started after day 1, so their pace is averaged
      over fewer days.
    - bulk_order: one unusually large order (3x or more their average day).
      The projection treats it as repeatable, so it can make a rep look
      better placed than their normal selling suggests.

    Args:
        rep_id: the rep's id, e.g. "R14". Find ids with get_team_status.
    """
    db, scheme, reps = load_everything()
    rep_id, problem = check_rep(rep_id, reps)
    if problem:
        return problem

    rep = reps[rep_id]
    numbers, now, d = rep_numbers(rep, scheme)
    days_worked = scheme["today"] - rep["start_day"] + 1

    flags = []
    if rep["start_day"] > 1:
        flags.append({
            "type": "late_starter",
            "start_day": rep["start_day"],
            "note": f"Started on day {rep['start_day']}, so pace is averaged over "
                    f"{days_worked} days, not {scheme['today']}.",
        })
    bulk = find_bulk_orders(db, rep_id, d["pace"])
    for day, value in bulk:
        flags.append({"type": "bulk_order", "day": day, "value": value})
    if bulk:
        # Show what the segment would be without the bulk orders.
        without = rep["sales"] - sum(value for _, value in bulk)
        group_without, _ = find_segment(without, rep["start_day"], scheme["today"],
                                   scheme["days_left"], scheme["thresholds"])
        flags[-1]["note"] = (
            f"Without bulk orders, sales would be {rupees(without)} and the segment "
            f"would be {group_without} (currently {numbers['segment']})."
        )

    return {
        **numbers,
        "start_day": rep["start_day"],
        "days_worked": days_worked,
        "days_left": scheme["days_left"],
        "projected_month_end": round(d["projected"]),
        "focus_pieces": rep["focus_pieces"],
        "kicker_per_piece": scheme["kicker_per_piece"],
        "current_payout": now,
        "flags": flags,
    }


# ---------------------------------------------------------------------------
# Tool 3: what-if payout
# ---------------------------------------------------------------------------

@mcp.tool()
def calculate_payout(rep_id: str, extra_sales: int = 0, extra_focus_pieces: int = 0) -> dict[str, Any]:
    """A rep's payout now, and what it would be after some extra sales.

    Use this for any payout figure, and for "what if" questions such as
    "what does Kavya earn if she sells ₹5,000 more?". Never work out a
    payout yourself; always quote this tool.

    Payout = flat payout for the highest step crossed
             + kicker per piece x ALL focus pieces this month,
               but the kicker is paid only once step 1 is crossed.

    Args:
        rep_id: the rep's id, e.g. "R14".
        extra_sales: extra rupee sales to add, 0 or more. Include the value
            of any extra focus pieces here too.
        extra_focus_pieces: how many of those extra sales are focus pieces,
            0 or more. These only affect the kicker.
    """
    db, scheme, reps = load_everything()
    rep_id, problem = check_rep(rep_id, reps)
    if problem:
        return problem
    for label, value in [("extra_sales", extra_sales), ("extra_focus_pieces", extra_focus_pieces)]:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return error(f"{label} must be a whole number, 0 or more. Got {value!r}.")

    rep = reps[rep_id]
    sales_after = rep["sales"] + extra_sales
    pieces_after = rep["focus_pieces"] + extra_focus_pieces
    now = payout(rep["sales"], rep["focus_pieces"], scheme)
    after = payout(sales_after, pieces_after, scheme)
    return {
        "rep_id": rep_id,
        "name": rep["name"],
        "kicker_per_piece": scheme["kicker_per_piece"],
        "now": {"sales": rep["sales"], "focus_pieces": rep["focus_pieces"], **now},
        "extra": {"extra_sales": extra_sales, "extra_focus_pieces": extra_focus_pieces},
        "after": {"sales": sales_after, "focus_pieces": pieces_after, **after},
        "payout_increase": after["total"] - now["total"],
    }


# ---------------------------------------------------------------------------
# Tool 4: fill a message template for a whole segment
# ---------------------------------------------------------------------------

@mcp.tool()
def fill_nudges(segment: str, template: str) -> dict[str, Any]:
    """Fill one message template with each rep's own numbers, for a segment.

    Use this after you have written a nudge for a segment: write the message
    once with {placeholders} where the numbers go, and this tool returns one
    finished message per rep in that segment, with every figure taken from
    the data. This is how messages reach reps with zero arithmetic mistakes.

    Allowed placeholders: {name}, {sales}, {next_step}, {gap},
    {extra_payout}, {need_per_day}, {pace_per_day}, {days_left},
    {focus_pieces}, {kicker_per_piece}. Rupee amounts come out like ₹23,360.
    extra_payout is the payout unlocked by crossing the next step.
    Top-step reps have no next step, so for top_step use only name, sales,
    pace_per_day, days_left, focus_pieces and kicker_per_piece.

    Args:
        segment: one of top_step, on_track, in_reach, far_behind.
        template: the message, e.g. "Hi {name}, you're {gap} from {next_step}."
    """
    segment, problem = check_segment(segment)
    if problem:
        return problem

    # Find every {placeholder} in the template and check it is allowed.
    try:
        used = [field for _, field, _, _ in string.Formatter().parse(template) if field is not None]
    except ValueError as e:
        return error(f"The template has a broken curly bracket ({e}). Use {{name}}-style "
                     "placeholders and make sure every {{ has a matching }}.",
                     allowed_placeholders=ALLOWED_PLACEHOLDERS)
    unknown = [field for field in used if field not in ALLOWED_PLACEHOLDERS]
    if unknown:
        return error(f"Unknown placeholder(s): {', '.join('{' + f + '}' for f in unknown)}.",
                     unknown_placeholders=unknown, allowed_placeholders=ALLOWED_PLACEHOLDERS)
    if segment == "top_step":
        not_available = [field for field in used if field in NEXT_STEP_PLACEHOLDERS]
        if not_available:
            return error(
                f"Placeholder(s) {', '.join('{' + f + '}' for f in not_available)} have no value "
                "for top_step reps: they have already crossed the highest step.",
                allowed_for_top_step=[f for f in ALLOWED_PLACEHOLDERS if f not in NEXT_STEP_PLACEHOLDERS],
            )

    db, scheme, reps = load_everything()
    messages = []
    for rep in reps.values():
        numbers, _, _ = rep_numbers(rep, scheme)
        if numbers["segment"] != segment:
            continue
        values = {
            "name": rep["name"],
            "sales": rupees(rep["sales"]),
            "pace_per_day": rupees(numbers["pace_per_day"]),
            "days_left": scheme["days_left"],
            "focus_pieces": rep["focus_pieces"],
            "kicker_per_piece": rupees(scheme["kicker_per_piece"]),
        }
        if segment != "top_step":
            values.update({
                "next_step": rupees(numbers["next_step"]),
                "gap": rupees(numbers["gap"]),
                "extra_payout": rupees(numbers["incentive_unlocked"]),
                "need_per_day": rupees(numbers["need_per_day"]),
            })
        messages.append({"rep_id": rep["rep_id"], "name": rep["name"],
                         "message": template.format(**values)})

    return {"segment": segment, "count": len(messages), "messages": messages}


# Start the server.
if __name__ == "__main__":
    mcp.run()
