# Pre-registration

Every research run is committed here **before** it is executed (README section 11). A result
read after the fact is a story, not evidence.

## Configuration budget

**Ten configurations for the entire project.** Each run below consumes one. When the count
reaches ten, the budget is spent and the strategy is locked.

| # | Date committed | Run name | Consumed by |
|---|---|---|---|
| 1 | 2026-09-29 | usd-1m-primary | 10 bps slippage configuration |
| 2 | 2026-09-29 | usd-1m-slippage-stress | 25 bps slippage sensitivity |
| 3 | | | |
| 4 | | | |
| 5 | | | |
| 6 | | | |
| 7 | | | |
| 8 | | | |
| 9 | | | |
| 10 | | | |

**Spent:** 2 / 10

## Holdout status

- Split point (`[backtest] in_sample_end`): 2025-01-01T00:00:00Z
- Holdout start (`[backtest] holdout_start`): 2025-01-01T00:00:00Z
- Pass rule, committed before opening: _not yet written_
- Opened: **no** — open once, then it is a consistency check and never again a holdout.

## Registered run pair — 2026-09-29

Configurations: `configs/research-usd-1m.toml` and
`configs/research-usd-1m-stress.toml`. Both use the fixed ten-symbol universe
BTC/ETH/BNB/SOL/XRP/ADA/DOGE/LTC/LINK/AVAX, USD 1h bars, $1,000,000 initial cash, and
2023-08-01 through 2025-01-01 in-sample reporting. Both use 30-day liquidity and correlation
windows; 7/14/30-day momentum horizons with a 24-hour skip; top 5 with keep-rank 10; equal weights
capped at 20% per name; 3% daily target volatility capped at 1x; 5% drift band; 2% cash buffer;
and 10 bps taker fees per side. Run 1 uses 10 bps slippage per order; run 2 changes only
`costs.slippage_bps` to 25. Binance USDT spot prices and quote volumes proxy Roostoo USD market
history. Fees, liquidity threshold, strategy parameters, and proxy choice are explicit research
assumptions, not verified Roostoo competition rules. Pair quantity precision and minimum notional
come from `data/snapshots/roostoo-pair-info-2026-09-29.json`. No holdout data is used.

Hypothesis: the strategy has positive net return and positive median 14-day rolling return at
10 bps slippage, and remains net positive at 25 bps. Report total return, PnL, Sharpe, Sortino,
Calmar, max drawdown, and the 14-day window return distribution regardless of outcome. A negative
net return, non-positive median window return, or a negative net return under the 25 bps stress
falsifies the hypothesis. This is one fixed configuration plus the required execution-cost
sensitivity, not a parameter search.

The metrics convention is daily UTC closing equity, 365 periods per year, Sortino MAR 0, and
Calmar undefined when maximum drawdown is below 1%. PnL and return are measured from the initial
$1,000,000 at `report_from`; all results include modeled fees and slippage.

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
