# MCP tools

*The Incentive Coach server (`server/main.py`) gives the agent five tools. Every tool reads `data/incentive.db` read-only and returns a dict, which the agent gets as structured JSON. None of them return prose for reps.*

## Design principles

| Principle | How the tools follow it |
|---|---|
| All maths in code (PRD) | Segments, paces, gaps and payouts are worked out in `server/logic.py`. The agent quotes numbers and never calculates them |
| One source of truth | `server/logic.py` holds every business rule. `data/check.py` imports the same file, so the data check and the coach can't disagree |
| Structured, not prose | Tools return numbers and labels. The agent writes the words, and `fill_nudges` puts the numbers in |
| Errors the model can act on | Every error is `{"error": "..."}`, plus the list of valid values where that helps, so the agent can fix its call and try again |
| Lenient on form, strict on meaning | `r5` is read as `R05` and `In reach` as `in_reach`. A rep that doesn't exist, or a negative extra, is always rejected |

## Shared definitions

| Term | Meaning |
|---|---|
| Sales | Gross ₹ sales this month. Returns are **not** subtracted (`returns_netted = 0`) |
| Focus pieces | Focus-SKU pieces sold this month, returns not subtracted |
| `pace_per_day` | Sales ÷ days since the rep's start day (days included), rounded to the nearest rupee |
| `need_per_day` | Gap ÷ days left, rounded **up**, so a rep who sells that much each day really does cross. Rounding down could leave them ₹1 short |
| `pace_multiple_needed` | Pace needed ÷ current pace, from the unrounded paces, to 2 decimals (e.g. Isha 1.22). `null` for top-step reps and for a rep with no sales yet. The agent quotes this instead of dividing `need_per_day` by `pace_per_day` itself |
| `gap` | Next step − sales. Reaching the threshold exactly counts as crossing it |
| `incentive_unlocked` | Payout at the next step − payout now. For step 1 this includes the kicker on **all** focus pieces already sold, so it's more than ₹1,000 |
| `margin_if_cross` | Gap × the rep's own gross margin rate, based on their product mix so far |
| `call_priority` | `margin_if_cross ÷ incentive_unlocked`. Higher means call first. In-reach reps only |
| Segments | `top_step`, `on_track`, `in_reach`, `far_behind`, using the PRD rules with a 1.5× pace multiplier |

---

## 1. `get_team_status`

**When to use:** to see the whole team, to answer "who should I call?", and to find rep ids.

**Input**

| Name | Type | Required | Notes |
|---|---|---|---|
| `segment` | string | no | One of the four segments. Leave it out to get every rep |

**Output**

```json
{
  "today": 24, "month_days": 30, "days_left": 6,
  "rep_count": {"top_step": 10, "on_track": 15, "in_reach": 15, "far_behind": 10},
  "call_priority_formula": "margin_if_cross / incentive_unlocked (in-reach reps only)",
  "segments": {
    "in_reach": [
      {"rep_id": "R14", "name": "Isha", "segment": "in_reach", "sales": 76640,
       "next_step": 100000, "gap": 23360, "pace_per_day": 3193, "need_per_day": 3894,
       "pace_multiple_needed": 1.22, "flags": [],
       "margin_if_cross": 6650, "incentive_unlocked": 2500, "call_priority": 2.66, "call_rank": 7},
      {"rep_id": "R37", "name": "Vidya", "segment": "in_reach", "...": "...",
       "flags": [{"type": "late_starter", "start_day": 10,
                  "note": "Started on day 10, so pace is averaged over 15 days, not 24."}],
       "call_rank": 11}
    ],
    "on_track": [ ... ], "top_step": [ ... ], "far_behind": [ ... ]
  }
}
```

Top-step reps have `next_step`, `gap`, `need_per_day` and `pace_multiple_needed` set to `null`. In-reach reps are sorted by `call_priority`, highest first. Every rep has `flags`, the same list `get_rep_detail` returns (see the flag table there); an empty list means no flags.

**Errors:** an unknown segment returns the error with `allowed_segments`.

**Why this design**
- **Grouped by segment.** The manager acts on one segment at a time (one call list, one message per segment), so the grouping matches how the output gets used.
- **Only in-reach reps are ranked.** On-track reps should cross anyway, and far-behind reps need more than 1.5× their pace. A call is worth most to in-reach reps (PRD).
- **Formula inputs are shown next to the score.** The PRD's "unexplainable ranking" risk: the manager can see why R03 ranks above R14.
- **Flags are included for every rep.** Without them, the agent had to call `get_rep_detail` on each in-reach rep just to find out who was flagged (16 tool calls for one call list). Now it's 1. Both tools build flags with the same helper, so they can't disagree.
- **The pace ratio is a field, not left to the agent.** Before `pace_multiple_needed` existed, the agent worked out "needs N times their pace" itself and sometimes got it wrong: it wrote "3 to 5 times" for far-behind reps whose real ratios run from 2.27 to 7.86. The eval check "pace multipliers from tools" caught this (case 7).

## 2. `get_rep_detail`

**When to use:** before calling or writing to one rep, or when the manager asks about one person.

**Input**

| Name | Type | Required | Notes |
|---|---|---|---|
| `rep_id` | string | yes | For example `R14`. `r14` and `R5` are accepted too |

**Output:** every field from `get_team_status` (including `pace_multiple_needed` and `flags`), plus `incentive_unlocked`, `margin_if_cross`, `start_day`, `days_worked`, `days_left`, `projected_month_end`, `focus_pieces`, `kicker_per_piece` and `current_payout` (same breakdown as `calculate_payout`).

```json
"flags": [
  {"type": "bulk_order", "day": 17, "value": 17105,
   "note": "Without bulk orders, sales would be ₹78,330 and the segment would be in_reach (currently on_track)."}
]
```

| Flag | Raised when | Extra fields |
|---|---|---|
| `late_starter` | `start_day` > 1 | `start_day`, `note` |
| `bulk_order` | One sales row worth ≥ 3× the rep's average day | `day`, `value`. The last bulk flag carries a `note` with the segment the rep would be in without any bulk orders |

**Errors:** an unknown `rep_id` returns an error that gives the valid id range and points the agent to `get_team_status`.

**Why this design**
- **Flags are for the manager, not the rep.** They explain numbers that look odd. They don't change the segment, because the PRD formula is the agreed rule.
- **Bulk orders are detected, not looked up.** A real sales export doesn't flag them, so the tool can't rely on a label. In this data the 3× cut-off separates the three bulk orders (4.3–6×) from the largest ordinary row (about 1.4×).
- **The "without bulk" segment is shown.** For R05 this is the difference between getting a call and not getting one, and the manager should know that (DATA.md test case 2).

## 3. `calculate_payout`

**When to use:** for any payout figure, and for what-if questions. The agent should never work out a payout itself.

**Input**

| Name | Type | Required | Notes |
|---|---|---|---|
| `rep_id` | string | yes | |
| `extra_sales` | integer | no, default 0 | Extra ₹ sales, 0 or more. Includes the value of any extra focus pieces |
| `extra_focus_pieces` | integer | no, default 0 | How many of those extra sales are focus pieces, 0 or more. Affects only the kicker |

**Output**

```json
{
  "rep_id": "R37", "name": "Vidya", "kicker_per_piece": 75,
  "now":   {"sales": 33280, "focus_pieces": 18, "highest_step_crossed": 0, "step_payout": 0,
            "kicker_unlocked": false, "focus_kicker": 0, "total": 0},
  "extra": {"extra_sales": 16720, "extra_focus_pieces": 5},
  "after": {"sales": 50000, "focus_pieces": 23, "highest_step_crossed": 1, "step_payout": 1000,
            "kicker_unlocked": true, "focus_kicker": 1725, "total": 2725},
  "payout_increase": 2725
}
```

**Errors:** an unknown `rep_id`, or an extra that is negative or not a whole number.

**Why this design**
- **Now and after, side by side.** That's the comparison behind every nudge ("₹X more earns you ₹Y"), and `payout_increase` saves the agent from subtracting.
- **The breakdown is split into step payout and focus kicker.** The kicker cliff at step 1 is the least obvious rule. Showing `kicker_unlocked` makes it visible: R37's 18 focus pieces earn nothing until step 1, and then all of them pay at once.
- **Extra sales and extra focus pieces are separate inputs.** Focus pieces have a ₹ value (which counts toward steps) and a piece count (which counts toward the kicker). One rupee input plus one piece input covers both without guessing prices.

## 4. `fill_nudges`

**When to use:** after the agent has drafted one message for a segment. It gets back one finished message per rep.

**Input**

| Name | Type | Required | Notes |
|---|---|---|---|
| `segment` | string | yes | One of the four segments |
| `template` | string | yes | Text with `{placeholders}` |

| Placeholder | Filled with | Example |
|---|---|---|
| `{name}` | First name | Isha |
| `{sales}` | Sales so far | ₹76,640 |
| `{next_step}` | Next step threshold | ₹1,00,000 |
| `{gap}` | Gap to next step | ₹23,360 |
| `{extra_payout}` | `incentive_unlocked` | ₹2,500 |
| `{need_per_day}` | Pace needed | ₹3,894 |
| `{pace_per_day}` | Current pace | ₹3,193 |
| `{days_left}` | Days left | 6 |
| `{focus_pieces}` | Focus pieces so far | 40 |
| `{kicker_per_piece}` | Kicker per piece | ₹75 |
| `{pace_multiple}` | `pace_multiple_needed` to 1 decimal, no unit. Write `{pace_multiple}x` or `{pace_multiple} times` | 1.2 |

Rupees use Indian digit grouping: ₹23,360, ₹1,00,000, ₹12,34,567. Raw numbers in the other tools stay plain integers.

**Output**

```json
{"segment": "in_reach", "count": 15, "messages": [
  {"rep_id": "R14", "name": "Isha",
   "message": "Hi Isha, you're at ₹76,640. ₹23,360 more gets you to ₹1,00,000 and unlocks ₹2,500. ..."}
]}
```

**Errors**

| Problem | Error includes |
|---|---|
| Unknown segment | `allowed_segments` |
| Unknown placeholder, e.g. `{bonus}` | The bad name(s) in `unknown_placeholders`, plus `allowed_placeholders` |
| Broken braces, e.g. `{name` | `allowed_placeholders` |
| `{next_step}`, `{gap}`, `{extra_payout}`, `{need_per_day}` or `{pace_multiple}` used for `top_step` | `allowed_for_top_step`. Those reps have no next step |

The whole template is checked before any message is filled, so the result is either all messages or one error, never a partial batch.

**Why this design**
- **The model writes the words; code writes the numbers.** That's how the PRD's quality bar ("every ₹ figure matches tool output, zero mismatches") is met by construction, not by checking afterwards.
- **One template per segment.** That matches the PRD's adoption mitigation ("drafts grouped by segment"). The manager reviews one message, not 15.
- **A fixed placeholder list.** An unknown name is an error, not a blank. A silently empty `{bonus}` would send reps a broken message.

## 5. `get_payout_totals`

**When to use:** for any total across reps, such as "what is our total payout?" or "what would it cost if every in-reach rep crosses?". The agent should never add up per-rep figures itself.

**Input:** none.

**Output**

```json
{
  "today": 24, "days_left": 6,
  "assumption": "Each in-reach rep sells exactly up to their next step, with no extra focus pieces. Reps in other segments stay where they are.",
  "team":       {"rep_count": 50, "payout_now": 187675, "payout_if_in_reach_cross": 221075, "extra_cost": 33400},
  "by_segment": {
    "top_step":   {"rep_count": 10, "payout_now": 93725, "payout_if_in_reach_cross": 93725, "extra_cost": 0},
    "on_track":   {"rep_count": 15, "payout_now": 48550, "payout_if_in_reach_cross": 48550, "extra_cost": 0},
    "in_reach":   {"rep_count": 15, "payout_now": 45400, "payout_if_in_reach_cross": 78800, "extra_cost": 33400},
    "far_behind": {"rep_count": 10, "payout_now": 0,     "payout_if_in_reach_cross": 0,     "extra_cost": 0}
  }
}
```

| Field | Meaning |
|---|---|
| `payout_now` | Sum of `calculate_payout`'s `now.total` for those reps |
| `payout_if_in_reach_cross` | The same sum after every in-reach rep sells exactly their `gap`. Other reps are unchanged |
| `extra_cost` | `payout_if_in_reach_cross − payout_now`. Non-zero only for `in_reach` and `team` |

**Errors:** none; the tool takes no input.

**Why this design**
- **Totals come from code, like every other number.** Before this tool, "what's our total payout if every in-reach rep crosses?" took 16 tool calls, and the agent then either added 15 figures itself (breaking the "never calculate" rule) or declined and asked the manager to use a spreadsheet.
- **Team and segment totals together.** "Total payout" can mean the in-reach reps only, the extra cost, or the whole team. One call returns all three, so the agent can answer whichever the manager meant, or show all of them.
- **The crossing assumption is stated in the output.** Selling exactly the gap with no extra focus pieces is the cheapest way to cross, so this is the minimum extra cost. The agent can repeat the assumption instead of inventing one.
- **Built from the same per-rep `payout()` as `calculate_payout`**, in `server/logic.py`, so a total always equals the sum of the per-rep figures.
