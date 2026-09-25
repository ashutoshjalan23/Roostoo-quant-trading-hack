"""Seeded synthetic panels with known, planted properties.

Every generator returns the same `Panel` type the real fetcher produces and builds it
through `Panel.from_bars`, so close-time indexing, the uniform-grid invariant and the stale
mask are exercised rather than bypassed (CLAUDE.md Phase 3: do not hand-build a clean frame
that skips the constructor).

No generator has a default argument. Every parameter is keyword-only and required, so a test
states the world it is asking for and a reader can check the planted property against the
call that planted it.

Nothing here reads a private panel attribute. `with_gaps` rebuilds a panel using only the
public `PanelView` accessors, which is a small proof that the boundary of Phase 2 is wide
enough to do real work through.

### The Ito trap, which matters for the Phase 7 gate

`random_walk` takes `drift_daily_log_return` and applies it to *log* prices. A walk with
zero log drift is **not** a fair game in price: log-normal shocks give

    E[P_t] = P_0 * exp(+0.5 * sigma^2 * t)

so an asset with 4% daily volatility drifts up about 0.08% a day in arithmetic terms, for
free. A strategy backtested on it would earn that, and the Phase 7 gate -- "a pure random
walk must return approximately minus the fees paid" -- would fail while nothing was wrong,
or worse, would be quietly relaxed until it passed.

To make price a martingale, pass `drift_daily_log_return=martingale_log_drift(daily_vol)`,
which is `-0.5 * daily_vol**2`. The choice is deliberately not made for you: both nulls are
legitimate and they answer different questions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta

import numpy as np

from qtrend.data.panel import Panel

SECONDS_PER_DAY = 86_400


def bars_per_day(interval: timedelta) -> float:
    """Bars in a day at this interval. Not required to be an integer here."""
    if interval <= timedelta(0):
        raise ValueError("interval must be positive")
    return SECONDS_PER_DAY / interval.total_seconds()


def martingale_log_drift(daily_vol: float) -> float:
    """The log drift that makes price a martingale: `-0.5 * sigma^2`.

    Use this when the question is "does the strategy find an edge where none exists".
    """
    return -0.5 * daily_vol**2


def _validate_grid(*, symbols: Sequence[str], n_bars: int) -> tuple[str, ...]:
    symbol_tuple = tuple(symbols)
    if n_bars < 1:
        raise ValueError("n_bars must be at least 1")
    if len(symbol_tuple) == 0:
        raise ValueError("need at least one symbol")
    if len(set(symbol_tuple)) != len(symbol_tuple):
        raise ValueError("symbols must be unique")
    return symbol_tuple


def _volume_array(
    *,
    rng: np.random.Generator,
    symbols: tuple[str, ...],
    n_bars: int,
    quote_volume_per_bar: Mapping[str, float],
    volume_jitter: float,
) -> np.ndarray:
    """Quote-asset volume per bar, optionally jittered multiplicatively.

    The jitter is median-preserving: the multiplier is `exp(N(0, j^2))`, whose median is 1,
    so the trailing *median* dollar volume of README section 3.1 stays at the level the
    caller asked for. Note this is not mean-preserving -- the mean is `exp(j^2/2)` -- which
    is the right way round, because the eligibility rule screens on the median.
    """
    if volume_jitter < 0:
        raise ValueError("volume_jitter must not be negative")
    missing = [symbol for symbol in symbols if symbol not in quote_volume_per_bar]
    if missing:
        raise KeyError(f"quote_volume_per_bar is missing: {', '.join(sorted(missing))}")

    base = np.array([float(quote_volume_per_bar[symbol]) for symbol in symbols])
    if volume_jitter == 0:
        return np.tile(base, (n_bars, 1))
    shocks = rng.normal(0.0, volume_jitter, size=(n_bars, len(symbols)))
    return base * np.exp(shocks)


def _build(
    *,
    symbols: tuple[str, ...],
    start: datetime,
    interval: timedelta,
    close: np.ndarray,
    quote_volume: np.ndarray,
    stale: np.ndarray,
) -> Panel:
    """The one construction path. `start` is the OPEN time of the first bar."""
    n_bars = close.shape[0]
    open_times = [start + i * interval for i in range(n_bars)]
    return Panel.from_bars(
        open_times=open_times,
        interval=interval,
        close=close,
        quote_volume=quote_volume,
        stale=stale,
        symbols=symbols,
    )


def _walk(
    *,
    rng: np.random.Generator,
    n_bars: int,
    n_symbols: int,
    initial_price: float,
    drift_per_bar: np.ndarray,
    vol_per_bar: np.ndarray,
) -> np.ndarray:
    """Log-price paths. Bar 0 is exactly `initial_price`; shocks start at bar 1.

    `drift_per_bar` and `vol_per_bar` are `(n_bars, n_symbols)`, so a regime change is just
    a change of row in those arrays.
    """
    if initial_price <= 0:
        raise ValueError("initial_price must be positive")
    shocks = rng.standard_normal(size=(n_bars, n_symbols))
    steps = drift_per_bar + vol_per_bar * shocks
    steps[0] = 0.0
    log_prices = np.log(initial_price) + np.cumsum(steps, axis=0)
    return np.exp(log_prices)


def random_walk(
    *,
    symbols: Sequence[str],
    n_bars: int,
    start: datetime,
    interval: timedelta,
    seed: int,
    initial_price: float,
    daily_vol: float,
    drift_daily_log_return: float,
    quote_volume_per_bar: Mapping[str, float],
    volume_jitter: float,
) -> Panel:
    """Independent random walks, one per symbol, with no cross-correlation.

    Read the module docstring on `drift_daily_log_return` before using this as a null.
    """
    symbol_tuple = _validate_grid(symbols=symbols, n_bars=n_bars)
    if daily_vol < 0:
        raise ValueError("daily_vol must not be negative")
    rng = np.random.default_rng(seed)
    per_day = bars_per_day(interval)
    n_symbols = len(symbol_tuple)

    drift = np.full((n_bars, n_symbols), drift_daily_log_return / per_day)
    vol = np.full((n_bars, n_symbols), daily_vol / np.sqrt(per_day))

    close = _walk(
        rng=rng,
        n_bars=n_bars,
        n_symbols=n_symbols,
        initial_price=initial_price,
        drift_per_bar=drift,
        vol_per_bar=vol,
    )
    volume = _volume_array(
        rng=rng,
        symbols=symbol_tuple,
        n_bars=n_bars,
        quote_volume_per_bar=quote_volume_per_bar,
        volume_jitter=volume_jitter,
    )
    return _build(
        symbols=symbol_tuple,
        start=start,
        interval=interval,
        close=close,
        quote_volume=volume,
        stale=np.zeros((n_bars, n_symbols), dtype=bool),
    )


def planted_trend(
    *,
    symbols: Sequence[str],
    trending_symbol: str,
    n_bars: int,
    start: datetime,
    interval: timedelta,
    seed: int,
    initial_price: float,
    daily_vol: float,
    trend_daily_log_return: float,
    background_daily_log_return: float,
    quote_volume_per_bar: Mapping[str, float],
    volume_jitter: float,
) -> Panel:
    """One symbol carries a planted drift; the rest carry `background_daily_log_return`.

    With `daily_vol=0` the planted drift is recovered exactly, which is what makes the
    property testable without a tolerance.
    """
    symbol_tuple = _validate_grid(symbols=symbols, n_bars=n_bars)
    if trending_symbol not in symbol_tuple:
        raise KeyError(f"{trending_symbol!r} is not among the symbols")
    rng = np.random.default_rng(seed)
    per_day = bars_per_day(interval)
    n_symbols = len(symbol_tuple)

    daily = np.full(n_symbols, background_daily_log_return)
    daily[symbol_tuple.index(trending_symbol)] = trend_daily_log_return
    drift = np.tile(daily / per_day, (n_bars, 1))
    vol = np.full((n_bars, n_symbols), daily_vol / np.sqrt(per_day))

    close = _walk(
        rng=rng,
        n_bars=n_bars,
        n_symbols=n_symbols,
        initial_price=initial_price,
        drift_per_bar=drift,
        vol_per_bar=vol,
    )
    volume = _volume_array(
        rng=rng,
        symbols=symbol_tuple,
        n_bars=n_bars,
        quote_volume_per_bar=quote_volume_per_bar,
        volume_jitter=volume_jitter,
    )
    return _build(
        symbols=symbol_tuple,
        start=start,
        interval=interval,
        close=close,
        quote_volume=volume,
        stale=np.zeros((n_bars, n_symbols), dtype=bool),
    )


def reversing_trend(
    *,
    symbols: Sequence[str],
    trending_symbol: str,
    n_bars: int,
    start: datetime,
    interval: timedelta,
    seed: int,
    initial_price: float,
    daily_vol: float,
    daily_log_return_before: float,
    daily_log_return_after: float,
    reversal_time: datetime,
    quote_volume_per_bar: Mapping[str, float],
    volume_jitter: float,
) -> Panel:
    """A trend that changes sign at a known bar close.

    `reversal_time` is a bar CLOSE time. Bars closing at or before it carry the "before"
    drift; bars closing after it carry the "after" drift.
    """
    symbol_tuple = _validate_grid(symbols=symbols, n_bars=n_bars)
    if trending_symbol not in symbol_tuple:
        raise KeyError(f"{trending_symbol!r} is not among the symbols")
    rng = np.random.default_rng(seed)
    per_day = bars_per_day(interval)
    n_symbols = len(symbol_tuple)
    column = symbol_tuple.index(trending_symbol)

    after = _bars_after(
        start=start, interval=interval, n_bars=n_bars, moment=reversal_time
    )
    drift = np.zeros((n_bars, n_symbols))
    drift[:after, column] = daily_log_return_before / per_day
    drift[after:, column] = daily_log_return_after / per_day
    vol = np.full((n_bars, n_symbols), daily_vol / np.sqrt(per_day))

    close = _walk(
        rng=rng,
        n_bars=n_bars,
        n_symbols=n_symbols,
        initial_price=initial_price,
        drift_per_bar=drift,
        vol_per_bar=vol,
    )
    volume = _volume_array(
        rng=rng,
        symbols=symbol_tuple,
        n_bars=n_bars,
        quote_volume_per_bar=quote_volume_per_bar,
        volume_jitter=volume_jitter,
    )
    return _build(
        symbols=symbol_tuple,
        start=start,
        interval=interval,
        close=close,
        quote_volume=volume,
        stale=np.zeros((n_bars, n_symbols), dtype=bool),
    )


def volatility_regime(
    *,
    symbols: Sequence[str],
    n_bars: int,
    start: datetime,
    interval: timedelta,
    seed: int,
    initial_price: float,
    daily_vol_before: float,
    daily_vol_after: float,
    change_time: datetime,
    drift_daily_log_return: float,
    quote_volume_per_bar: Mapping[str, float],
    volume_jitter: float,
) -> Panel:
    """Volatility steps from one level to another at a known bar close.

    `change_time` is a bar CLOSE time: bars closing at or before it use the "before" level.
    """
    symbol_tuple = _validate_grid(symbols=symbols, n_bars=n_bars)
    if daily_vol_before < 0 or daily_vol_after < 0:
        raise ValueError("volatilities must not be negative")
    rng = np.random.default_rng(seed)
    per_day = bars_per_day(interval)
    n_symbols = len(symbol_tuple)

    after = _bars_after(start=start, interval=interval, n_bars=n_bars, moment=change_time)
    vol = np.empty((n_bars, n_symbols))
    vol[:after] = daily_vol_before / np.sqrt(per_day)
    vol[after:] = daily_vol_after / np.sqrt(per_day)
    drift = np.full((n_bars, n_symbols), drift_daily_log_return / per_day)

    close = _walk(
        rng=rng,
        n_bars=n_bars,
        n_symbols=n_symbols,
        initial_price=initial_price,
        drift_per_bar=drift,
        vol_per_bar=vol,
    )
    volume = _volume_array(
        rng=rng,
        symbols=symbol_tuple,
        n_bars=n_bars,
        quote_volume_per_bar=quote_volume_per_bar,
        volume_jitter=volume_jitter,
    )
    return _build(
        symbols=symbol_tuple,
        start=start,
        interval=interval,
        close=close,
        quote_volume=volume,
        stale=np.zeros((n_bars, n_symbols), dtype=bool),
    )


def constant_prices(
    *,
    symbols: Sequence[str],
    n_bars: int,
    start: datetime,
    interval: timedelta,
    price: float,
    quote_volume_per_bar: Mapping[str, float],
) -> Panel:
    """Every close identical. Zero returns, zero volatility, nothing stale.

    Not a gap: these bars traded, they just did not move. The stale mask stays False so the
    volatility estimator sees genuine zero returns rather than skipping them.
    """
    symbol_tuple = _validate_grid(symbols=symbols, n_bars=n_bars)
    if price <= 0:
        raise ValueError("price must be positive")
    n_symbols = len(symbol_tuple)
    volume = _volume_array(
        rng=np.random.default_rng(0),
        symbols=symbol_tuple,
        n_bars=n_bars,
        quote_volume_per_bar=quote_volume_per_bar,
        volume_jitter=0.0,
    )
    return _build(
        symbols=symbol_tuple,
        start=start,
        interval=interval,
        close=np.full((n_bars, n_symbols), float(price)),
        quote_volume=volume,
        stale=np.zeros((n_bars, n_symbols), dtype=bool),
    )


def with_gaps(panel: Panel, *, gaps: Mapping[str, tuple[int, int]]) -> Panel:
    """Return a copy of `panel` with forward-filled, stale-flagged gaps.

    `gaps` maps a symbol to `(first_bar, n_bars)`. Within a gap the last observed close is
    carried forward, quote volume is zero because nothing traded, and the stale mask is
    True -- README section 7.1 rule 3: forward-fill is flagged, never silent.

    The index keeps every row. A gap is missing *source data*, not a missing bar; dropping
    rows would break the uniform-grid invariant and change what an offset means.

    Rebuilt entirely through the public PanelView accessors: this module never reads a
    private panel array.
    """
    view = panel.slice_to(panel.end_time)
    symbols = view.symbols
    n_bars = view.n_rows

    close = np.column_stack([np.array(view.close(symbol), dtype=np.float64) for symbol in symbols])
    volume = np.column_stack(
        [np.array(view.quote_volume(symbol), dtype=np.float64) for symbol in symbols]
    )
    stale = np.column_stack([np.array(view.stale_mask(symbol), dtype=bool) for symbol in symbols])

    for symbol, (first_bar, length) in sorted(gaps.items()):
        if symbol not in symbols:
            raise KeyError(f"{symbol!r} is not in this panel")
        if length < 1:
            raise ValueError(f"gap for {symbol!r} must cover at least one bar")
        if first_bar < 1:
            raise ValueError(
                f"gap for {symbol!r} starts at bar {first_bar}; there is no earlier close "
                "to carry forward"
            )
        if first_bar + length > n_bars:
            raise ValueError(
                f"gap for {symbol!r} runs to bar {first_bar + length}, past the panel's "
                f"{n_bars} bars"
            )
        column = symbols.index(symbol)
        close[first_bar : first_bar + length, column] = close[first_bar - 1, column]
        volume[first_bar : first_bar + length, column] = 0.0
        stale[first_bar : first_bar + length, column] = True

    return _build(
        symbols=symbols,
        start=panel.start_time - panel.step,
        interval=panel.step,
        close=close,
        quote_volume=volume,
        stale=stale,
    )


def _utc(value: datetime) -> datetime:
    """Naive datetimes are UTC, matching the convention in panel.py."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _bars_after(
    *, start: datetime, interval: timedelta, n_bars: int, moment: datetime
) -> int:
    """Index of the first bar whose CLOSE time is strictly after `moment`.

    `start` is the open time of bar 0, so bar `i` closes at `start + (i + 1) * interval`.
    Computed in seconds relative to `start`, so no epoch or local-time conversion is
    involved and a naive datetime cannot silently mean local noon.
    """
    offset = (_utc(moment) - _utc(start)).total_seconds()
    step = interval.total_seconds()
    closes = np.arange(1, n_bars + 1, dtype=np.float64) * step
    return int(np.searchsorted(closes, offset, side="right"))
