"""The single deterministic order planner shared by simulation and live execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from qtrend.config import CostsConfig, ExecutionConfig, LimitsConfig


@dataclass(frozen=True, slots=True)
class PairInfo:
    """Exchange-fetched quantity and notional constraints for one pair."""

    step_size: Decimal
    min_notional: Decimal


@dataclass(frozen=True, slots=True)
class Order:
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal

    @property
    def notional(self) -> Decimal:
        return self.quantity * self.price


def _decimal(value: Decimal | float | int) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _floor_step(quantity: Decimal, step_size: Decimal) -> Decimal:
    if step_size <= 0:
        raise ValueError("pair step_size must be positive")
    return (quantity / step_size).to_integral_value(rounding=ROUND_DOWN) * step_size


def plan_orders(
    current: Mapping[str, float | Decimal],
    target: Mapping[str, float | Decimal],
    equity: float | Decimal,
    prices: Mapping[str, float | Decimal],
    info: Mapping[str, PairInfo],
    execution: ExecutionConfig,
    limits: LimitsConfig,
    costs: CostsConfig,
) -> tuple[Order, ...]:
    """Plan rounded, banded orders without exceeding cash or per-order limits."""
    equity_value = _decimal(equity)
    if equity_value <= 0:
        raise ValueError("equity must be positive")

    symbols = sorted(set(current) | set(target))
    sells: list[Order] = []
    buys: list[tuple[str, Decimal, Decimal]] = []
    for symbol in symbols:
        if symbol not in prices or symbol not in info:
            raise KeyError(f"missing price or pair metadata for {symbol!r}")
        current_weight = _decimal(current.get(symbol, 0))
        target_weight = _decimal(target.get(symbol, 0))
        drift = target_weight - current_weight
        if abs(drift) <= _decimal(execution.drift_band):
            continue

        price = _decimal(prices[symbol])
        if price <= 0:
            raise ValueError(f"price for {symbol!r} must be positive")
        desired_notional = abs(drift) * equity_value
        if desired_notional > equity_value * _decimal(limits.max_order_fraction):
            raise ValueError(f"order for {symbol!r} exceeds max_order_fraction")
        # A weighted target travels through float-derived equity/weights; absorb only
        # sub-picostep representation noise so exact full exits do not leave dust.
        quantity = _floor_step(
            desired_notional / price + info[symbol].step_size * Decimal("1e-12"),
            info[symbol].step_size,
        )
        if quantity <= 0 or quantity * price < info[symbol].min_notional:
            continue
        if drift < 0:
            sells.append(Order(symbol, "SELL", quantity, price))
        else:
            buys.append((symbol, quantity, price))

    current_invested = sum(
        (_decimal(current.get(symbol, 0)) * equity_value for symbol in current),
        Decimal(0),
    )
    current_cash = equity_value - current_invested
    cost_rate = _decimal(
        costs.taker_fee if execution.order_type == "market" else costs.maker_fee
    ) + _decimal(costs.slippage_bps) / Decimal("10000")
    sell_proceeds = sum(
        (order.notional * (Decimal(1) - cost_rate) for order in sells), Decimal(0)
    )
    available_cash = current_cash + sell_proceeds - equity_value * _decimal(execution.cash_buffer)
    if available_cash < 0:
        available_cash = Decimal(0)

    buy_orders: list[Order] = []
    for symbol, quantity, price in buys:
        affordable = _floor_step(
            available_cash / (price * (Decimal(1) + cost_rate)), info[symbol].step_size
        )
        quantity = min(quantity, affordable)
        if quantity <= 0 or quantity * price < info[symbol].min_notional:
            continue
        order = Order(symbol, "BUY", quantity, price)
        buy_orders.append(order)
        available_cash -= order.notional * (Decimal(1) + cost_rate)

    return tuple(sorted(sells, key=lambda order: order.symbol) + buy_orders)
