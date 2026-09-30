# Stop-loss backtest matrix

**As of:** 2026-09-30 00:00 UTC  
**Pre-registration:** [`2919b95`](../docs/preregistration.md)  
**Code/data run commit:** `ac53d376897e2ae365f8e1777a33176fee41f19c`  
**Start cash:** $1,000,000 per case; 10 symbols; 900 SHA-256-recorded Binance Vision archives.

## Results

Return and PnL are net of modeled fees and slippage. Sharpe and Sortino are daily and annualized; Sortino uses MAR 0. Calmar uses annualized return. Maximum drawdown is shown as a positive loss magnitude. Stop cycles count timestamps where at least one stop exit completed.

| Stop | Window | Return | PnL | Sharpe | Sortino | Calmar | Max DD | Stop cycles |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 5% | Trailing 1y | -45.25% | -$452,529.91 | -1.339 | -1.858 | -0.733 | 60.56% | 41 |
| 5% | Trailing 3y | 42.59% | $425,857.37 | 0.478 | 0.691 | 0.184 | 65.32% | 104 |
| 5% | Trailing 5y | -71.08% | -$710,843.81 | -0.356 | -0.498 | -0.268 | 82.04% | 185 |
| 5% | 14-day from 2025-07-01 | -16.78% | -$167,832.98 | -0.073 | -0.102 | -0.186 | 59.75% | 32 |
| 7.5% | Trailing 1y | -50.60% | -$506,042.42 | -1.544 | -2.043 | -0.778 | 64.49% | 32 |
| 7.5% | Trailing 3y | 19.66% | $196,633.98 | 0.350 | 0.494 | 0.078 | 72.57% | 57 |
| 7.5% | Trailing 5y | -53.75% | -$537,527.35 | -0.113 | -0.158 | -0.199 | 71.85% | 123 |
| 7.5% | 14-day from 2025-07-01 | -20.95% | -$209,487.34 | -0.146 | -0.204 | -0.241 | 61.71% | 20 |
| 10% | Trailing 1y | -45.44% | -$454,398.46 | -1.246 | -1.683 | -0.712 | 63.11% | 22 |
| 10% | Trailing 3y | 18.36% | $183,583.30 | 0.343 | 0.485 | 0.075 | 70.14% | 46 |
| 10% | Trailing 5y | -44.96% | -$449,610.32 | -0.013 | -0.019 | -0.151 | 74.48% | 90 |
| 10% | 14-day from 2025-07-01 | -19.82% | -$198,160.29 | -0.116 | -0.162 | -0.229 | 60.61% | 14 |

All three settings were net positive over the trailing three-year window, but each had a 65%–73% maximum drawdown. Each setting lost money over the trailing one-year, trailing five-year, and Q3-2025-to-present windows. This matrix does not establish a deployable stop setting; it is not used to choose or retune one.

## Consecutive 14-day PnL summary

There are 32 complete non-overlapping 14-day windows from 2025-07-01 to 2026-09-30 00:00 UTC. The remaining 8 days are excluded from this table and from the frequency bins.

| Stop | Median PnL | P10 | P90 | Worst | Best | Positive | Negative |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 5% | -$4,229.28 | -$74,199.44 | $92,892.21 | -$270,654.55 | $132,285.40 | 16 | 16 |
| 7.5% | $5,896.43 | -$83,621.05 | $94,741.85 | -$261,382.41 | $132,695.27 | 17 | 15 |
| 10% | $2,191.05 | -$74,750.51 | $95,235.39 | -$260,784.42 | $139,993.38 | 17 | 15 |

## 14-day PnL ranges and frequencies

Bins use NumPy automatic widths. Only nonzero-frequency bins are listed; any gaps between ranges had zero windows. Lower bounds are inclusive and upper bounds exclusive, except the final upper bound is included.

| Stop | PnL range | Frequency |
|---:|---:|---:|
| 5% | -$270,654.55 to -$220,287.06 | 1 |
| 5% | -$119,552.07 to -$69,184.57 | 6 |
| 5% | -$69,184.57 to -$18,817.08 | 7 |
| 5% | -$18,817.08 to $31,550.41 | 7 |
| 5% | $31,550.41 to $81,917.91 | 7 |
| 5% | $81,917.91 to $132,285.40 | 4 |
| 7.5% | -$261,382.41 to -$225,557.17 | 1 |
| 7.5% | -$189,731.93 to -$153,906.68 | 1 |
| 7.5% | -$153,906.68 to -$118,081.44 | 1 |
| 7.5% | -$118,081.44 to -$82,256.19 | 1 |
| 7.5% | -$82,256.19 to -$46,430.95 | 3 |
| 7.5% | -$46,430.95 to -$10,605.70 | 8 |
| 7.5% | -$10,605.70 to $25,219.54 | 7 |
| 7.5% | $25,219.54 to $61,044.79 | 4 |
| 7.5% | $61,044.79 to $96,870.03 | 2 |
| 7.5% | $96,870.03 to $132,695.27 | 4 |
| 10% | -$260,784.42 to -$216,253.55 | 1 |
| 10% | -$127,191.82 to -$82,660.95 | 2 |
| 10% | -$82,660.95 to -$38,130.09 | 8 |
| 10% | -$38,130.09 to $6,400.78 | 7 |
| 10% | $6,400.78 to $50,931.64 | 9 |
| 10% | $50,931.64 to $95,462.51 | 1 |
| 10% | $95,462.51 to $139,993.38 | 4 |

## Assumptions and limits

- Stop trigger: hourly close at or below the registered percentage loss from weighted-average entry price; order is filled at the next hourly close.
- After full liquidation, the coin has a 24-hour re-entry cooldown. Market taker fee is 0.1% per side and modeled slippage is 10 bps per order. Maker fee is configured at 0.05%, but these market-order runs do not pay maker fees.
- Price/volume history is Binance Vision spot USDT, treated as a USD proxy. Current Roostoo pair precision and minimum-order data are from the public exchange-info snapshot dated 2026-09-30. Several isolated missing hourly bars are forward-filled with zero volume and excluded from live-return calculations.
- The linked problem statement currently lists a $100,000 mock balance, while this user-requested research matrix uses $1,000,000 starting cash. Confirm the applicable event edition and live balance before deployment. The linked page also lists the maker/taker rates used here.
- No holdout was used. Results are proxy backtests; they do not measure Roostoo fills, spreads, or live execution. Stop-loss execution is implemented in the backtest simulator; `scripts/run_bot.py` remains a paper/dry-run entry point without an exchange trading adapter.

Raw per-case metrics, window histograms, and symbol stop counts: [`stop-loss-matrix-2026-09-30.json`](stop-loss-matrix-2026-09-30.json). Data source: [Binance Vision public spot klines](https://data.binance.vision/). Event terms: [linked problem statement](https://luma.com/coghwiyt).
