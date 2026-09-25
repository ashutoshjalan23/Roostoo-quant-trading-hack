# Pre-registration

Every research run is committed here **before** it is executed (README section 11). A result
read after the fact is a story, not evidence.

## Configuration budget

**Ten configurations for the entire project.** Each run below consumes one. When the count
reaches ten, the budget is spent and the strategy is locked.

| # | Date committed | Run name | Consumed by |
|---|---|---|---|
| 1 | | | |
| 2 | | | |
| 3 | | | |
| 4 | | | |
| 5 | | | |
| 6 | | | |
| 7 | | | |
| 8 | | | |
| 9 | | | |
| 10 | | | |

**Spent:** 0 / 10

## Holdout status

- Split point (`[backtest] in_sample_end`): _not yet set_
- Holdout start (`[backtest] holdout_start`): _not yet set_
- Pass rule, committed before opening: _not yet written_
- Opened: **no** — open once, then it is a consistency check and never again a holdout.

---

## Run template

Copy this block for each run. Fill it in and commit it *before* executing.

### Run N — <name>

- **Date committed:**
- **Git commit at pre-registration:**
- **Hypothesis:** (what should be true, stated so it can fail)
- **Parameters varied:** (exact keys and the exact values, including the neighbours required by
  the sensitivity rule)
- **Held fixed:**
- **Data window:** (in-sample only, unless this is the one holdout run)
- **Accept rule:** (the threshold, decided now, that decides the outcome)
- **How the result will be read:** (which statistic, over which windows, against which baseline;
  the noise band from the bootstrap, below which a difference is not a finding)
- **What would falsify it:**

#### Result — filled in after the run

- **Date run:**
- **Git commit of the run:**
- **Outcome:** accept / reject
- **Numbers:** (median, p10, p90, worst, max drawdown across rolling windows)
- **Mean across neighbours:**
- **Notes:**

---

## Rejected hypotheses

The most informative part of the submission. Nothing is deleted from this table.

| Run | Hypothesis | Why rejected |
|---|---|---|
| | | |
