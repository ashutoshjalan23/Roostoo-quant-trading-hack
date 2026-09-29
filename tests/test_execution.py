"""Acceptance tests for the single order planner."""

from __future__ import annotations

from decimal import Decimal

import pytest

from qtrend.config import CostsConfig, ExecutionConfig, LimitsConfig
from qtrend.execution import PairInfo, plan_orders

INFO = {
    "AAA": PairInfo(step_size=Decimal("0.1"), min_notional=Decimal("10")),
    "BBB": PairInfo(step_size=Decimal("0.01"), min_notional=Decimal("10")),
}
EXECUTION = ExecutionConfig(
    drift_band=0.05, order_type="market", cash_buffer=0.1,
    sells_before_buys=True, use_client_order_id=True,
)
LIMITS = LimitsConfig(
    api_calls_per_minute=1, min_seconds_between_orders=1.0, max_order_fraction=0.5,
)
COSTS = CostsConfig(taker_fee=0.001, maker_fee=0.0005, slippage_bps=5.0)


def test_drift_inside_band_produces_no_order():
    orders = plan_orders(
        {"AAA": 0.50}, {"AAA": 0.54}, 1_000, {"AAA": 100}, INFO,
        EXECUTION, LIMITS, COSTS,
    )

    assert orders == ()


def test_quantity_is_rounded_down_and_minimum_notional_is_applied():
    orders = plan_orders(
        {"AAA": 0.0}, {"AAA": 0.153}, 1_000, {"AAA": 100}, INFO,
        EXECUTION, LIMITS, COSTS,
    )

    assert orders[0].quantity == Decimal("1.5")
    assert orders[0].notional >= INFO["AAA"].min_notional


def test_sells_precede_buys_and_buys_stay_within_cash_after_sells():
    rotation_limits = LimitsConfig(
        api_calls_per_minute=1, min_seconds_between_orders=1.0, max_order_fraction=0.9,
    )
    orders = plan_orders(
        {"AAA": 0.8, "BBB": 0.0}, {"AAA": 0.0, "BBB": 0.8}, 1_000,
        {"AAA": 100, "BBB": 100}, INFO, EXECUTION, rotation_limits, COSTS,
    )

    assert [order.side for order in orders] == ["SELL", "BUY"]
    sell_value = sum((order.notional for order in orders if order.side == "SELL"), Decimal(0))
    buy_value = sum((order.notional for order in orders if order.side == "BUY"), Decimal(0))
    assert buy_value <= Decimal("200") + sell_value


def test_order_above_max_fraction_is_rejected_not_truncated():
    with pytest.raises(ValueError, match="max_order_fraction"):
        plan_orders(
            {"AAA": 0.0}, {"AAA": 0.6}, 1_000, {"AAA": 100}, INFO,
            EXECUTION, LIMITS, COSTS,
        )


def test_buy_planning_reserves_fees_and_slippage_even_with_a_small_cash_buffer():
    execution = ExecutionConfig(
        drift_band=0.0, order_type="market", cash_buffer=0.00001,
        sells_before_buys=True, use_client_order_id=True,
    )
    limits = LimitsConfig(
        api_calls_per_minute=1, min_seconds_between_orders=1.0, max_order_fraction=1.0,
    )
    costs = CostsConfig(taker_fee=0.009, maker_fee=0.0005, slippage_bps=999.0)
    orders = plan_orders(
        {}, {"AAA": 1.0}, 1_000, {"AAA": 100}, INFO, execution, limits, costs
    )

    cost_rate = costs.taker_fee + costs.slippage_bps / 10_000
    reserved_cash = Decimal("1000") * Decimal(str(execution.cash_buffer))
    spend = sum((order.notional for order in orders), Decimal(0)) * Decimal(
        str(1 + cost_rate)
    )
    assert spend <= Decimal("1000") - reserved_cash
