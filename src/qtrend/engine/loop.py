"""One live cycle in reconcile -> decide -> plan -> execute -> journal order."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from qtrend.engine.journal import Journal
from qtrend.engine.safety import check_cycle_safety


def run_cycle(
    reconcile: Callable[[], Mapping[str, Any]],
    decide: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    plan: Callable[[Mapping[str, Any]], list[Mapping[str, Any]]],
    execute: Callable[[list[Mapping[str, Any]]], list[Mapping[str, Any]]],
    journal: Journal,
    *,
    data_age_seconds: float,
    max_data_age_seconds: float,
    unexplained_equity_move: float,
    max_unexplained_equity_move: float,
    portfolio_volatility: float,
    dry_run: bool,
) -> dict[str, Any]:
    try:
        state = reconcile()
        halt = check_cycle_safety(
            data_age_seconds, max_data_age_seconds, unexplained_equity_move,
            max_unexplained_equity_move, portfolio_volatility,
        )
    except BaseException:
        result = {"halted_reason": "unhandled_exception", "decision": {}, "orders": [], "fills": []}
        journal.append(result)
        return result
    if halt is not None:
        result = {"halted_reason": halt, "decision": {}, "orders": [], "fills": []}
        journal.append(result)
        return result
    try:
        decision = decide(state)
        orders = plan(decision)
        fills = [] if dry_run else execute(orders)
    except BaseException:
        result = {"halted_reason": "unhandled_exception", "decision": {}, "orders": [], "fills": []}
        journal.append(result)
        return result
    result = {"halted_reason": None, "decision": decision, "orders": orders, "fills": fills}
    journal.append(result)
    return result
