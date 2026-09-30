# Pre-registration

Every research run is committed here **before** it is executed (README section 11). A result
read after the fact is a story, not evidence.

## Configuration budget

**Ten configurations were originally budgeted.** The user has now explicitly requested a fixed
12-case stop-loss comparison, so this pre-registration records an authorized one-time expansion
to 14 total configurations (the original two plus these 12). Each matrix cell counts as one
configuration. Do not extend or retune this matrix after viewing its results.

| # | Date committed | Run name | Consumed by |
|---|---|---|---|
| 1 | 2026-09-29 | usd-1m-primary | 10 bps slippage configuration |
| 2 | 2026-09-29 | usd-1m-slippage-stress | 25 bps slippage sensitivity |
| 3 | 2026-09-30 | stoploss-5pct-trailing-1y | user-requested matrix |
| 4 | 2026-09-30 | stoploss-5pct-trailing-3y | user-requested matrix |
| 5 | 2026-09-30 | stoploss-5pct-trailing-5y | user-requested matrix |
| 6 | 2026-09-30 | stoploss-5pct-fortnight | user-requested matrix |
| 7 | 2026-09-30 | stoploss-7_5pct-trailing-1y | user-requested matrix |
| 8 | 2026-09-30 | stoploss-7_5pct-trailing-3y | user-requested matrix |
| 9 | 2026-09-30 | stoploss-7_5pct-trailing-5y | user-requested matrix |
| 10 | 2026-09-30 | stoploss-7_5pct-fortnight | user-requested matrix |
| 11 | 2026-09-30 | stoploss-10pct-trailing-1y | user-requested matrix |
| 12 | 2026-09-30 | stoploss-10pct-trailing-3y | user-requested matrix |
| 13 | 2026-09-30 | stoploss-10pct-trailing-5y | user-requested matrix |
| 14 | 2026-09-30 | stoploss-10pct-fortnight | user-requested matrix |

**Spent after this pre-registration:** 14 / 14

## Registered run matrix — 2026-09-30 (not yet executed)

This exact matrix is recorded in git before the runs start; the pre-registration commit hash is
added to the result addendum after git assigns it.

The user-requested matrix tests stop-loss drawdowns of **5%, 7.5%, and 10%** against four
reporting windows: trailing 1 year, 3 years, 5 years, and consecutive non-overlapping 14-day
windows beginning 2025-07-01 (Q3 2025) through the latest complete hourly observation. This is
12 cases, with the same fixed ten-symbol BTC/ETH/BNB/SOL/XRP/ADA/DOGE/LTC/LINK/AVAX basket and
$1,000,000 initial cash in every case. It is a comparison requested by the user, not a search to
select and deploy a winning stop setting.

The stop is a close-price trigger when a coin closes at least the registered percentage below
its weighted-average entry price. It is filled at the following hourly close using the existing
order planner and modeled market costs. After liquidation, that coin has a fixed 24-hour
re-entry cooldown; the next normal rebalance can otherwise act on the current strategy signals.
The portfolio is flat during each case's warm-up, including each trailing-window start. Data is
hourly Binance Vision spot USDT history, used as a USDT≈USD proxy, and current Roostoo pair
precision/minimum-notional metadata from `data/snapshots/roostoo-pair-info-2026-09-30.json`. The
900 source archives are SHA-256 recorded in `data/snapshots/backtest-data-manifest.json`; the
panel spans 2021-08-01 01:00 UTC through 2026-09-30 00:00 UTC. A handful of archive gaps are
forward-filled with zero volume and excluded from live-return calculations by the stale mask.
A case is reported through the latest complete hourly bar;
the incomplete tail after the last complete 14-day window is excluded from the fortnight PnL
frequency table and reported separately. No holdout is used.

Held fixed: all strategy, universe, sizing, execution and risk settings other than the enabled
stop threshold; 10 bps market slippage, 0.1% taker fee, $1m cash, and 0.05% maker fee (recorded
for completeness; the strategy uses market orders). Fees match the [problem statement linked in
the team resource pack](https://luma.com/coghwiyt); its current page has a different event title
and lists a $100,000 wallet, so the event edition and actual competition balance must be
confirmed before deployment. The user-requested $1m remains the fixed research starting cash.
The hypothesis is descriptive: the stop settings will produce distinguishable risk/return
profiles over these windows. Report every
case regardless of outcome: net PnL, total return, Sharpe, Sortino, Calmar, maximum drawdown,
stop counts, and 14-day PnL histogram for the Q3-2025-onward case. No outcome selects a winner
without a separately pre-registered validation.

## Holdout status

- Split point (`[backtest] in_sample_end`): 2025-01-01T00:00:00Z
- Holdout start (`[backtest] holdout_start`): 2025-01-01T00:00:00Z
- Pass rule, committed before opening: _not yet written_
- Opened: **no** — open once, then it is a consistency check and never again a holdout.

## Registered run pair — 2026-09-29

Pre-registration commit: `5df3e09`.

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

#### Result — run 2026-09-29

- **Code commit:** `1bb5c3e`
- **Outcome:** hypothesis accepted under the preregistered proxy assumptions; net return and
  median 14-day return were positive in both cost cases.
- **10 bps slippage:** ending equity $2,391,149.86; PnL $1,391,149.86; return 139.11%; Calmar
  2.166; Sharpe 1.459; Sortino 2.157; max drawdown 39.07%.
- **25 bps slippage:** ending equity $2,318,943.34; PnL $1,318,943.34; return 131.89%; Calmar
  2.035; Sharpe 1.417; Sortino 2.091; max drawdown 39.64%.
- **14-day rolling returns, 10 bps:** 73 windows; median 1.44%; p10 -8.95%; p90 17.74%; worst
  -18.36%; worst window max drawdown 21.39%.
- **14-day rolling returns, 25 bps:** 73 windows; median 1.41%; p10 -9.05%; p90 17.64%; worst
  -18.45%; worst window max drawdown 21.42%.
- **Limitations:** Binance USDT prices and volumes stand in for Roostoo USD history; the USDT/USD
  peg is assumed. The symbol universe is fixed from currently tradeable pairs, and current Roostoo
  quantity/minimum-order metadata is applied retrospectively. Fees and slippage are assumptions.
  This is a proxy research result, not evidence of Roostoo execution performance. The holdout was
  not used, and no benchmark returns were computed.

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
