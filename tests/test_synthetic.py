"""Tests that each planted property is recovered from the generated panel.

CLAUDE.md Phase 3: "every expected value derived from the generator's own parameters rather
than written as a literal". So no test here compares against a hand-computed price. Each
test names its parameters as variables, passes them to the generator, and recomputes the
expectation from those same variables.

Two kinds of assertion:

* **Exact**, where `daily_vol=0` makes the path deterministic. The planted drift comes back
  to floating-point precision and no tolerance is needed. These are the real tests.
* **Statistical**, where volatility is on. The tolerance is derived from the generator's
  parameters -- the standard error of the estimator -- never chosen to make a test pass.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from qtrend.data.panel import Panel
from qtrend.data.synthetic import (
    bars_per_day,
    constant_prices,
    martingale_log_drift,
    planted_trend,
    random_walk,
    reversing_trend,
    volatility_regime,
    with_gaps,
)

HOUR = timedelta(hours=1)
START = datetime(2024, 1, 1, tzinfo=UTC)
SYMBOLS = ("AAA", "BBB", "CCC")
VOLUME = dict.fromkeys(SYMBOLS, 5_000_000.0)

# Number of standard errors a statistical assertion is allowed. Four is a tolerance, not a
# tuned value: the generators are seeded, so each test either passes or does not.
SIGMAS = 4.0


def full_view(panel: Panel):
    return panel.slice_to(panel.end_time)


def log_returns(panel: Panel, symbol: str) -> np.ndarray:
    prices = np.asarray(full_view(panel).close(symbol), dtype=np.float64)
    return np.diff(np.log(prices))


def realised_daily_log_drift(panel: Panel, symbol: str, interval: timedelta) -> float:
    """Mean log return per day, recomputed from the panel."""
    return float(np.mean(log_returns(panel, symbol))) * bars_per_day(interval)


def realised_daily_vol(panel: Panel, symbol: str, interval: timedelta) -> float:
    returns = log_returns(panel, symbol)
    return float(np.std(returns, ddof=1)) * math.sqrt(bars_per_day(interval))


# --------------------------------------------------------------------------------------
# Every generator goes through the Panel constructor
# --------------------------------------------------------------------------------------


def all_generators() -> dict[str, Panel]:
    """One panel from each generator, with parameters spelled out at the call site."""
    return {
        "random_walk": random_walk(
            symbols=SYMBOLS, n_bars=240, start=START, interval=HOUR, seed=1,
            initial_price=100.0, daily_vol=0.04, drift_daily_log_return=0.0,
            quote_volume_per_bar=VOLUME, volume_jitter=0.0,
        ),
        "planted_trend": planted_trend(
            symbols=SYMBOLS, trending_symbol="BBB", n_bars=240, start=START, interval=HOUR,
            seed=1, initial_price=100.0, daily_vol=0.0, trend_daily_log_return=0.01,
            background_daily_log_return=0.0, quote_volume_per_bar=VOLUME, volume_jitter=0.0,
        ),
        "reversing_trend": reversing_trend(
            symbols=SYMBOLS, trending_symbol="AAA", n_bars=240, start=START, interval=HOUR,
            seed=1, initial_price=100.0, daily_vol=0.0, daily_log_return_before=0.02,
            daily_log_return_after=-0.02, reversal_time=START + 120 * HOUR,
            quote_volume_per_bar=VOLUME, volume_jitter=0.0,
        ),
        "volatility_regime": volatility_regime(
            symbols=SYMBOLS, n_bars=240, start=START, interval=HOUR, seed=1,
            initial_price=100.0, daily_vol_before=0.01, daily_vol_after=0.08,
            change_time=START + 120 * HOUR, drift_daily_log_return=0.0,
            quote_volume_per_bar=VOLUME, volume_jitter=0.0,
        ),
        "constant_prices": constant_prices(
            symbols=SYMBOLS, n_bars=240, start=START, interval=HOUR, price=100.0,
            quote_volume_per_bar=VOLUME,
        ),
    }


def test_every_generator_returns_a_panel():
    for name, panel in all_generators().items():
        assert isinstance(panel, Panel), name


def test_every_generator_uses_close_time_indexing():
    """Bar 0 opens at START and is indexed at START + interval, never at START."""
    for name, panel in all_generators().items():
        assert panel.start_time == START + HOUR, name
        assert panel.slice_to(START).n_rows == 0, f"{name} is visible before it closed"
        assert panel.slice_to(START + HOUR).n_rows == 1, name


def test_every_generator_produces_a_stale_mask():
    for name, panel in all_generators().items():
        view = full_view(panel)
        for symbol in SYMBOLS:
            mask = view.stale_mask(symbol)
            assert mask.dtype == np.bool_, name
            assert mask.shape == (panel.n_rows,), name


def test_every_generator_is_on_a_uniform_hourly_grid():
    for name, panel in all_generators().items():
        assert panel.step == HOUR, name
        assert panel.n_rows == 240, name


def test_generators_are_seeded_and_deterministic():
    def build(seed: int) -> Panel:
        return random_walk(
            symbols=SYMBOLS, n_bars=120, start=START, interval=HOUR, seed=seed,
            initial_price=100.0, daily_vol=0.04, drift_daily_log_return=0.0,
            quote_volume_per_bar=VOLUME, volume_jitter=0.2,
        )

    first, again, other = build(99), build(99), build(100)
    for symbol in SYMBOLS:
        assert np.array_equal(full_view(first).close(symbol), full_view(again).close(symbol))
        assert np.array_equal(
            full_view(first).quote_volume(symbol), full_view(again).quote_volume(symbol)
        )
        assert not np.array_equal(full_view(first).close(symbol), full_view(other).close(symbol))


# --------------------------------------------------------------------------------------
# Planted trend
# --------------------------------------------------------------------------------------


def test_planted_trend_is_recovered_exactly_without_noise():
    trend = 0.013
    background = -0.004
    panel = planted_trend(
        symbols=SYMBOLS, trending_symbol="BBB", n_bars=480, start=START, interval=HOUR,
        seed=3, initial_price=100.0, daily_vol=0.0, trend_daily_log_return=trend,
        background_daily_log_return=background, quote_volume_per_bar=VOLUME,
        volume_jitter=0.0,
    )
    assert realised_daily_log_drift(panel, "BBB", HOUR) == pytest.approx(trend)
    for other in ("AAA", "CCC"):
        assert realised_daily_log_drift(panel, other, HOUR) == pytest.approx(background)


def test_planted_trend_survives_noise_within_its_own_standard_error():
    trend = 0.02
    daily_vol = 0.03
    n_bars = 24 * 180
    n_days = n_bars / bars_per_day(HOUR)
    panel = planted_trend(
        symbols=SYMBOLS, trending_symbol="BBB", n_bars=n_bars, start=START, interval=HOUR,
        seed=4, initial_price=100.0, daily_vol=daily_vol, trend_daily_log_return=trend,
        background_daily_log_return=0.0, quote_volume_per_bar=VOLUME, volume_jitter=0.0,
    )
    standard_error = daily_vol / math.sqrt(n_days)
    assert abs(realised_daily_log_drift(panel, "BBB", HOUR) - trend) < SIGMAS * standard_error


def test_planted_trend_makes_the_trending_symbol_the_top_performer():
    panel = planted_trend(
        symbols=SYMBOLS, trending_symbol="CCC", n_bars=480, start=START, interval=HOUR,
        seed=5, initial_price=100.0, daily_vol=0.0, trend_daily_log_return=0.01,
        background_daily_log_return=0.0, quote_volume_per_bar=VOLUME, volume_jitter=0.0,
    )
    finals = full_view(panel).last_prices()
    assert max(finals, key=lambda symbol: finals[symbol]) == "CCC"


def test_unknown_trending_symbol_is_rejected():
    with pytest.raises(KeyError, match="not among the symbols"):
        planted_trend(
            symbols=SYMBOLS, trending_symbol="ZZZ", n_bars=10, start=START, interval=HOUR,
            seed=1, initial_price=100.0, daily_vol=0.0, trend_daily_log_return=0.01,
            background_daily_log_return=0.0, quote_volume_per_bar=VOLUME, volume_jitter=0.0,
        )


# --------------------------------------------------------------------------------------
# Reversal on a known date
# --------------------------------------------------------------------------------------


def test_trend_reverses_at_the_planted_time():
    before, after = 0.02, -0.03
    bars_before = 120
    reversal = START + bars_before * HOUR
    panel = reversing_trend(
        symbols=SYMBOLS, trending_symbol="AAA", n_bars=300, start=START, interval=HOUR,
        seed=6, initial_price=100.0, daily_vol=0.0, daily_log_return_before=before,
        daily_log_return_after=after, reversal_time=reversal, quote_volume_per_bar=VOLUME,
        volume_jitter=0.0,
    )
    returns = log_returns(panel, "AAA")
    per_day = bars_per_day(HOUR)

    # Bar i closes at START + (i+1)h, so the bars closing at or before the reversal are
    # indices 0 .. bars_before-1, and return i is the step from bar i-1 to bar i.
    early = returns[: bars_before - 1] * per_day
    late = returns[bars_before:] * per_day
    assert np.allclose(early, before)
    assert np.allclose(late, after)


def test_the_peak_sits_at_the_reversal():
    bars_before = 120
    reversal = START + bars_before * HOUR
    panel = reversing_trend(
        symbols=SYMBOLS, trending_symbol="AAA", n_bars=300, start=START, interval=HOUR,
        seed=7, initial_price=100.0, daily_vol=0.0, daily_log_return_before=0.02,
        daily_log_return_after=-0.02, reversal_time=reversal, quote_volume_per_bar=VOLUME,
        volume_jitter=0.0,
    )
    prices = np.asarray(full_view(panel).close("AAA"), dtype=np.float64)
    peak_bar = int(np.argmax(prices))
    assert panel.slice_to(reversal).n_rows - 1 == peak_bar


def test_non_trending_symbols_are_flat_through_the_reversal():
    panel = reversing_trend(
        symbols=SYMBOLS, trending_symbol="AAA", n_bars=200, start=START, interval=HOUR,
        seed=8, initial_price=100.0, daily_vol=0.0, daily_log_return_before=0.02,
        daily_log_return_after=-0.02, reversal_time=START + 100 * HOUR,
        quote_volume_per_bar=VOLUME, volume_jitter=0.0,
    )
    for symbol in ("BBB", "CCC"):
        assert np.allclose(log_returns(panel, symbol), 0.0)


# --------------------------------------------------------------------------------------
# Volatility regime change on a known date
# --------------------------------------------------------------------------------------


def test_volatility_regime_change_is_recovered():
    before, after = 0.01, 0.08
    bars_before = 24 * 60
    change = START + bars_before * HOUR
    panel = volatility_regime(
        symbols=SYMBOLS, n_bars=bars_before * 2, start=START, interval=HOUR, seed=9,
        initial_price=100.0, daily_vol_before=before, daily_vol_after=after,
        change_time=change, drift_daily_log_return=0.0, quote_volume_per_bar=VOLUME,
        volume_jitter=0.0,
    )
    returns = log_returns(panel, "AAA")
    per_day = bars_per_day(HOUR)
    early = np.std(returns[: bars_before - 1], ddof=1) * math.sqrt(per_day)
    late = np.std(returns[bars_before:], ddof=1) * math.sqrt(per_day)

    # Standard error of a sample standard deviation is sigma / sqrt(2n).
    assert abs(early - before) < SIGMAS * before / math.sqrt(2 * (bars_before - 1))
    assert abs(late - after) < SIGMAS * after / math.sqrt(2 * bars_before)
    assert late > early


def test_volatility_ratio_matches_the_planted_ratio():
    before, after = 0.02, 0.06
    bars_before = 24 * 90
    panel = volatility_regime(
        symbols=SYMBOLS, n_bars=bars_before * 2, start=START, interval=HOUR, seed=10,
        initial_price=100.0, daily_vol_before=before, daily_vol_after=after,
        change_time=START + bars_before * HOUR, drift_daily_log_return=0.0,
        quote_volume_per_bar=VOLUME, volume_jitter=0.0,
    )
    returns = log_returns(panel, "AAA")
    observed = np.std(returns[bars_before:], ddof=1) / np.std(returns[: bars_before - 1], ddof=1)
    planted = after / before
    assert abs(observed - planted) < SIGMAS * planted / math.sqrt(bars_before)


# --------------------------------------------------------------------------------------
# Constant prices
# --------------------------------------------------------------------------------------


def test_constant_prices_have_zero_returns_and_zero_volatility():
    price = 137.5
    panel = constant_prices(
        symbols=SYMBOLS, n_bars=240, start=START, interval=HOUR, price=price,
        quote_volume_per_bar=VOLUME,
    )
    view = full_view(panel)
    for symbol in SYMBOLS:
        assert np.all(np.asarray(view.close(symbol)) == price)
        assert np.all(log_returns(panel, symbol) == 0.0)
        assert realised_daily_vol(panel, symbol, HOUR) == 0.0


def test_constant_prices_are_not_marked_stale():
    """A flat price that traded is not a carried-forward price."""
    panel = constant_prices(
        symbols=SYMBOLS, n_bars=48, start=START, interval=HOUR, price=100.0,
        quote_volume_per_bar=VOLUME,
    )
    view = full_view(panel)
    for symbol in SYMBOLS:
        assert not np.any(view.stale_mask(symbol))
        assert np.all(np.asarray(view.quote_volume(symbol)) > 0)


# --------------------------------------------------------------------------------------
# The deliberate gap
# --------------------------------------------------------------------------------------


def test_gap_is_forward_filled_and_flagged():
    first_bar, length = 50, 12
    base = random_walk(
        symbols=SYMBOLS, n_bars=240, start=START, interval=HOUR, seed=11,
        initial_price=100.0, daily_vol=0.04, drift_daily_log_return=0.0,
        quote_volume_per_bar=VOLUME, volume_jitter=0.0,
    )
    gapped = with_gaps(base, gaps={"BBB": (first_bar, length)})

    original = np.asarray(full_view(base).close("BBB"), dtype=np.float64)
    filled = np.asarray(full_view(gapped).close("BBB"), dtype=np.float64)
    mask = np.asarray(full_view(gapped).stale_mask("BBB"), dtype=bool)
    volume = np.asarray(full_view(gapped).quote_volume("BBB"), dtype=np.float64)

    gap = slice(first_bar, first_bar + length)
    assert np.all(filled[gap] == original[first_bar - 1]), "gap is not the carried close"
    assert np.all(mask[gap]), "gap is not flagged stale"
    assert np.all(volume[gap] == 0.0), "nothing traded, so volume is zero"


def test_gap_leaves_every_other_bar_and_symbol_untouched():
    first_bar, length = 50, 12
    base = random_walk(
        symbols=SYMBOLS, n_bars=240, start=START, interval=HOUR, seed=12,
        initial_price=100.0, daily_vol=0.04, drift_daily_log_return=0.0,
        quote_volume_per_bar=VOLUME, volume_jitter=0.0,
    )
    gapped = with_gaps(base, gaps={"BBB": (first_bar, length)})

    for symbol in ("AAA", "CCC"):
        assert np.array_equal(full_view(base).close(symbol), full_view(gapped).close(symbol))
        assert not np.any(full_view(gapped).stale_mask(symbol))

    original = np.asarray(full_view(base).close("BBB"), dtype=np.float64)
    filled = np.asarray(full_view(gapped).close("BBB"), dtype=np.float64)
    outside = np.ones(len(original), dtype=bool)
    outside[first_bar : first_bar + length] = False
    assert np.array_equal(original[outside], filled[outside])


def test_gap_keeps_the_index_complete():
    """A gap is missing source data, not a missing bar. Dropping rows would break the grid."""
    base = random_walk(
        symbols=SYMBOLS, n_bars=100, start=START, interval=HOUR, seed=13,
        initial_price=100.0, daily_vol=0.04, drift_daily_log_return=0.0,
        quote_volume_per_bar=VOLUME, volume_jitter=0.0,
    )
    gapped = with_gaps(base, gaps={"AAA": (10, 20)})
    assert gapped.n_rows == base.n_rows
    assert gapped.start_time == base.start_time
    assert gapped.end_time == base.end_time
    assert gapped.step == base.step


def test_a_gap_produces_zero_returns_that_the_mask_can_exclude():
    """The reason the mask exists: a carried price looks calm to a volatility estimator."""
    first_bar, length = 30, 24
    base = random_walk(
        symbols=SYMBOLS, n_bars=240, start=START, interval=HOUR, seed=14,
        initial_price=100.0, daily_vol=0.05, drift_daily_log_return=0.0,
        quote_volume_per_bar=VOLUME, volume_jitter=0.0,
    )
    gapped = with_gaps(base, gaps={"AAA": (first_bar, length)})
    view = full_view(gapped)
    returns = log_returns(gapped, "AAA")
    mask = np.asarray(view.stale_mask("AAA"), dtype=bool)

    # Return i is the step into bar i+1, so a stale bar i+1 gives a zero return at i.
    inside = mask[1:]
    assert np.count_nonzero(inside) == length
    assert np.all(returns[inside][1:] == 0.0), "carried-forward bars are not flat"
    assert np.std(returns[inside], ddof=1) < np.std(returns[~inside], ddof=1)


@pytest.mark.parametrize(
    ("gaps", "message"),
    [
        ({"ZZZ": (10, 5)}, "not in this panel"),
        ({"AAA": (0, 5)}, "no earlier close to carry forward"),
        ({"AAA": (10, 0)}, "at least one bar"),
        ({"AAA": (95, 20)}, "past the panel"),
    ],
)
def test_invalid_gaps_are_rejected(gaps, message):
    base = constant_prices(
        symbols=SYMBOLS, n_bars=100, start=START, interval=HOUR, price=100.0,
        quote_volume_per_bar=VOLUME,
    )
    with pytest.raises((KeyError, ValueError), match=message):
        with_gaps(base, gaps=gaps)


# --------------------------------------------------------------------------------------
# The random walk, and the Ito trap
# --------------------------------------------------------------------------------------


def test_first_bar_is_exactly_the_initial_price():
    """Bar 0 carries no shock, so every path starts where the caller said it does."""
    initial = 137.0
    panels = (
        random_walk(
            symbols=SYMBOLS, n_bars=240, start=START, interval=HOUR, seed=20,
            initial_price=initial, daily_vol=0.05, drift_daily_log_return=0.0,
            quote_volume_per_bar=VOLUME, volume_jitter=0.0,
        ),
        planted_trend(
            symbols=SYMBOLS, trending_symbol="BBB", n_bars=240, start=START, interval=HOUR,
            seed=20, initial_price=initial, daily_vol=0.05, trend_daily_log_return=0.01,
            background_daily_log_return=0.0, quote_volume_per_bar=VOLUME, volume_jitter=0.0,
        ),
        reversing_trend(
            symbols=SYMBOLS, trending_symbol="AAA", n_bars=240, start=START, interval=HOUR,
            seed=20, initial_price=initial, daily_vol=0.05, daily_log_return_before=0.02,
            daily_log_return_after=-0.02, reversal_time=START + 120 * HOUR,
            quote_volume_per_bar=VOLUME, volume_jitter=0.0,
        ),
        volatility_regime(
            symbols=SYMBOLS, n_bars=240, start=START, interval=HOUR, seed=20,
            initial_price=initial, daily_vol_before=0.02, daily_vol_after=0.09,
            change_time=START + 120 * HOUR, drift_daily_log_return=0.0,
            quote_volume_per_bar=VOLUME, volume_jitter=0.0,
        ),
    )
    for panel in panels:
        view = full_view(panel)
        for symbol in SYMBOLS:
            assert float(view.close(symbol)[0]) == pytest.approx(initial)


def test_random_walk_drift_is_recovered_exactly_without_noise():
    """Deterministic check of the drift parameter, including its sign.

    The statistical tests below cannot resolve a sign error of order sigma^2 per day, so
    the drift gets a noiseless test of its own.
    """
    for drift in (0.0075, -0.0075):
        panel = random_walk(
            symbols=SYMBOLS, n_bars=24 * 90, start=START, interval=HOUR, seed=21,
            initial_price=100.0, daily_vol=0.0, drift_daily_log_return=drift,
            quote_volume_per_bar=VOLUME, volume_jitter=0.0,
        )
        for symbol in SYMBOLS:
            assert realised_daily_log_drift(panel, symbol, HOUR) == pytest.approx(drift)


def test_volatility_regime_drift_is_recovered_exactly_without_noise():
    drift = -0.004
    panel = volatility_regime(
        symbols=SYMBOLS, n_bars=24 * 90, start=START, interval=HOUR, seed=22,
        initial_price=100.0, daily_vol_before=0.0, daily_vol_after=0.0,
        change_time=START + 24 * 45 * HOUR, drift_daily_log_return=drift,
        quote_volume_per_bar=VOLUME, volume_jitter=0.0,
    )
    assert realised_daily_log_drift(panel, "AAA", HOUR) == pytest.approx(drift)


def test_random_walk_has_the_planted_volatility():
    daily_vol = 0.05
    n_bars = 24 * 365
    panel = random_walk(
        symbols=SYMBOLS, n_bars=n_bars, start=START, interval=HOUR, seed=15,
        initial_price=100.0, daily_vol=daily_vol, drift_daily_log_return=0.0,
        quote_volume_per_bar=VOLUME, volume_jitter=0.0,
    )
    observed = realised_daily_vol(panel, "AAA", HOUR)
    standard_error = daily_vol / math.sqrt(2 * (n_bars - 1))
    assert abs(observed - daily_vol) < SIGMAS * standard_error


def test_zero_log_drift_still_drifts_up_in_price():
    """The trap the Phase 7 gate would otherwise trip over.

    With zero LOG drift the price is not a fair game: E[P_t] = P_0 exp(+sigma^2 t / 2).
    """
    daily_vol = 0.06
    n_bars = 24 * 365
    panel = random_walk(
        symbols=SYMBOLS, n_bars=n_bars, start=START, interval=HOUR, seed=16,
        initial_price=100.0, daily_vol=daily_vol, drift_daily_log_return=0.0,
        quote_volume_per_bar=VOLUME, volume_jitter=0.0,
    )
    returns = np.exp(log_returns(panel, "AAA")) - 1.0
    per_day = bars_per_day(HOUR)
    observed = float(np.mean(returns)) * per_day
    expected = 0.5 * daily_vol**2

    standard_error = daily_vol / math.sqrt(n_bars / per_day)
    assert abs(observed - expected) < SIGMAS * standard_error
    assert observed > 0, "zero log drift gives a positive arithmetic drift"


def test_martingale_log_drift_makes_price_a_fair_game():
    """With this drift the expected arithmetic return is zero, which is the true null."""
    daily_vol = 0.06
    n_bars = 24 * 365
    panel = random_walk(
        symbols=SYMBOLS, n_bars=n_bars, start=START, interval=HOUR, seed=16,
        initial_price=100.0, daily_vol=daily_vol,
        drift_daily_log_return=martingale_log_drift(daily_vol),
        quote_volume_per_bar=VOLUME, volume_jitter=0.0,
    )
    returns = np.exp(log_returns(panel, "AAA")) - 1.0
    per_day = bars_per_day(HOUR)
    observed = float(np.mean(returns)) * per_day
    standard_error = daily_vol / math.sqrt(n_bars / per_day)
    assert abs(observed) < SIGMAS * standard_error


def test_martingale_log_drift_matches_its_definition():
    for daily_vol in (0.01, 0.04, 0.2):
        assert martingale_log_drift(daily_vol) == pytest.approx(-0.5 * daily_vol**2)


def test_random_walk_symbols_are_independent():
    """No planted cross-correlation: a rho estimated from this panel should be near zero."""
    n_bars = 24 * 365
    panel = random_walk(
        symbols=SYMBOLS, n_bars=n_bars, start=START, interval=HOUR, seed=17,
        initial_price=100.0, daily_vol=0.04, drift_daily_log_return=0.0,
        quote_volume_per_bar=VOLUME, volume_jitter=0.0,
    )
    a, b = log_returns(panel, "AAA"), log_returns(panel, "BBB")
    correlation = float(np.corrcoef(a, b)[0, 1])
    assert abs(correlation) < SIGMAS / math.sqrt(len(a))


# --------------------------------------------------------------------------------------
# Volume
# --------------------------------------------------------------------------------------


def test_volume_is_constant_when_jitter_is_zero():
    level = 3_250_000.0
    panel = random_walk(
        symbols=SYMBOLS, n_bars=240, start=START, interval=HOUR, seed=18,
        initial_price=100.0, daily_vol=0.04, drift_daily_log_return=0.0,
        quote_volume_per_bar=dict.fromkeys(SYMBOLS, level), volume_jitter=0.0,
    )
    for symbol in SYMBOLS:
        assert np.all(np.asarray(full_view(panel).quote_volume(symbol)) == level)


def test_jittered_volume_keeps_its_median_at_the_requested_level():
    """README section 3.1 screens on the trailing MEDIAN, so the jitter preserves it."""
    level = 8_000_000.0
    jitter = 0.5
    n_bars = 24 * 400
    panel = random_walk(
        symbols=SYMBOLS, n_bars=n_bars, start=START, interval=HOUR, seed=19,
        initial_price=100.0, daily_vol=0.04, drift_daily_log_return=0.0,
        quote_volume_per_bar=dict.fromkeys(SYMBOLS, level), volume_jitter=jitter,
    )
    observed = float(np.median(np.asarray(full_view(panel).quote_volume("AAA"))))
    # Standard error of a median of a lognormal, in log space, is ~1.253 * j / sqrt(n).
    relative_error = 1.253 * jitter / math.sqrt(n_bars)
    assert abs(math.log(observed / level)) < SIGMAS * relative_error


def test_missing_volume_entry_is_rejected():
    with pytest.raises(KeyError, match="quote_volume_per_bar is missing"):
        random_walk(
            symbols=SYMBOLS, n_bars=10, start=START, interval=HOUR, seed=1,
            initial_price=100.0, daily_vol=0.04, drift_daily_log_return=0.0,
            quote_volume_per_bar={"AAA": 1.0}, volume_jitter=0.0,
        )


# --------------------------------------------------------------------------------------
# Deferred to Phase 7
# --------------------------------------------------------------------------------------


@pytest.mark.skip(
    reason="CLAUDE.md Phase 3 defers this to Phase 7: it needs the simulator and the "
    "strategy modules. The generator and its data-level tests exist now."
)
def test_random_walk_strategy_return_is_approximately_minus_fees():
    """THE Phase 3 gate, wired in at Phase 7.

    On a pure random walk the strategy must return approximately minus the fees paid and
    nothing more. Any positive return here is look-ahead or a sign error.

    Use drift_daily_log_return=martingale_log_drift(daily_vol); see the module docstring in
    synthetic.py for why zero log drift would make this assertion fail for a reason that has
    nothing to do with the strategy.
    """
    raise AssertionError("not yet wired; see Phase 7")
