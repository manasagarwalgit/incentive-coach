# Experiment design

*This is a plan, not a result. No experiment has been run.*

**Question.** Do coach nudges beat generic reminders on incremental gross margin per incentive rupee, net of next-month returns?

**Design.** Randomise at rep level within each manager's team. Half the reps get coach nudges; the other half get the current generic reminder.
- **Every manager is live.** The client wants all managers using the coach, and this design allows that.
- **Manager quality cancels out.** Each manager has reps in both groups, so lift can be read per manager.

**Known weakness.** The manager sees coach data for all reps and may change how they treat control reps, for example by personalising their reminders. That pulls the two groups together, so a measured lift is conservative. To track it, compare the messages sent to control reps against the generic template.

**Not tested in v1: call ranking.** Calls are a manager action, so they can't be randomised by rep. The next experiment rolls managers out in random waves and compares waves.

**Duration: 3 months, plus 1 month for returns.**
- The extra month catches stock loading, which shows up as returns.
- Three months separates a novelty effect from a lasting one.

**Metrics** (from [PRD.md](PRD.md)):

| Type | Metric |
|---|---|
| North star | Incremental gross margin per incentive rupee, net of next-month returns, vs generic reminders |
| Floor | Net sales lift must be positive |
| Guardrail | Next-month return rate: reps who crossed vs did not |
| Guardrail | Max 2 nudges per rep per week |

**Win rule.** The coach wins if the north star improves and the floor and both guardrails hold. The minimum detectable effect and sample size are calculated before launch, from historical variance. No thresholds are set in this document.
