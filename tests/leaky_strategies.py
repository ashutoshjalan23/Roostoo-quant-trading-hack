"""Deliberately broken strategies, plus an honest one, for testing the look-ahead checker.

Not a test module -- no `test_` prefix, so pytest does not collect it. These functions exist
to be caught.

### Why they leak the way they do

The checker perturbs a *copy* of the panel and hands the strategy a view of that copy. A
strategy that closed over some outer panel would read the unperturbed original, produce the
same answer twice, and the checker would report a pass on a strategy that is leaking. So to
leak in a way the checker can see -- which is also the only way a leak matters in the
simulator -- the strategy must reach the future through the object it was handed.

The door is `view._panel`. `Panel.slice_to` is public, so holding a Panel is holding the
future. That is exactly why CLAUDE.md rule 4 says strategy code receives a `PanelView` and
nothing else: the boundary works by never handing over a Panel, not by being impossible to
circumvent from inside Python.

Note none of these read the four private panel arrays the Phase 2 grep gate guards, so that
gate stays green. They reach the future through public Panel and PanelView accessors after
grabbing the panel reference. (Naming those four arrays in this sentence would itself trip
the gate, which is the gate working.)
"""

from __future__ import annotations

import math

from qtrend.data.panel import PanelView


def _future_view(view: PanelView, bars_ahead: int) -> PanelView:
    """A view extending `bars_ahead` bars past the end of `view`. This is the leak."""
    panel = view._panel
    moment = view.end_time
    for _ in range(bars_ahead):
        moment = panel.next_timestamp(moment)
    return panel.slice_to(moment)


# --------------------------------------------------------------------------------------
# Correct
# --------------------------------------------------------------------------------------


def honest_momentum(view: PanelView, config: dict) -> dict[str, float]:
    """Trailing return over a fixed horizon. Reads nothing it was not given.

    Returns NaN before enough history exists, which also exercises the checker's NaN
    handling: NaN must compare equal to NaN or every warm-up bar looks like a leak.
    """
    horizon = config["horizon_bars"]
    out: dict[str, float] = {}
    for symbol in view.symbols:
        if view.n_rows <= horizon:
            out[symbol] = math.nan
        else:
            now = view.price_at_offset(symbol, 0)
            then = view.price_at_offset(symbol, horizon)
            out[symbol] = now / then - 1.0
    return out


def always_nan(view: PanelView, config: dict) -> dict[str, float]:
    """Every value NaN. Must pass: identical output is identical output."""
    return dict.fromkeys(view.symbols, math.nan)


def constant_decision(view: PanelView, config: dict) -> dict[str, float]:
    """Ignores the data entirely. The trivial correct strategy."""
    weight = 1.0 / len(view.symbols)
    return dict.fromkeys(view.symbols, weight)


# --------------------------------------------------------------------------------------
# Broken
# --------------------------------------------------------------------------------------


def reads_one_bar_ahead(view: PanelView, config: dict) -> dict[str, float]:
    """The canonical leak: next bar's return, known before it happened."""
    future = _future_view(view, 1)
    out: dict[str, float] = {}
    for symbol in view.symbols:
        out[symbol] = future.price_at_offset(symbol, 0) / view.price_at_offset(symbol, 0) - 1.0
    return out


def reads_the_whole_future(view: PanelView, config: dict) -> dict[str, float]:
    """Buys whichever symbol ends the sample highest. The backtest looks superb."""
    panel = view._panel
    final = panel.slice_to(panel.end_time).last_prices()
    now = view.last_prices()
    return {symbol: final[symbol] / now[symbol] - 1.0 for symbol in view.symbols}


def leaks_only_at_one_hour(view: PanelView, config: dict) -> dict[str, float]:
    """Leaks only on bars closing at a particular hour.

    A single-timestamp check has a good chance of missing this. It is why the checker runs
    across a sample of timestamps rather than one.
    """
    if view.end_time.hour == config["leak_hour"]:
        return reads_one_bar_ahead(view, config)
    return honest_momentum(view, config)


def records_the_future_but_decides_nothing(view: PanelView, config: dict) -> dict[str, float]:
    """Looks at the future, records it, and returns a constant.

    Not a leak -- the decision does not depend on what it saw, so the checker passes it.
    That is what makes it useful as a spy: the sweep runs to completion and the recording
    shows which perturbation each call actually saw.
    """
    config["seen"].append(_future_view(view, 1).last_prices()["AAA"])
    return {"constant": 1.0}


def leaks_through_volume_only(view: PanelView, config: dict) -> dict[str, float]:
    """Reads future VOLUME and no future price.

    Caught only if the perturbation covers quote volume, not just close prices.
    """
    ahead = config["bars_ahead"]
    future = _future_view(view, ahead)
    out: dict[str, float] = {}
    for symbol in view.symbols:
        window = future.quote_volume(symbol)[-ahead:]
        out[symbol] = float(window.sum())
    return out


def leaks_through_stale_only(view: PanelView, config: dict) -> dict[str, float]:
    """Reads the future STALE MASK and nothing else.

    Caught only if the perturbation covers every column, as README section 7.2 requires
    ("prices, volumes, everything").
    """
    ahead = config["bars_ahead"]
    future = _future_view(view, ahead)
    out: dict[str, float] = {}
    for symbol in view.symbols:
        window = future.stale_mask(symbol)[-ahead:]
        out[symbol] = float(window.sum())
    return out
