"""Phase 11 journaling, safety, and cycle-order tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from scripts.replay import ReplayMismatch, replay_records

from qtrend.engine.journal import Journal
from qtrend.engine.loop import run_cycle
from qtrend.engine.safety import check_cycle_safety


def test_safety_halts_each_control():
    assert check_cycle_safety(2, 1, 0, 1, 0.1) == "stale_data"
    assert check_cycle_safety(0, 1, 2, 1, 0.1) == "unexplained_equity_move"
    assert check_cycle_safety(0, 1, 0, 1, float("nan")) == "non_finite_portfolio_volatility"
    assert check_cycle_safety(0, 1, 0, 1, 0.1, exception=RuntimeError()) == "unhandled_exception"
    assert check_cycle_safety(0, 1, 0, 1, 0.1, clock_skew_ms=2, max_clock_skew_ms=1) == "clock_skew"
    assert check_cycle_safety(float("nan"), 1, 0, 1, 0.1) == "invalid_data_age"
    assert check_cycle_safety(0, 1, float("nan"), 1, 0.1) == "invalid_equity_move"
    assert check_cycle_safety(0, 1, 0, 1, -0.1) == "non_finite_portfolio_volatility"


def test_cycle_orders_steps_and_dry_run_does_not_execute(tmp_path: Path):
    journal = Journal(tmp_path / "journal.jsonl", "test-commit")
    calls: list[str] = []

    result = run_cycle(
        lambda: calls.append("reconcile") or {"cash": 1},
        lambda state: calls.append("decide") or {"state": state},
        lambda decision: calls.append("plan") or [{"symbol": "AAA"}],
        lambda orders: calls.append("execute") or [{"filled": orders}],
        journal,
        data_age_seconds=0, max_data_age_seconds=1,
        unexplained_equity_move=0, max_unexplained_equity_move=1,
        portfolio_volatility=0.1, dry_run=True,
    )

    assert result["fills"] == []
    assert calls == ["reconcile", "decide", "plan"]
    assert journal.read()[0]["commit"] == "test-commit"


def test_journal_serializes_operational_types_and_replay_detects_mismatch(tmp_path: Path):
    journal = Journal(tmp_path / "journal.jsonl", "test-commit")
    journal.append(
        {
            "decision": {"timestamp": datetime.now(UTC), "cash": Decimal("1.0")},
            "orders": [],
            "fills": [],
        }
    )
    records = journal.read()
    assert records[0]["decision"]["cash"] == "1.0"
    assert replay_records(records, lambda record: record["decision"]) == 1
    with pytest.raises(ReplayMismatch):
        replay_records(records, lambda _: {"different": True})


def test_cycle_does_not_swallow_keyboard_interrupt(tmp_path: Path):
    journal = Journal(tmp_path / "journal.jsonl", "test-commit")
    with pytest.raises(KeyboardInterrupt):
        run_cycle(
            lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
            lambda state: state,
            lambda decision: [],
            lambda orders: [],
            journal,
            data_age_seconds=0, max_data_age_seconds=1,
            unexplained_equity_move=0, max_unexplained_equity_move=1,
            portfolio_volatility=0.1, dry_run=False,
        )
