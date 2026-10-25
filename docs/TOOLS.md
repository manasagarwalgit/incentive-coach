# MCP tools

*The Incentive Coach server (`server/main.py`) gives the agent four tools. Every tool reads `data/incentive.db` read-only and returns a dict, which the agent gets as structured JSON. None of them return prose for reps.*

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
       "margin_if_cross": 6650, "incentive_unlocked": 2500, "call_priority": 2.66, "call_rank": 7}
    ],
    "on_track": [ ... ], "top_step": [ ... ], "far_behind": [ ... ]
  }
}
```

Top-step reps have `next_step`, `gap` and `need_per_day` set to `null`. In-reach reps are sorted by `call_priority`, highest first.

**Errors:** an unknown segment returns the error with `allowed_segments`.

**Why this design**
- **Grouped by segment.** The manager acts on one segment at a time (one call list, one message per segment), so the grouping matches how the output gets used.
- **Only in-reach reps are ranked.** On-track reps should cross anyway, and far-behind reps need more than 1.5× their pace. A call is worth most to in-reach reps (PRD).
- **Formula inputs are shown next to the score.** The PRD's "unexplainable ranking" risk: the manager can see why R03 ranks above R14.

## 2. `get_rep_detail`

**When to use:** before calling or writing to one rep, or when the manager asks about one person.

**Input**

| Name | Type | Required | Notes |
|---|---|---|---|
| `rep_id` | string | yes | For example `R14`. `r14` and `R5` are accepted too |

**Output:** every field from `get_team_status`, plus `incentive_unlocked`, `margin_if_cross`, `start_day`, `days_worked`, `days_left`, `projected_month_end`, `focus_pieces`, `kicker_per_piece`, `current_payout` (same breakdown as `calculate_payout`) and `flags`.

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
| `{next_step}`, `{gap}`, `{extra_payout}` or `{need_per_day}` used for `top_step` | `allowed_for_top_step`. Those reps have no next step |

The whole template is checked before any message is filled, so the result is either all messages or one error, never a partial batch.

**Why this design**
- **The model writes the words; code writes the numbers.** That's how the PRD's quality bar ("every ₹ figure matches tool output, zero mismatches") is met by construction, not by checking afterwards.
- **One template per segment.** That matches the PRD's adoption mitigation ("drafts grouped by segment"). The manager reviews one message, not 15.
- **A fixed placeholder list.** An unknown name is an error, not a blank. A silently empty `{bonus}` would send reps a broken message.
