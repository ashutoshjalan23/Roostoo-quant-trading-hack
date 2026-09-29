"""Phase 8 scoring acceptance tests."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from qtrend.backtest.costs import apply_costs
from qtrend.backtest.metrics import calmar, composite, max_drawdown, sharpe, sortino
from qtrend.backtest.windows import bootstrap, rolling_windows
from qtrend.config import CostsConfig


def test_costs_and_hand_computed_metrics():
    costs = CostsConfig(taker_fee=0.001, maker_fee=0.0005, slippage_bps=10.0)
    assert apply_costs(Decimal("1000"), "market", costs) == Decimal("2")
    returns = (0.01, -0.01, 0.02)
    equity = (100.0, 101.0, 99.99, 101.9898)
    assert sharpe(returns, 1.0) == pytest.approx(0.4364357804719848)
    assert sortino(returns, 1.0, 0.0) > 0
    assert max_drawdown(equity) == pytest.approx((101.0 - 99.99) / 101.0)
    assert math.isfinite(calmar(returns, equity, 1.0, 0.01))
    assert math.isfinite(composite(returns, equity, 1.0, 0.0, 0.01, {
        "calmar": 1.0, "sharpe": 1.0, "sortino": 1.0,
    }))


def test_zero_drawdown_calmar_is_guarded():
    assert calmar((0.01, 0.01), (100.0, 101.0, 102.01), 1.0, 0.01) < math.inf


def test_windows_and_bootstrap_are_deterministic():
    curve = tuple(
        (datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=index), float(100 + index))
        for index in range(10)
    )
    windows = rolling_windows(curve, 3, 2)
    assert windows
    assert bootstrap(windows, 4, 7) == bootstrap(windows, 4, 7)
