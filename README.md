# qtrend — a long-only, volatility-managed momentum rotation bot for Roostoo

A rule-based crypto trading bot for a Roostoo mock-exchange competition. The strategy is
deliberately ordinary: multi-horizon, risk-adjusted cross-sectional momentum on a
liquidity-screened universe, sized so that estimated portfolio volatility hits a target, with
cash as the only defensive asset.

The work is not in the signal. It is in **turnover control**, **drawdown control**, and
**making the backtest honest**. Those are the three things that decide a 14-day contest.

---

## Table of contents

1. [Design principles](#1-design-principles)
2. [Roostoo trading conditions](#2-roostoo-trading-conditions)
3. [Strategy specification](#3-strategy-specification)
4. [Known drawbacks and mitigations](#4-known-drawbacks-and-mitigations)
5. [Risk controls and trading practice](#5-risk-controls-and-trading-practice)
6. [No-hardcoding policy](#6-no-hardcoding-policy)
7. [No-look-ahead policy](#7-no-look-ahead-policy)
8. [Repository layout](#8-repository-layout)
9. [Backtester design](#9-backtester-design)
10. [Build and run](#10-build-and-run) — including [building with an agent](#101-building-with-claude-code) and [entering API keys](#104-entering-roostoo-api-keys)
11. [Research protocol](#11-research-protocol)
12. [References](#12-references)

---

## 1. Design principles

These are constraints on the code, not aspirations.

| Principle | What it means mechanically |
|---|---|
| **Backtest equals live** | The strategy is pure functions with no I/O. Order planning lives in one function used by both the simulator and the live engine. The simulator can replay a live decision exactly. |
| **Never trade blind** | Any exception, stale feed, unexplained equity move, or uncertain order response means the cycle places no orders. The bot holds and alerts. It never guesses. |
| **Nothing hardcoded** | Every threshold, horizon, universe rule, fee, and limit is a config value. A literal number in a strategy or execution module is a bug. See §6. |
| **No information from the future** | Every decision at time `t` is computed from bars whose *close time* is `≤ t`. Enforced by an automated checker that runs before any result is reported. See §7. |
| **Autonomous** | The bot alone calls the trading API. There is no manual-trading path. The kill switch is a config field, not a human at a keyboard. |
| **Everything journalled** | Every request, response, and decision is written to an append-only JSONL log stamped with the git commit that produced it. |

---

## 2. Roostoo trading conditions

> Verify exchange mechanics against the API documentation. For competition terms, use the
> problem statement and team email for your event edition; terms differ between hackathons.

### Exchange mechanics

| Item | Value to confirm | Where it lives in config |
|---|---|---|
| REST base URL | `https://mock-api.roostoo.com` | `[exchange] base_url` |
| Authentication | `RST-API-KEY` header plus `MSG-SIGNATURE` header | `.env` (never committed) |
| Signature | HMAC-SHA256 over `totalParams`, keyed with the secret | — |
| `totalParams` | The query string for GET, the request body for POST | — |
| POST content type | `application/x-www-form-urlencoded` | — |
| Timestamp | 13-digit millisecond epoch, required on signed endpoints | — |
| Timestamp tolerance | Request rejected if `abs(serverTime - timestamp)` exceeds the documented window | `[exchange] max_clock_skew_ms` |
| Instruments | Spot only, no leverage. Confirm whether a 1x short is permitted for your event | `[strategy] allow_short` |
| Universe | All coins listed on the Roostoo app; no restriction on how many you trade | `[universe]` rules |
| Historical prices | **No historical endpoint.** Signals must be computed from an external source | `[data] source` |

### Costs and limits

| Item | Value to confirm | Config key |
|---|---|---|
| Market order commission | 0.1% per side on the problem statement linked in the team resource pack | `[costs] taker_fee` |
| Limit order commission | 0.05% per side on the problem statement linked in the team resource pack | `[costs] maker_fee` |
| Strategy / request rules | No HFT, market-making or arbitrage; excessive server requests can fail | strategy and API pacing |
| General API rate limit | Confirm the per-minute request ceiling and pace below it | `[limits] api_calls_per_minute` |
| Minimum order notional | Confirm per pair from `exchangeInfo` | fetched, not hardcoded |
| Lot size / precision | Confirm per pair from `exchangeInfo` | fetched, not hardcoded |
| Starting mock balance | Varies by event | fetched from the balance endpoint |

The linked [problem statement](https://luma.com/coghwiyt) currently lists spot trading without
leverage, 0.1% taker and 0.05% maker commission, and a $100,000 mock portfolio. Its current page
title names an HK/AU/IN event, while the team invitation describes the APAC University Quant
Trading Hackathon. Confirm the applicable edition and starting balance with the team's event
materials before deployment. The requested research runs deliberately keep the previously
specified **$1,000,000 initial cash**; this is a backtest assumption and is not changed to the
page's $100,000 figure. The strategy uses market orders, so modeled fills pay the taker fee.

### Competition rules to encode

Some events impose rules that the bot itself must satisfy. Check for, and encode, at minimum:

- **Minimum activity requirements** (e.g. "must trade on at least N days"). A calm fortnight can
  produce fewer trading days than you expect. If such a rule exists, add an activity tracker that
  forces a genuine rebalance when the pace falls behind.
- **Codebase redeployment windows** — some events allow exactly one mid-competition update.
- **Open-source submission** — assume the repo will be read and diffed against other entries.
- **Anti-plagiarism.** Read other teams' published entries for ideas. Do not copy code, structure,
  or prose from them. Attribute any idea you take in the whitepaper.

### The data split this forces

Because Roostoo exposes no price history, the system has two data planes:

```
Signals      ← external hourly OHLCV (e.g. a public exchange's klines endpoint)
Execution    ← Roostoo (orders, fills, balances, live ticker)
Portfolio    ← Roostoo, reconciled from the exchange every cycle
```

The backtester must simulate Roostoo's *costs and constraints* while running on the external
price panel. Record the basis risk: external prices and Roostoo's quotes are not identical, and
thin pairs near your liquidity floor can quote several basis points wide. Your simulator fills at
the close with no spread, so its costs are a **lower bound**.

---

## 3. Strategy specification

All parameters below are named config keys. The values shown are *starting points to be
validated*, not tuned results.

### 3.1 Eligibility

A pair is eligible at time `t` while its **trailing median** daily dollar volume over a lookback
window is at or above a floor.

```
eligible(pair, t) = median(hourly_dollar_volume[pair, t-W : t]) * 24 >= floor
```

Config: `[universe] volume_window_days`, `[universe] min_daily_dollar_volume`,
`[universe] quote_asset`.

Use the **median**, not the mean. A mean lets a single pump-hour admit a microcap into the
ranking during the very move that makes it rank well. This is a known and measured failure mode:
a volume floor applied to a mean admits small names as they pump, and the resulting exposure can
account for most of a backtest's profit while also holding through single-day losses near 50%.

The rule must be a **rule**, evaluated hour by hour, never a hand-picked list. A list chosen using
volume observed at the end of your sample is survivorship bias that no look-ahead checker can
detect, because the universe *is* the set of columns the checker operates on.

### 3.2 Volatility forecast

Exponentially weighted variance on hourly log returns:

```
sigma2[t] = lam * sigma2[t-1] + (1 - lam) * r[t]^2
sigma_daily = sqrt(sigma2[t] * 24)
```

Config: `[vol] lambda`, `[vol] min_observations`, `[vol] floor`.

Three requirements:

- **Skip stale bars.** A carried-forward price produces a zero return, which an estimator will
  read as calm. Feed the estimator only bars flagged live.
- **Floor sigma.** A newly listed or stalled pair can produce a near-zero denominator that sends
  the signal to infinity.
- **Score the vol model on QLIKE, not MSE.** QLIKE penalises *under*-forecasting variance far
  more heavily. For a risk control, under-forecasting is the one error that cannot be tolerated —
  it produces maximum exposure exactly when markets turn violent. A fitted model that wins on MSE
  can lose on QLIKE precisely because its coefficients shrink toward the mean and it under-predicts
  spikes.

### 3.3 Signal

For each horizon `H` in a configured list, computed on prices lagged by a configured skip:

```
r_H  = log( P[t - skip] / P[t - skip - H] )
z_H  = r_H / ( sigma_daily * sqrt(H_in_days) )
signal = mean over H of z_H
```

Config: `[signal] horizons_days`, `[signal] skip_hours`.

- **Dividing by own volatility** is what stops the highest-volatility memecoin winning the
  ranking every single day. It asks "how many standard deviations has this moved", not "how much".
- **The skip** exists because very short-horizon returns reverse rather than continue
  (Jegadeesh 1990).
- **Averaging several horizons** is a cheap ensemble against picking the wrong one. It is not a
  free lunch: it will lag a fast turn more than the shortest horizon alone would.

### 3.4 Selection

Once per day at a configured UTC hour, rank eligible pairs by signal and hold the top `K`. An
existing holding is kept while it remains within the top `K_keep`, where `K_keep > K`.

Config: `[selection] hour_utc`, `[selection] top_k`, `[selection] keep_rank`,
`[selection] min_signal`.

The hysteresis band is what makes the strategy affordable. Hourly re-selection is a known
turnover disaster: at a 0.1% fee per side, rotating the whole book daily costs on the order of
2.8% over a fortnight, which is larger than any edge you can expect to find. Never enter on a
signal below `min_signal` (default zero) — a long-only book holding the "least bad" asset in a
falling market is holding a loser by construction.

### 3.5 Weighting

Equal weight across holdings, subject to a per-name cap.

Config: `[weights] scheme`, `[weights] max_single_name`.

Inverse-volatility weighting is the obvious alternative and is implemented behind the same config
key. It tilts the book toward the lowest-volatility names, which produces shallower tails and
lower rally participation. Which one you want depends on whether your event gates on return
before scoring risk ratios. Decide that from the rule sheet, not from the backtest.

### 3.6 Exposure — the volatility target

Estimate portfolio daily volatility under a constant-correlation model and scale the whole book:

```
sigma_p = sqrt( sum(w_i^2 * sigma_i^2)
              + rho * sum_{i != j}(w_i * w_j * sigma_i * sigma_j) )

k = min( max_leverage, target_daily_vol / sigma_p )
```

Every weight is multiplied by `k`. The remainder is cash.

Config: `[exposure] target_daily_vol`, `[exposure] rho_estimation`, `[exposure] max_leverage`.

**Measure `rho` from your panel. Do not assume it.** Crypto cross-correlations are high and rise
under stress: major coins commonly sit at 0.8–0.9 against Bitcoin, and during crisis periods
correlations between Bitcoin and altcoins increase rather than fall. Estimate `rho` on a rolling
window and validate it by comparing **realised** portfolio volatility in the backtest against the
target. If realised vol overshoots during selloffs, your correlation model is what broke.

In a long-only book, volatility targeting can only ever *reduce* exposure. The mechanism by which
this can raise returns as well as cut drawdown is that the periods it removes are net losers,
because volatility rises inside selloffs (Moreira & Muir 2017). It does not have to be *right*
about a regime, which is why it outperforms binary regime switches.

### 3.7 Execution

Trade a holding only when it has drifted beyond a band from its target weight. Sells before buys,
so a rotation fits in available cash. Market orders by default.

Config: `[execution] drift_band`, `[execution] order_type`, `[execution] cash_buffer`,
`[execution] sells_before_buys`.

---

## 4. Known drawbacks and mitigations

Every entry below is a documented weakness of this strategy family. The third column is what the
mitigation *costs*, because every one of them costs something.

| Drawback | Evidence | Mitigation | What it costs |
|---|---|---|---|
| **Whipsaw in choppy and vol-spiking markets.** The dominant failure mode, and it has worsened. | Momentum returned −0.73% per month around volatility spikes vs +0.54% otherwise across 1994–2024; −0.96% vs +0.65% in 2014–2024 (Mozes 2026). Trend systems buy high, sell low, buy higher, sell low again in chop. | Selection hysteresis (`keep_rank > top_k`), a drift band, daily rather than hourly selection, and a skip on the most recent bars. | Slower to enter a genuine new trend. Nothing eliminates this; a chop fortnight is a losing fortnight. |
| **Momentum crashes.** A single coin can wipe out portfolio returns. | Crypto momentum is subject to severe crashes; even one cryptocurrency can render momentum portfolio returns insignificant (Grobys et al. 2025). | Volatility targeting (§3.6) plus a per-name weight cap. Vol management virtually eliminated crashes and nearly doubled momentum's Sharpe (Barroso & Santa-Clara 2015; Daniel & Moskowitz 2016). | Cuts exposure into rallies, because crypto rallies arrive with rising volatility. Directly costs return-gate performance. |
| **Long-short crash risk** from the loser leg rebounding. | Crashes occur in rebounding bear markets when momentum displays negative betas. | **Long-only.** Long-only momentum strategies are not subject to these deep crashes. | Gives up the short leg's alpha and any market-neutrality. |
| **Fees destroy the edge through turnover.** | A market round trip costs two times the taker fee. Daily full rotation can exceed the available edge over a fortnight. | Daily selection, hysteresis, drift band, minimum order notional, and a fee model in the backtester that is never optional. | Tracking error against the ideal target weights. |
| **Vol-estimate lag.** A trailing window is slow to notice that an asset has become dangerous. | A flat window takes days to react; an exponentially weighted one reacts within hours. | EWMA with a high `lambda`; score it on QLIKE (§3.2). | More sensitive to single outlier bars. Needs a floor and a stale-bar mask. |
| **Regime gates whipsaw worse than the thing they fix.** | Binary "go to cash when below trend" filters give up most of the return for a few points of tail improvement, with sharply negative in-market ratios — the signature of selling after falls and buying after rises. | **Do not use one.** Continuous de-risking via the vol target instead. | You will sit through drawdowns that a perfect regime call would have avoided. |
| **Drawdown brakes lock the book out.** | A brake without a reset locks into cash permanently once triggered; with a cooldown it re-enters into continuing drawdowns, producing more trades and less return for marginal tail improvement. | Prefer the vol target. If you implement a brake, it needs an explicit, tested re-entry rule and it counts as one of your configurations. | A brake that never triggers in-sample is untested code running live. |
| **Stop-losses are conditionally, not universally, helpful.** | Under the random walk hypothesis simple stop-loss rules *always decrease* expected return; with momentum present the stopping premium is positive and proportional to return persistence (Kaminski & Lo 2014). They added no value at short sampling frequencies but raised Sharpe at longer intervals. A 15% per-position trigger gave higher average returns, lower variance and higher skewness (Han, Zhou & Zhu 2016). | Per-name stop implemented behind a config flag, **off by default**, tested as one of your ten configurations. Never a stop tighter than a few multiples of the asset's own daily sigma. | A stop tight relative to crypto volatility converts the strategy into a fee-generating whipsaw machine. |
| **Liquidity-floor gaming.** A volume floor admits microcaps during their pump. | Measured in a prior entry: surge-admitted names produced most of a four-month return, and one such name also fell 49% in a day. | Median volume rather than mean; a longer volume window; per-name cap; optionally half-weight for recently admitted names. | Removes the positively skewed lottery tickets that the scoring function actually rewards. Genuinely two-sided. |
| **Estimation error in the correlation matrix.** You estimate O(n²) parameters from O(n) data. | Crypto correlations are high, unstable, and rise under stress. | Constant-correlation model with a single estimated `rho`, not a full matrix. Validate against realised portfolio vol. | Understates diversification among genuinely different names; overstates it in a crash. |
| **The 14-day window cannot distinguish skill from luck.** | Two years of hourly data contains roughly 43 independent fortnights. Medians are trustworthy; extreme quantiles are indicative at best. | Report the *distribution* over rolling windows, bootstrap it, and state the p10 alongside the median. Never quote a single total return. | Your headline number looks less impressive. It is also true. |
| **Backtest overfitting.** | Under memory effects, backtest overfitting leads to *negative* expected out-of-sample returns, not zero (Bailey, Borwein, López de Prado & Zhu 2014). Selection across N trials inflates the best Sharpe mechanically (Deflated Sharpe Ratio). | Pre-registered hypotheses, a hard configuration budget, plateau-over-peak selection, a sealed holdout opened once. See §11. | You will leave apparent performance on the table. That performance was not real. |

---

## 5. Risk controls and trading practice

Every control below is mandatory and config-driven. Each one exists because of a specific way a
bot loses money or gets disqualified.

### 5.1 Order safety

| Control | Rule | Config |
|---|---|---|
| **No retry on uncertain response** | If a `place_order` POST times out or returns an unparseable response, **stop the cycle**. Do not retry. A duplicate fill is worse than a missed one. | — (hard rule) |
| **Reconcile from the exchange** | Read positions and balances from the exchange every cycle. Never trust local state across cycles. | — (hard rule) |
| **Idempotency** | Attach a client order ID derived from `(cycle_id, symbol, side)` if the API supports it. | `[execution] use_client_order_id` |
| **Per-order size cap** | No single order may exceed a fraction of equity. A runaway guard against a sign error. | `[limits] max_order_fraction` |
| **Rate pacing** | A token-bucket limiter in the client. Pace so a full rebalance stays under the exchange ceiling *including retries*. | `[limits] api_calls_per_minute`, `[limits] min_seconds_between_orders` |
| **Cash buffer** | Sells execute before buys, and a buffer fraction is held back so buys do not fail on rounding. | `[execution] cash_buffer` |
| **Precision and notional** | Round every quantity down to the pair's step size; skip any order below the pair's minimum notional. Both fetched from `exchangeInfo`, never hardcoded. | fetched |

### 5.2 Halt conditions

The bot places **no orders** for a cycle when any of these fire, and writes an alert:

| Condition | Config |
|---|---|
| Any unhandled exception anywhere in the cycle | — |
| Data feed staleness beyond a threshold | `[safety] max_data_age_seconds` |
| Unexplained equity move beyond a threshold since last cycle | `[safety] max_unexplained_equity_move` |
| Clock skew against exchange server time beyond the signature window | `[exchange] max_clock_skew_ms` |
| Fewer than `min_observations` volatility samples for a candidate | `[vol] min_observations` |
| Estimated portfolio volatility is NaN or non-finite | — |

### 5.3 Position and portfolio limits

| Limit | Purpose | Config |
|---|---|---|
| Max single-name weight | Caps blowup from one coin | `[weights] max_single_name` |
| Max invested fraction | Hard ceiling regardless of what the vol target computes | `[exposure] max_leverage` |
| Per-name stop-loss | Optional, off by default, expressed as a loss fraction from average entry price | `[risk] stop_loss_pct`, `[risk] stop_loss_enabled` |
| Stop re-entry cooldown | Prevents immediate re-buy of a stopped name | `[risk] stop_cooldown_hours` |
| Daily portfolio loss limit | Optional circuit breaker with an explicit, tested re-entry rule | `[risk] daily_loss_limit`, `[risk] loss_limit_reset` |

**On stop-loss sizing:** stop distance is a configured fraction from weighted-average entry price.
The simulator triggers when an hourly close crosses the threshold and fills at the next hourly
close, with modeled fees and slippage. After a full stop exit it blocks that coin for the
configured cooldown. Fixed percentage stops do not adjust to each coin's volatility; compare
thresholds across a fixed universe only when that limitation is intended.

### 5.4 Operational

| Control | Implementation |
|---|---|
| **Kill switch** | A `mode` field in a committed config file, re-read every cycle. Values: `trade`, `hold`, `liquidate`. No manual-trading path exists. |
| **Journalling** | Append-only JSONL: every signed request, every response, every cycle's inputs and decisions, stamped with the git commit hash. |
| **Replay** | `scripts/replay.py` re-derives every journalled decision from the journalled inputs and diffs it. A judge can run this. |
| **Secret hygiene** | Credentials in `.env`, git-ignored. A pre-commit hook scans staged changes for key-shaped strings. The repo is submitted publicly. |
| **Restart safety** | On startup, reconcile before deciding. Never place an order on the first cycle after a restart without a completed reconciliation. |

---

## 6. No-hardcoding policy

**Rule: a numeric or string literal in `strategy/`, `execution.py`, or `engine/` is a bug.**

Everything lives in one TOML file per bot. The config is loaded once, validated against a schema
at startup, and passed down explicitly. No module reads global state.

```toml
[meta]
name = "..."
mode = "trade"            # trade | hold | liquidate — re-read every cycle

[exchange]
base_url          = "..."
max_clock_skew_ms = 0

[data]
source             = "..."
interval           = "..."
history_days       = 0
cache_dir          = "..."
warmup_halflives   = 0      # EWMA half-lives required before a decision is scored

[universe]
quote_asset                = "..."
volume_window_days         = 0
min_daily_dollar_volume    = 0
exclude                    = []

[vol]
lambda           = 0.0
min_observations = 0
floor            = 0.0

[signal]
horizons_days = []
skip_hours    = 0

[selection]
hour_utc   = 0
top_k      = 0
keep_rank  = 0
min_signal = 0.0

[weights]
scheme          = "equal"   # equal | inverse_vol
max_single_name = 0.0

[exposure]
target_daily_vol = 0.0
rho_estimation   = "rolling"
rho_window_days  = 0
max_leverage     = 1.0

[execution]
drift_band           = 0.0
order_type           = "market"
cash_buffer          = 0.0
sells_before_buys    = true
use_client_order_id  = true

[costs]
taker_fee = 0.0
maker_fee = 0.0
slippage_bps = 0.0

[limits]
api_calls_per_minute      = 0
min_seconds_between_orders = 0
max_order_fraction         = 0.0

[risk]
stop_loss_enabled  = false
stop_loss_pct      = 0.0
stop_cooldown_hours = 0
daily_loss_limit   = 0.0

[safety]
max_data_age_seconds           = 0
max_unexplained_equity_move    = 0.0

[backtest]
initial_cash   = 0
start          = "..."      # first bar loaded; must precede report_from by the warm-up
report_from    = "..."      # first bar whose decision is scored
in_sample_end  = "..."
holdout_start  = "..."
window_days    = 0
step_days      = 0
```

**The placeholders above are placeholders.** `0` and `"..."` are not defaults and not suggestions.
Nobody — human or agent — fills them in as part of building the system. They are set by you, once,
from the rule sheet (§2) or from a pre-registered research run (§11). A config that still contains
placeholders should fail validation with a message naming the unset keys, which is the desired
behaviour: it makes an unconfigured bot refuse to start rather than trade on invented numbers.

### 6.1 Derived values

Some quantities are **computed from config, never configured directly**. Deriving them is not a
violation of the no-hardcoding rule — it is the point of it. A number that can be derived and is
instead typed in by hand is a number that will silently disagree with the values it should follow.

`warmup_bars` is the main one. Every stage of the strategy needs history before its output means
anything, and the requirement is a function of parameters you already set:

```
warmup_bars = max(
    max(signal.horizons_days) * bars_per_day + signal.skip_hours,
    universe.volume_window_days * bars_per_day,
    data.warmup_halflives * ewma_halflife_bars(vol.lambda),
)

where ewma_halflife_bars(lam) = log(0.5) / log(lam)
```

Change `horizons_days`, `volume_window_days` or `lambda`, and the warm-up follows automatically.
The only free parameter is `warmup_halflives` — how many EWMA half-lives you require before
trusting the volatility estimate. It is a placeholder like any other.

This matters more than it looks. At a high `lambda` the half-life runs to dozens of hours and full
convergence takes weeks. A backtest that starts scoring before the estimator has converged runs on
a volatility forecast that reads everything as calm, so the vol target leaves exposure at maximum
through exactly the period you should distrust. Nothing crashes. You get a plausible equity curve.

Validation must therefore reject a config where `report_from` is earlier than
`start + warmup_bars`, with an error stating the required warm-up and the shortfall — not merely
reject `report_from` earlier than `start`.

**Additional rules:**

- **Pair metadata is fetched, not written.** Lot sizes, tick sizes, and minimum notionals come from
  `exchangeInfo`. Commit a snapshot of it so backtests are reproducible when the exchange relists
  pairs, but never type the values into source.
- **No hidden constants in tests.** Test fixtures build configs explicitly so a schema change
  breaks the tests loudly.
- **Config is validated, not trusted.** Reject at startup: `keep_rank <= top_k`,
  `max_single_name * top_k < 1`, `lambda` outside `(0, 1)`, empty `horizons_days`, any fee that is
  negative or absurd. A misconfigured bot must refuse to start, not trade strangely.

---

## 7. No-look-ahead policy

### 7.1 The rules

1. **Index every bar by its close time**, the moment its close price became known — never by open
   time. This single convention removes the most common source of leakage.
2. **A decision at time `t` may read rows with index `<= t`.** Nothing else. Enforce it at the
   data-access layer by slicing the panel before handing it to a strategy function, so a strategy
   function is physically incapable of seeing the future.
3. **Forward-fill is flagged, never silent.** Missing bars carry the last close forward *and* set a
   `stale` mask. Consumers that must not treat a flat carried price as a real zero return (the
   volatility estimator, the signal) read the mask.
4. **The universe rule sees only past data.** Eligibility at `t` uses volume up to `t`. This
   deserves separate attention: see §7.3.
5. **Fills use prices at or after the decision.** Never fill a decision made on the close of bar
   `t` at the close of bar `t`. Fill at the next bar, or model the delay explicitly.
6. **Parameters are chosen on in-sample data only.** A parameter chosen on the holdout has leaked
   the future just as surely as a misaligned index.

### 7.2 The automated checker

```python
def assert_no_lookahead(strategy_fn, panel, t, config, seed):
    """Perturb all data strictly after t; assert the decision at t is unchanged."""
    baseline  = strategy_fn(panel.slice_to(t), config)
    perturbed = panel.copy()
    perturbed.randomize_after(t, seed=seed)     # prices, volumes, everything
    result    = strategy_fn(perturbed.slice_to(t), config)
    assert result == baseline, f"look-ahead detected at {t}"
```

This runs across a sample of timestamps for **every** strategy function before any backtest result
is reported. It is not optional and it is not a one-off.

### 7.3 What the checker cannot catch

The checker perturbs values **within a fixed set of columns**. If your universe is a hand-picked
list, the universe *is* the set of columns, and hindsight in choosing it is invisible to this test.
This is a real and expensive bias: a prior entry measured it on themselves and found that
correcting a hindsight-chosen universe dropped their composite score from 1.05 to 0.75, level with
simply holding Bitcoin. It was most of their measured edge.

**Defences:**

- Never ship a hardcoded pair list. Ship a rule (§3.1) applied hourly to everything the exchange
  lists.
- Build the candidate pool from a **committed snapshot** of exchange listings so results are
  reproducible, and state in the whitepaper that the snapshot cannot see pairs delisted before it.
- Acknowledge the residual bias explicitly. The rule is still an optimistic bound: it cannot see
  coins that were never listed.

---

## 8. Repository layout

```
src/qtrend/
  config.py            load + validate TOML; typed config objects; no defaults in code
  roostoo/
    client.py          signing, clock sync, typed endpoints, token-bucket rate limiter
    exchange_info.py   lot sizes, min notionals, precision — fetched and cached
  data/
    fetch.py           external OHLCV history -> parquet cache
    panel.py           hourly UTC panel, close-time indexed, stale mask, slice_to(t)
  strategy/
    universe.py        eligibility rule                     [pure]
    volatility.py      EWMA forecast, QLIKE scoring         [pure]
    signal.py          multi-horizon risk-adjusted momentum [pure]
    selection.py       ranking + hysteresis                 [pure]
    weights.py         equal / inverse-vol, per-name cap    [pure]
    exposure.py        constant-correlation vol target      [pure]
    baselines.py       buy-and-hold, equal-weight, no-target twin
  execution.py         plan_orders() — the ONE order planner, shared by sim and live
  backtest/
    simulator.py       event loop
    costs.py           fee + slippage model
    metrics.py         Sharpe, Sortino, Calmar, composite, drawdown
    windows.py         rolling-window scoring, bootstrap
    lookahead.py       the checker from §7.2
  engine/
    loop.py            live cycle: reconcile -> decide -> plan -> execute -> journal
    safety.py          halt conditions from §5.2
    journal.py         append-only JSONL with commit stamping
configs/               one TOML per bot
data/snapshots/        committed exchangeInfo snapshot
scripts/
  fetch_history.py
  run_backtest.py
  run_bot.py
  replay.py
  check_secrets.py
tests/                 offline, deterministic
docs/
  whitepaper.md
  preregistration.md   hypotheses and accept rules, committed BEFORE each run
```

The `[pure]` modules take data and config, return values, and perform **no I/O**. That is what
makes "backtest equals live" mechanically true rather than aspirational.

---

## 9. Backtester design

### 9.1 What it must get right

A backtester that is wrong in your favour is worse than no backtester. In priority order it must
model: **look-ahead** (§7), **fees**, **minimum notional and lot size**, **the drift band**, and
**determinism**. A single tie-break in fill ordering has been observed to compound into an
11-point difference in total return over 20 months.

### 9.2 Module contract

```python
# backtest/simulator.py

@dataclass(frozen=True)
class Fill:
    timestamp: datetime
    symbol: str
    side: str            # "BUY" | "SELL"
    quantity: Decimal
    price: Decimal
    fee: Decimal

@dataclass(frozen=True)
class BookState:
    cash: Decimal
    positions: dict[str, Decimal]      # symbol -> quantity

    def equity(self, prices: dict[str, Decimal]) -> Decimal: ...
    def weights(self, prices: dict[str, Decimal]) -> dict[str, float]: ...

@dataclass(frozen=True)
class CycleRecord:
    """Everything needed to re-derive this decision. Written to the journal."""
    timestamp: datetime
    eligible: list[str]
    signals: dict[str, float]
    sigmas: dict[str, float]
    selected: list[str]
    target_weights: dict[str, float]
    scale_k: float
    orders: list[Order]
    fills: list[Fill]
    equity: Decimal
    halted_reason: str | None


def run_backtest(
    panel: Panel,
    config: Config,
    exchange_info: ExchangeInfo,
    start: datetime,
    end: datetime,
) -> BacktestResult:
    ...
```

### 9.3 The event loop

```python
def run_backtest(panel, config, exchange_info, start, end):
    book    = BookState(cash=config.backtest.initial_cash, positions={})
    records = []
    held    = {}          # symbol -> entry price, for the optional stop
    equity_curve = []

    for t in panel.timestamps_between(start, end):          # hourly
        visible = panel.slice_to(t)                          # HARD BOUNDARY
        prices  = visible.last_prices()

        if visible.n_rows < config.derived.warmup_bars:      # see §6.1
            records.append(CycleRecord(timestamp=t, halted_reason="warmup"))
            continue

        equity_curve.append((t, book.equity(prices)))

        # --- optional per-name stop, checked every hour -------------------
        if config.risk.stop_loss_enabled:
            stops = check_stops(book, held, prices, visible, config)
            if stops:
                book, fills = apply_orders(book, stops, prices,
                                           exchange_info, config.costs)
                # record and continue; stopped names enter cooldown

        # --- selection happens only at the configured hour ----------------
        if t.hour != config.selection.hour_utc:
            continue

        # --- pure strategy functions, in order ---------------------------
        eligible = universe.eligible(visible, config.universe)
        sigmas   = volatility.forecast(visible, eligible, config.vol)
        signals  = signal.compute(visible, eligible, sigmas, config.signal)
        selected = selection.select(signals, held.keys(), config.selection)
        weights  = weights_mod.assign(selected, sigmas, config.weights)
        targets  = exposure.scale(weights, sigmas, visible, config.exposure)

        # --- shared order planner: identical code path as live ------------
        orders = execution.plan_orders(
            current   = book.weights(prices),
            target    = targets,
            equity    = book.equity(prices),
            prices    = prices,
            info      = exchange_info,
            config    = config.execution,
        )

        # --- fill at the NEXT bar, never this one -------------------------
        fill_prices = panel.prices_at(panel.next_timestamp(t))
        book, fills = apply_orders(book, orders, fill_prices,
                                   exchange_info, config.costs)

        records.append(CycleRecord(...))

    return BacktestResult(equity_curve, records, config, git_commit())
```

Five things to notice, because each is a bug class:

1. `panel.slice_to(t)` is the only way strategy code touches data. Make the raw panel private.
2. Stops are checked **hourly**, selection runs **daily**. Different clocks, one loop.
3. `execution.plan_orders` is the same function the live engine calls. Not a copy.
4. Fills happen at the *next* bar. A backtest that fills at the decision bar's close is lying.
5. The result carries the config and the commit hash. A result you cannot reproduce is not a
   result.

### 9.4 Cost model

```python
# backtest/costs.py
def apply_costs(notional, side, order_type, config) -> Decimal:
    fee       = config.taker_fee if order_type == "market" else config.maker_fee
    slippage  = notional * config.slippage_bps / 10_000
    return notional * fee + slippage
```

Set `slippage_bps` above zero. Filling at the close with no spread is optimistic, and thin pairs
near your liquidity floor quote meaningfully wide. Run the whole backtest at two slippage settings
and report both. If your edge disappears at the higher one, you do not have an edge.

### 9.5 Metrics

```python
# backtest/metrics.py
def sharpe(returns, periods_per_year) -> float: ...
def sortino(returns, periods_per_year, mar=0.0) -> float: ...
def max_drawdown(equity_curve) -> float: ...
def calmar(returns, equity_curve) -> float: ...

def composite(returns, equity_curve, weights) -> float:
    """Competition score. Weights come from config, NOT from a literal."""
```

Two traps:

- **Calmar's denominator can approach zero** over a 14-day window in a calm market, producing an
  enormous and meaningless score. Decide the guard now — a floor on max drawdown, or reporting
  Calmar as undefined below a threshold — and apply it consistently across every variant.
- **Sortino needs a stated minimum acceptable return.** Put it in config. Different conventions
  produce materially different numbers and you will need to state yours in the whitepaper.

### 9.6 Window scoring and the bootstrap

```python
# backtest/windows.py
def rolling_windows(equity_curve, window_days, step_days) -> list[Window]: ...
def summarise(windows) -> Summary:
    """median, p10, p90, worst, max drawdown, composite, win rate vs baselines"""
def bootstrap(windows, n_resamples, seed) -> Distribution:
    """Resample INDEPENDENT (non-overlapping) windows. Report p10 and median."""
```

Report the distribution. A single total return over two years tells you nothing about a
fourteen-day contest.

### 9.7 Baselines — run these in the same harness, every time

| Baseline | Why |
|---|---|
| BTC buy-and-hold | The thing you must beat to justify any complexity |
| Equal-weight top-N by volume, rebalanced daily | Isolates whether your *signal* adds anything over diversification |
| Your signal, no volatility target | Isolates what the vol target contributes |
| Your vol target, random selection | Isolates what the signal contributes |
| Cash | Sanity check on the Calmar denominator trap |

If the strategy does not beat all five, the thing that is working is not the thing you think.

---

## 10. Build and run

### 10.1 Building with Claude Code

`CLAUDE.md` in the repository root drives the build. It carries the non-negotiable rules from
this document and splits the work into eleven phases, each with an explicit acceptance gate.

Give the agent one phase at a time. Do not hand it the whole README and ask for a finished bot.
The reason is structural: phases 0–4 establish the data boundary (§7), and **every number the
system produces afterwards inherits it**. A boundary that leaks produces a plausible equity curve
and passes every test written against it. Later phases are recoverable; these are not.

```
Phase 0   repo skeleton, tooling, secret hook          no keys needed
Phase 1   config loader + schema validation            no keys needed
Phase 2   Panel / PanelView / slice_to                 no keys needed   <- the boundary
Phase 3   synthetic data + planted-property tests      no keys needed
Phase 4   look-ahead checker                           no keys needed   <- the boundary
Phase 5   strategy modules (pure functions)            no keys needed
Phase 6   execution.plan_orders (shared)               no keys needed
Phase 7   simulator                                    no keys needed
Phase 8   costs, metrics, windows, field               no keys needed
Phase 9   real data fetcher + first backtest           no keys needed
Phase 10  Roostoo client + local mock exchange         no keys needed
Phase 11  live engine, journal, replay                 keys at the very end
```

**Phases 0 through 10 require no Roostoo credentials at all.** You can build, backtest, score
against a simulated field, and rehearse the full signed-order path against a local mock exchange
before you have keys. See §10.4.

Four failure modes to watch for specifically, because an agent will produce all four confidently:

- **Inventing config values.** Every key in §6 is a placeholder (`0` or `"..."`). An agent will
  fill them with plausible numbers, and you will have hardcoded, unvalidated constants that look
  like configuration. They must stay as placeholders until *you* set them.
- **Encoding the unverified §2 table.** Those figures come from other events. They are marked
  "confirm" because they are not confirmed.
- **"Fixing" the fill-at-next-bar rule.** It reads like an off-by-one to a reviewer. Removing it
  introduces a one-bar look-ahead, nothing fails, and returns improve.
- **Parameter sweeps.** Ask for "the best parameters" and you will get a search over hundreds of
  configurations and an impressive number. That is exactly what §11 exists to prevent. The
  ten-configuration budget is a human discipline; an agent has no reason to honour it.

### 10.2 Environment

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

cp .env.example .env               # leave blank until you have keys; .env is git-ignored
python scripts/install_hooks.py    # pre-commit secret scanner
```

### 10.3 Verification ladder — do these in order, and do not skip:

```bash
pytest                                            # offline, deterministic unit tests
pytest tests/test_lookahead.py -v                 # the checker from §7.2

python scripts/fetch_history.py  --config configs/dev.toml
python scripts/run_backtest.py   --config configs/dev.toml
python scripts/run_backtest.py   --config configs/dev.toml --baselines

python scripts/smoke_public.py                    # public endpoints, no keys needed
python scripts/mock_exchange.py                   # local server implementing the documented API
python scripts/run_bot.py --config configs/paper.toml --once   # one cycle, paper
python scripts/run_bot.py --config configs/paper.toml          # paper loop
python scripts/replay.py   --config configs/paper.toml         # journal vs strategy, must diff clean
```

Rehearse the signed order path against a **local mock exchange** before you have live keys. Have
it validate signatures over the exact bytes sent, enforce the timestamp window, return the
documented error envelopes, and inject faults — timeouts, 429s, partial fills, malformed JSON.
The faults are the point. The happy path is not where bots lose money.

### 10.4 Entering Roostoo API keys

The system is built so that credentials are the **last** thing added and the **only** manual step.
Nothing above this point needs them.

When your keys arrive:

```bash
# 1. Fill in .env — the ONLY file that ever holds a credential. Git-ignored.
ROOSTOO_API_KEY=...
ROOSTOO_SECRET_KEY=...

# 2. Confirm the exchange is reachable and the clock agrees
python scripts/smoke_private.py --config configs/live.toml

# 3. Dry run against the real API: reads balances and plans orders, places none
python scripts/run_bot.py --config configs/live.toml --once --dry-run

# 4. Arm it
#    configs/live.toml -> [meta] mode = "trade"
python scripts/run_bot.py --config configs/live.toml
```

Rules that make this a one-step change rather than a migration:

- **Credentials live only in `.env`**, read once at startup through `config.py`. No module reads
  `os.environ` directly. No credential is ever logged, journalled, printed in a traceback, or
  written to a config file.
- **The exchange adapter is an interface** with three implementations — `PaperExchange`,
  `MockRoostooExchange` (the local server), and `RoostooExchange`. Switching is a config value,
  not a code change.
- **The client fails loudly on a missing key.** It must not silently fall back to paper trading,
  or you will believe you are live when you are not.
- **`mode` defaults to `hold`** in every committed config. Arming the bot is a deliberate,
  separate edit.
- **`--dry-run` plans and journals orders without sending them.** Run it against the real API
  first. It exercises authentication, clock sync, precision rounding and minimum notionals — the
  four things that fail on first contact — without risking a fill.

---

## 11. Research protocol

This section is the difference between a result and a mirage.

**Budget: ten configurations for the entire project.** Count them in
`docs/preregistration.md`. Selection across many trials inflates the best observed Sharpe
mechanically, and under memory effects backtest overfitting produces *negative* expected
out-of-sample returns rather than merely zero (Bailey et al. 2014).

**Pre-register every run.** Before executing, commit: the hypothesis, the parameters to be varied,
the accept rule, and how you will read the result. Then run. A result read after the fact is a
story, not evidence.

**Seal a holdout now.** Split the panel at `[backtest] in_sample_end`. Do not look at the holdout
until the strategy is locked. Open it **once**. Commit the pass rule beforehand — for example:
smaller worst window and smaller max drawdown than the benchmark, and beating the benchmark in at
least some stated fraction of windows. Once opened, it is a consistency check, never again a
holdout.

**Prefer plateaus to peaks.** If two adjacent parameter values both score well and the next falls
off a cliff, take the one in the middle of the plateau. Selecting the peak of a noisy surface is
the textbook definition of an overfitted backtest.

**Establish a noise band and honour it.** Bootstrap your independent windows. If two variants'
returns are highly correlated, differences between their composite scores below your measured band
are **not findings** — including differences that favour you.

**Test neighbours of every arbitrary number.** Any constant you picked by hand — the volume floor,
the volume window, the selection hour — gets a sensitivity run with the reading committed first.
Report the **mean across neighbours**, not the best cell. If the results swing wildly across
neighbours, say so in the whitepaper and stand behind the average.

**Write down what you rejected.** The rejected-hypotheses table is the most informative part of a
submission and the part judges can actually evaluate, because over fourteen days the market path
dominates your realised score. You cannot control whether the fortnight trends. You can control
whether your research is defensible.

---

## 12. References

- Bailey, D., Borwein, J., López de Prado, M. & Zhu, Q. (2014). Pseudo-Mathematics and Financial
  Charlatanism: The Effects of Backtest Overfitting on Out-of-Sample Performance. *Notices of the
  AMS*, 61(5).
- Bailey, D. & López de Prado, M. (2014). The Deflated Sharpe Ratio. *Journal of Portfolio
  Management*, 40(5).
- Barroso, P. & Santa-Clara, P. (2015). Momentum has its moments. *Journal of Financial Economics*.
- Bongaerts, D., Kang, X. & van Dijk, M. (2020). Conditional Volatility Targeting.
- Daniel, K. & Moskowitz, T. (2016). Momentum crashes. *Journal of Financial Economics*.
- Fung, W. & Hsieh, D. (2001). The risk in hedge fund strategies. *Review of Financial Studies*.
- Grobys, K. et al. (2025). Cryptocurrency momentum has (not) its moments. *Financial Markets and
  Portfolio Management*.
- Han, C., Kang, B. & Ryu, J. (2023). Time-Series and Cross-Sectional Momentum in the
  Cryptocurrency Market: A Comprehensive Analysis under Realistic Assumptions. SSRN 4675565.
- Han, Y., Zhou, G. & Zhu, Y. (2016). Taming Momentum Crashes: A Simple Stop-Loss Strategy.
- Harvey, C., Hoyle, E., Korgaonkar, R., Rattray, S., Sargaison, M. & van Hemert, O. (2018). The
  Impact of Volatility Targeting. SSRN 3175538.
- Jegadeesh, N. (1990). Evidence of predictable behavior of security returns. *Journal of Finance*.
- Jegadeesh, N. & Titman, S. (1993). Returns to buying winners and selling losers. *Journal of
  Finance*.
- Kaminski, K. & Lo, A. (2014). When do stop-loss rules stop losses? *Journal of Financial
  Markets*, 18.
- Liu, Y. & Tsyvinski, A. (2021). Risks and returns of cryptocurrency. *Review of Financial
  Studies*.
- Liu, Y., Tsyvinski, A. & Wu, X. (2022). Common risk factors in cryptocurrency. *Journal of
  Finance*.
- Moreira, A. & Muir, T. (2017). Volatility-managed portfolios. *Journal of Finance*.
- Moskowitz, T., Ooi, Y. H. & Pedersen, L. H. (2012). Time series momentum. *Journal of Financial
  Economics*.
- Mozes, H. (2026). Volatility Spikes and Momentum. *Journal of Beta Investment Strategies*,
  Spring 2026.
