"""Performance reporting tests for the command-line backtest."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from scripts.run_backtest import performance_summary


def test_performance_summary_uses_daily_marks_and_reports_cash_pnl():
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2024, 1, 4, tzinfo=UTC)
    config = SimpleNamespace(
        backtest=SimpleNamespace(
            report_from=start,
            in_sample_end=end,
            initial_cash=1_000_000.0,
            window_days=1,
            step_days=1,
        ),
        costs=SimpleNamespace(taker_fee=0.001, slippage_bps=10),
    )
    result = SimpleNamespace(
        equity_curve=(
            (start, Decimal("1000000")),
            (datetime(2024, 1, 2, tzinfo=UTC), Decimal("1010000")),
            (datetime(2024, 1, 3, tzinfo=UTC), Decimal("1000000")),
            (end, Decimal("1050000")),
        )
    )

    report = performance_summary(result, config)

    assert report["initial_cash_usd"] == 1_000_000.0
    assert report["ending_equity_usd"] == 1_050_000.0
    assert report["pnl_usd"] == 50_000.0
    assert report["total_return"] == pytest.approx(0.05)
    assert report["max_drawdown"] == pytest.approx(1.0 - 1_000_000 / 1_010_000)
    assert report["daily_observations"] == 3
    assert report["sortino_daily_annualized_mar_0"] is not None
    assert report["rolling_windows"]["count"] == 3
    assert report["rolling_windows"]["worst_return"] < 0


def test_calmar_is_undefined_when_drawdown_is_below_reported_threshold():
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2024, 1, 3, tzinfo=UTC)
    config = SimpleNamespace(
        backtest=SimpleNamespace(
            report_from=start,
            in_sample_end=end,
            initial_cash=1_000_000.0,
            window_days=1,
            step_days=1,
        ),
        costs=SimpleNamespace(taker_fee=0.001, slippage_bps=25),
    )
    result = SimpleNamespace(
        equity_curve=(
            (start, Decimal("1000000")),
            (datetime(2024, 1, 2, tzinfo=UTC), Decimal("1010000")),
            (end, Decimal("1005000")),
        )
    )

    report = performance_summary(result, config)

    assert report["calmar"] is None
