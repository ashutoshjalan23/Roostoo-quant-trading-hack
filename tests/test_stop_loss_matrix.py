"""Tests for the requested stop-loss matrix reporting helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from scripts.run_stop_loss_matrix import fortnight_pnl_distribution

from qtrend.backtest.simulator import BacktestResult


def test_fortnight_distribution_counts_only_complete_non_overlapping_windows():
    start = datetime(2025, 7, 1, tzinfo=UTC)
    result = BacktestResult(
        equity_curve=(
            (start, Decimal("1000")),
            (start + timedelta(days=14), Decimal("1100")),
            (start + timedelta(days=28), Decimal("900")),
            (start + timedelta(days=36), Decimal("950")),
        ),
        records=(),
    )

    distribution = fortnight_pnl_distribution(
        result, start, start + timedelta(days=36), window_days=14
    )

    assert distribution["count"] == 2
    assert distribution["partial_tail_days"] == 8
    assert distribution["positive_windows"] == 1
    assert distribution["negative_windows"] == 1
    assert distribution["best_pnl_usd"] == 100.0
    assert distribution["worst_pnl_usd"] == -200.0
    assert sum(row["frequency"] for row in distribution["histogram"]) == 2
