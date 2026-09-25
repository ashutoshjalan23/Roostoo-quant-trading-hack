# CLAUDE.md

Build instructions for **qtrend**, a long-only volatility-managed momentum rotation bot for a
Roostoo mock-exchange trading competition.

`README.md` is the specification. This file is the build process. Where they disagree, `README.md`
wins and you should say so rather than resolving it silently.

---

## Read before doing anything

Read `README.md` end to end first, in particular:

- **§3** the strategy specification
- **§5** risk controls
- **§6** the no-hardcoding policy
- **§7** the no-look-ahead policy
- **§9** the backtester design

This is a competition entry that will be submitted open-source and judged by professional quant
traders. Correctness and honesty matter more than features. A backtest that is wrong in our favour
is worse than no backtest.

---

## Non-negotiable rules

Violations of these are bugs regardless of whether tests pass.

### 1. Never invent a config value

Every key in `README.md` §6 is a placeholder — `0` or `"..."`. Leave them. Do not fill in a
"sensible default" for a fee, a lambda, a lookback, a threshold, a `top_k`, or anything else.

Config validation must **reject** a config containing placeholders, with an error naming the unset
keys. An unconfigured bot refuses to start; it does not trade on invented numbers.

There are no default values anywhere in the code. No `config.get(key, fallback)`. No
`DEFAULT_LAMBDA = 0.94`. If a key is missing, that is an error.

### 2. Never encode the unverified exchange figures

`README.md` §2 is marked "verify before trusting". Fee rates, rate limits, minimum notionals and
whether shorting is allowed came from *other* competitions and differ between events. They are
config placeholders and they stay placeholders.

Pair metadata (lot size, tick size, minimum notional) is **fetched** from the exchange and cached,
never typed into source.

### 3. Never run parameter sweeps

Do not search a parameter space. Do not report a "best" configuration. Do not optimise anything
against backtest performance unless explicitly told to, and then only for the specific
pre-registered run described in `docs/preregistration.md`.

If asked to improve performance, improve correctness, coverage, or clarity — not the number.

Rationale (`README.md` §11): selection across N trials inflates the best observed Sharpe
mechanically, and under memory effects backtest overfitting produces negative expected
out-of-sample returns rather than merely zero.

### 4. Never widen the data boundary

`Panel._index`, `Panel._close`, `Panel._quote_volume` and `Panel._stale` are private. Only
`PanelView` reads them. Strategy code receives a `PanelView` and nothing else.

`PanelView` must expose **no** method taking an absolute row index or a future timestamp. If a
strategy author *can* reach past the view's end, eventually one will.

If a later phase seems to need raw panel access, stop and ask. Do not add an accessor.

### 5. Never change the fill timing

Orders decided on the bar closing at `t` fill at the **next** bar. This reads like an off-by-one
error. It is not. Removing it introduces a one-bar look-ahead across the entire backtest, nothing
fails, and returns improve — which is the worst possible failure mode.

### 6. Strategy modules are pure

Everything under `src/qtrend/strategy/` takes data and config and returns values. No file I/O, no
network, no `datetime.now()`, no logging, no global state, no randomness without an injected seed.

This is what makes "backtest equals live" mechanically true rather than aspirational.

### 7. One order planner

`execution.plan_orders` is defined once and imported by both the simulator and the live engine.
Never write a second version, a "backtest variant", or a copy with different rounding.

### 8. Determinism

Same inputs, same outputs, byte for byte. Sort every dict iteration that affects order. Seed every
random draw from config. A single non-deterministic tie-break in fill ordering has been observed
to compound into an 11-point difference in total return over 20 months.

### 9. Credentials

Credentials live only in `.env`, git-ignored, read once at startup through `config.py`. No module
reads `os.environ` directly. No credential is ever logged, journalled, printed in a traceback, or
written to a config file. Never create a file containing a real-looking key, even as an example.

### 10. Honesty in reporting

Never report a single total return as a headline. Report the distribution over rolling windows:
median, p10, p90, worst, max drawdown. Never claim a test passed that you did not run. If a gate
is not met, say so and stop.

---

## How to work: phase gates

Build in the phases below, **one at a time, in order**.

For each phase:

1. State what you are about to build and what the acceptance gate is.
2. Build it.
3. Run the gate.
4. Report the result — pass or fail, honestly — and **stop**. Wait for the go-ahead.

Do not start the next phase because the current one went well. Do not build ahead. Phases 2 and 4
establish the data boundary and every number produced afterwards inherits it, so a leak there
poisons everything downstream while passing every test written against it.

If a gate fails, fix it and re-run. Do not weaken the gate. Do not mark it passed with a caveat.

---

## Phase 0 — skeleton

**Build.** The directory layout from `README.md` §8. `pyproject.toml` with dev extras (pytest,
ruff or equivalent). `.gitignore` covering `.env`, `data/cache/`, `logs/`, `__pycache__`.
`.env.example` with empty values. `scripts/check_secrets.py` scanning for key-shaped assignments,
webhook URLs and bot tokens. `scripts/install_hooks.py` installing it as a pre-commit hook.
`docs/preregistration.md` as an empty template.

**Gate.** `pytest` runs and collects zero tests without error. `python scripts/check_secrets.py
--all` runs clean. The pre-commit hook blocks a commit containing a fake key-shaped string.

**Do not.** Create any file holding a real-looking credential.

---

## Phase 1 — config

**Build.** `src/qtrend/config.py`: load one TOML file, validate against a typed schema, return
frozen typed objects. Credentials read from `.env` separately and never merged into a file that
could be committed.

Validation must reject: any remaining placeholder; `keep_rank <= top_k`;
`max_single_name * top_k < 1`; `lambda` outside `(0, 1)`; empty `horizons_days`; negative or
absurd fees; `report_from` before `start`; `holdout_start` before `in_sample_end`.

**Gate.** A test per validation rule, each asserting the specific error message. A config with
placeholders fails with a message naming the unset keys. No default value appears anywhere in
`config.py`.

**Do not.** Fill in any placeholder. Add any fallback.

---

## Phase 2 — the data boundary

**Build.** `src/qtrend/data/panel.py`: `Panel` and `PanelView` per `README.md` §9 and the
`slice_to` design. Arrays of shape `(n_hours, n_symbols)` for close, quote volume and a stale
mask. Index is **close time**, ascending. `slice_to(t)` uses `searchsorted` and returns a view —
never a copy, never an allocation.

`PanelView` exposes: `close(symbol)`, `quote_volume(symbol)`, `stale_mask(symbol)`,
`price_at_offset(symbol, hours_back)`, `last_prices()`, `n_rows`, `symbols`, `end_time`.

**Gate.** Three tests:

```python
def test_slice_is_a_view_not_a_copy():
    # assert slice_to() allocates no new price array

def test_view_cannot_see_future():
    # mutate panel rows at and after `end`; every PanelView accessor
    # returns identical values before and after

def test_close_time_indexing():
    # a bar covering 00:00-01:00 is indexed at its close, not its open
```

Plus: `grep` confirms no module outside `panel.py` touches `_close`, `_index`, `_quote_volume` or
`_stale`.

**Do not.** Add any accessor taking an absolute index or a timestamp beyond `end_time`.

---

## Phase 3 — synthetic data

**Build.** `src/qtrend/data/synthetic.py`: seeded generators producing the same `Panel` type the
real fetcher will produce, through the **same constructor** — including close-time indexing and
the stale mask. Generators for: pure random walks, one planted trend, a trend reversing on a known
date, constant prices, a volatility regime change on a known date, and a panel with a deliberate
gap.

**Gate.** Tests asserting each planted property is recovered, with every expected value derived
from the generator's own parameters rather than written as a literal. Most important: **the pure
random-walk panel must produce a strategy return approximately equal to negative the fees paid,
and nothing more.** Any positive return here is look-ahead or a sign error.

(This gate depends on Phases 5–7; write the generators and their data-level tests now, wire the
strategy-level assertions in at Phase 7.)

**Do not.** Hand-build a clean DataFrame that bypasses the `Panel` constructor.

---

## Phase 4 — the look-ahead checker

**Build.** `src/qtrend/backtest/lookahead.py` implementing `assert_no_lookahead` per
`README.md` §7.2. It must perturb prices, volumes and every other column strictly after `t`, and
run across a sample of timestamps.

**Build also:** a deliberately broken strategy function under `tests/` that reads one bar ahead.

**Gate.** The checker **catches the broken strategy**. A checker that has never caught anything is
not a checker. Then confirm it passes on a correct trivial strategy.

**Do not.** Weaken the perturbation to make a test pass.

---

## Phase 5 — strategy modules

**Build**, one module at a time, each with its own tests before moving on:
`universe.py`, `volatility.py`, `signal.py`, `selection.py`, `weights.py`, `exposure.py`,
`baselines.py` — per `README.md` §3.

Specific requirements to get right:

- Eligibility uses the **median** of the volume window, not the mean (§3.1).
- The volatility estimator skips stale bars and applies the configured floor (§3.2).
- The signal divides by own volatility and honours `skip_hours` (§3.3).
- Selection hysteresis: keep a holding while it is within `keep_rank`; never enter below
  `min_signal` (§3.4).
- `exposure.scale` estimates `rho` from the panel per `rho_estimation`; it is never assumed (§3.6).

**Gate.** Every module is pure — `grep` for `open(`, `requests`, `urllib`, `datetime.now`,
`random.` and `logging` under `strategy/` returns nothing. `assert_no_lookahead` passes on each
function. Unit tests use synthetic panels with known answers.

---

## Phase 6 — the order planner

**Build.** `src/qtrend/execution.py::plan_orders` per `README.md` §9.2. Applies the drift band,
rounds quantities down to step size, drops orders below minimum notional, orders sells before
buys, respects the cash buffer and `max_order_fraction`.

**Gate.** Tests covering: drift inside the band produces no order; rounding never produces a
quantity the exchange would reject; a full rotation's buys never exceed cash available after
sells; an order above `max_order_fraction` is rejected, not truncated silently.

**Do not.** Create a second planner for any reason.

---

## Phase 7 — the simulator

**Build.** `src/qtrend/backtest/simulator.py` implementing the event loop from `README.md` §9.3,
including the warm-up guard:

```python
if view.n_rows < config.data.warmup_bars:
    records.append(CycleRecord(t=t, halted_reason="warmup"))
    continue
```

Hourly loop; stops checked hourly; selection only at `selection.hour_utc`; fills at the **next**
bar; `CycleRecord` capturing everything needed to re-derive the decision.

**Gate.** Running the same config twice produces byte-identical equity curves. The Phase 3
random-walk assertion now passes: return ≈ −fees. The constant-price panel produces zero trades
after the first cycle. The volatility-regime panel halves exposure within hours of the change.

**Do not.** Fill at the decision bar.

---

## Phase 8 — scoring

**Build.** `costs.py` (fee plus configurable slippage), `metrics.py` (Sharpe, Sortino, Calmar,
max drawdown, composite), `windows.py` (rolling windows, bootstrap over non-overlapping windows),
`field.py` (simulated competitors per the study design).

Two traps to handle explicitly:

- **Calmar's denominator can approach zero** over a short window, producing an enormous meaningless
  score. Implement the guard from config and apply it identically to every variant and competitor.
- **Sortino needs a stated minimum acceptable return.** It comes from config.

Competitors in `field.py` use the **same interface** as the strategy —
`(PanelView, config) -> target_weights` — and pay the **same** fees and minimum notionals. The
field is generated by a rule (top-N by trailing volume at each window start), never a hardcoded
ticker list.

**Gate.** Metrics verified against hand-computed values on a tiny fixed series. A zero-drawdown
series does not produce infinity. A competitor and the strategy scored on an identical return
series produce identical scores.

---

## Phase 9 — real data and the first backtest

**Build.** `scripts/fetch_history.py` pulling bulk hourly klines into the parquet cache. Requires
**no credentials**. Must handle, per `README.md`:

- **Per-file timestamp unit inference from magnitude.** Spot data switched to microseconds from
  2025-01-01; earlier files are milliseconds. Never assume.
- **Per-file header sniffing.** Some CSVs have a header row, some do not.
- **`Quote asset volume` used directly** as dollar volume. Never recomputed as `close × volume`.
- **Close time snapped up to the grid hour.** Raw close times end at `...399999`, not a round hour.
- **SHA-256 verification** against the `.CHECKSUM` sidecar for every download.
- Concurrent downloads with a configurable worker pool; missing periods reported, not raised.
- A lead-in before `report_from` covering the longest signal horizon, the volume window and EWMA
  convergence, so no decision is scored on a half-warmed estimator.

Then `scripts/run_backtest.py` with a `--baselines` flag running all five baselines from
`README.md` §9.7 in the same harness.

**Gate.** `assert_no_lookahead` passes on the real panel. The fetched panel's bar count matches
the expected hour count for the window, gaps accounted for. The baseline table runs and BTC
buy-and-hold reproduces a sanity-checkable return for the period.

**Do not.** Tune anything after seeing these numbers.

---

## Phase 10 — exchange client and mock server

**Build.** `src/qtrend/roostoo/client.py`: HMAC-SHA256 signing over the exact bytes sent, server
clock sync, typed endpoints, token-bucket rate limiter, and the hard rule that `place_order`
**never retries** on an uncertain response.

`scripts/mock_exchange.py`: a local HTTP server implementing the documented API — signature
validation over the exact bytes, the timestamp window, documented error envelopes, and injectable
faults (timeouts, 429s, partial fills, malformed JSON, duplicate-order rejection).

An `Exchange` interface with `PaperExchange`, `MockRoostooExchange` and `RoostooExchange`
implementations, selected by config.

**Gate.** The full engine runs end to end against the mock server with **no credentials set**.
Each injected fault produces the correct halt or retry behaviour. A timeout on `place_order`
produces zero retries and a halted cycle.

**Do not.** Hardcode any fee, limit or minimum notional observed from the mock.

---

## Phase 11 — live engine

**Build.** `engine/loop.py` (reconcile → decide → plan → execute → journal), `engine/safety.py`
(all halt conditions from `README.md` §5.2), `journal.py` (append-only JSONL stamped with the git
commit), `scripts/replay.py` (re-derive every journalled decision and diff it),
`scripts/smoke_private.py`, and `--dry-run` support that plans and journals orders without sending
them.

`mode` in every committed config defaults to `hold` and is re-read every cycle.

**Gate.** Paper-trading loop runs a full cycle. `replay.py` diffs clean against its journal. Every
halt condition has a test that triggers it. The client fails loudly and exits on a missing
credential rather than falling back to paper.

**Do not.** Set `mode = "trade"` in any committed config.

---

## Credentials: the only manual step

Phases 0 through 10 need **no Roostoo credentials**. Never ask for them, never create a file
containing them, never write code that waits for them.

When the user has keys, the entire integration is:

1. They fill `ROOSTOO_API_KEY` and `ROOSTOO_SECRET_KEY` in `.env`.
2. `python scripts/smoke_private.py --config configs/live.toml`
3. `python scripts/run_bot.py --config configs/live.toml --once --dry-run`
4. They set `[meta] mode = "trade"` themselves.

Nothing else changes. If your design would require a code change at this point, the design is
wrong — fix it before Phase 11.

---

## Commands

```bash
pytest                                                      # offline, deterministic
pytest tests/test_lookahead.py -v                           # the checker
python scripts/check_secrets.py --all
python scripts/fetch_history.py  --config configs/dev.toml
python scripts/run_backtest.py   --config configs/dev.toml --baselines
python scripts/run_period_study.py --config configs/study.toml
python scripts/mock_exchange.py                             # terminal 1
python scripts/run_bot.py --config configs/paper.toml --once   # terminal 2
python scripts/replay.py --config configs/paper.toml
```

---

## When you are unsure

Ask. Specifically, always stop and ask rather than deciding, when:

- A config value is needed and no placeholder covers it.
- Something appears to require raw `Panel` access.
- `README.md` and this file conflict.
- A gate cannot be met without changing the gate.
- A rule above seems to prevent a reasonable implementation.

An honest "this gate fails and here is why" is a good outcome. A gate quietly weakened until it
passes is the failure this whole process exists to prevent.
