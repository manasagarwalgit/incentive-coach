# Incentive Coach — PRD v1.1

*Personal portfolio project. All data synthetic. No real companies, clients or people.*

## Problem

Field sales managers run month-end pushes by WhatsApp and phone across up to 50 reps. Their messages are generic, so reps ignore them. Managers also cannot see who is close to an incentive step, who will cross anyway, or what a push is worth, so calls go to the wrong reps.

**Hypothesis:** specific, number-backed nudges outperform generic reminders. Rationale: the goal-gradient effect — people accelerate effort as a goal becomes closer and more concrete.

## Roles

| Role | Who | What they need |
|---|---|---|
| Buyer | Central sales ops | Incentive spend that earns a measurable return |
| Primary user | Field sales managers | Who to call, and messages reps act on, at no more effort than a broadcast |
| Recipients | Reps | Honest, specific numbers about their own position |

The buyer's return depends on manager adoption. In v1, buyer value is delivered through measured outcomes, not features.

## Scheme mechanics (in scope)

- Stepped cliffs on monthly ₹ sales: a flat payout for the highest step crossed.
- Focus-SKU kicker per piece, unlocked by crossing step 1, applied to **all** focus pieces sold that month.
- No return netting; payout is final once computed.
- Volume-based steps: same engine, different unit (configuration, not a second build).

**Payout = flat payout for the highest step crossed + (if step 1 crossed) kicker × all focus pieces that month**

## Segments (by projected month-end)

**Projected month-end = sales so far + average daily sales × days left** (average over days since the rep's start date).

| Segment | Rule | Message | Call? |
|---|---|---|---|
| Top step | Highest step already crossed | Focus SKUs: ₹ per extra piece | No |
| On track | Projected to cross their next step without help | Light confirmation with numbers | No |
| In reach | Short of next step, required pace ≤ 1.5× current pace | Gap, unlock value, required pace | Yes — ranked |
| Far behind | Required pace > 1.5× current pace (parameter) | Gap, unlock value, required vs actual pace. No outcome prediction | No |

## Call ranking

Among **in-reach** reps only:

**Call priority = gross margin gained if they cross ÷ incentive unlocked by crossing**

v1 uses a transparent in-reach rule instead of a predicted probability. With real outcome data, the rule becomes a learned model.

## Jobs to be done (v1 build)

1. **Manager:** segment every rep and rank in-reach reps for a phone call.
2. **Manager:** draft a message per segment, personalised with each rep's numbers. Every rep gets a nudge.

**Stretch (first):** scheme designer simulates a scheme change — payout exposure and who crosses.
**Later:** region target tracking; nudge-performance reporting (requires a record of which drafts were sent).

## Key decisions

| Decision | Choice | Reason |
|---|---|---|
| Who sends | Agent drafts; manager sends | One payout error sent to 50 reps destroys trust. A human checks every number until evals prove accuracy |
| Ranking | Margin gained ÷ incentive unlocked, in-reach reps only | Aligns with the buyer's north star and the manager's regional target; excludes reps who would cross anyway |
| Arithmetic | All maths in code | Models are unreliable at arithmetic; payouts must be deterministic |
| Message honesty | State the pace, never predict the outcome | Prevents false promises, especially to far-behind reps |
| Fairness | Every rep gets an honest message; calls go to in-reach reps | Trade-off accepted and stated |

## Success metrics

| Type | Metric |
|---|---|
| North star | Incremental gross margin per incentive rupee, net of next-month returns, vs generic reminders |
| Floor | Net sales lift must be positive (prevents gaming the ratio by doing less) |
| Secondary | Step attainment rate vs generic reminders |
| Guardrail | Next-month return rate: reps who crossed vs did not (detects stuffing) |
| Guardrail | Max 2 nudges per rep per week |
| Quality bar | Every ₹ figure in a drafted message matches tool output — zero mismatches across all eval cases |

Thresholds come from the experiment's sample-size logic, not guesses. Ratio metrics are noisy and need larger samples.

## Autonomy path (not built; earned through evidence)

| Stage | Behaviour | Unlocked when |
|---|---|---|
| v1 (this build) | Agent drafts; manager sends | — |
| v2 | Manager approves a batch; system sends | Zero ₹ mismatches and ≥ 90% rubric pass in evals (placeholder thresholds) |
| v3 | Automated weekly sends for low-risk segments | v2 runs with zero payout errors for a set period and the experiment shows lift |

## Non-goals

Sending messages or WhatsApp integration. Changing schemes. Manager payout calculation. Payroll. Percentage-slab schemes. Dashboards. Real data of any kind. Multiple languages.

## Risks

| Risk | Mitigation |
|---|---|
| Adoption: tailored messages take more effort than one broadcast | Drafts grouped by segment, formatted for one-tap copy |
| Wrong ₹ figure in a message | All maths in code; model quotes tool outputs only; eval checks every number |
| Calls wasted on reps who would cross anyway | On-track segment excluded from calls |
| Nudges encourage stuffing | Rubric: no nudge suggests loading stock to cross; returns guardrail |
| False promises | Rubric: state pace, never predict outcome |
| Nudge fatigue | Max 2 nudges per rep per week |
| Unexplainable ranking | Every rank shows gap, unlock value, margin and formula inputs |

## Assumptions to validate

Managers will adopt segment-level drafts. Reps read WhatsApp nudges at month-end. Specific messages outperform generic ones. The 1.5× pace threshold separates reachable from unreachable gaps.
