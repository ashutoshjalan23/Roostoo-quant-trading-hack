"""Hard halt conditions for a live cycle."""

from __future__ import annotations

import math


def check_cycle_safety(
    data_age_seconds: float,
    max_data_age_seconds: float,
    unexplained_equity_move: float,
    max_unexplained_equity_move: float,
    portfolio_volatility: float,
    exception: BaseException | None = None,
    clock_skew_ms: float | None = None,
    max_clock_skew_ms: float | None = None,
) -> str | None:
    if exception is not None:
        return "unhandled_exception"
    if (
        clock_skew_ms is not None
        and max_clock_skew_ms is not None
        and abs(clock_skew_ms) > max_clock_skew_ms
    ):
        return "clock_skew"
    if data_age_seconds > max_data_age_seconds:
        return "stale_data"
    if abs(unexplained_equity_move) > max_unexplained_equity_move:
        return "unexplained_equity_move"
    if not math.isfinite(portfolio_volatility):
        return "non_finite_portfolio_volatility"
    return None
