"""Deterministic hourly backtester using the live strategy and order planner."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from qtrend.config import Config
from qtrend.data.panel import Panel
from qtrend.execution import Order, PairInfo, plan_orders
from qtrend.strategy import exposure, selection, signal, universe, volatility, weights


@dataclass(frozen=True, slots=True)
class Fill:
    timestamp: datetime
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    fee: Decimal


@dataclass(frozen=True, slots=True)
class BookState:
    cash: Decimal
    positions: dict[str, Decimal]

    def equity(self, prices: Mapping[str, Decimal | float]) -> Decimal:
        return self.cash + sum(
            (
                quantity * Decimal(str(prices[symbol]))
                for symbol, quantity in self.positions.items()
            ),
            Decimal(0),
        )

    def weights(self, prices: Mapping[str, Decimal | float]) -> dict[str, float]:
        total = self.equity(prices)
        if total <= 0:
            raise ValueError("book equity must be positive")
        return {
            symbol: float(quantity * Decimal(str(prices[symbol])) / total)
            for symbol, quantity in sorted(self.positions.items())
        }


@dataclass(frozen=True, slots=True)
class CycleRecord:
    timestamp: datetime
    eligible: tuple[str, ...]
    signals: dict[str, float]
    sigmas: dict[str, float]
    selected: tuple[str, ...]
    target_weights: dict[str, float]
    orders: tuple[Order, ...]
    fills: tuple[Fill, ...]
    equity: Decimal
    halted_reason: str | None


@dataclass(frozen=True, slots=True)
class BacktestResult:
    equity_curve: tuple[tuple[datetime, Decimal], ...]
    records: tuple[CycleRecord, ...]


def _apply_orders(
    book: BookState,
    orders: tuple[Order, ...],
    prices: Mapping[str, Decimal | float],
    timestamp: datetime,
    config: Config,
) -> tuple[BookState, tuple[Fill, ...]]:
    cash = book.cash
    positions = dict(book.positions)
    fills: list[Fill] = []
    for order in orders:
        price = Decimal(str(prices[order.symbol]))
        notional = order.quantity * price
        fee_rate = (
            config.costs.taker_fee
            if config.execution.order_type == "market"
            else config.costs.maker_fee
        )
        fee = notional * Decimal(str(fee_rate))
        fee += notional * Decimal(str(config.costs.slippage_bps)) / Decimal("10000")
        if order.side == "SELL":
            positions[order.symbol] = positions.get(order.symbol, Decimal(0)) - order.quantity
            cash += notional - fee
        else:
            if cash < notional + fee:
                raise ValueError("buy fill exceeds available cash")
            positions[order.symbol] = positions.get(order.symbol, Decimal(0)) + order.quantity
            cash -= notional + fee
        fills.append(Fill(timestamp, order.symbol, order.side, order.quantity, price, fee))
    positions = {symbol: quantity for symbol, quantity in positions.items() if quantity != 0}
    return BookState(cash, positions), tuple(fills)


def run_backtest(
    panel: Panel,
    config: Config,
    exchange_info: Mapping[str, PairInfo],
    start: datetime,
    end: datetime,
) -> BacktestResult:
    """Run the strategy hourly; decisions at `t` fill on the next bar."""
    book = BookState(Decimal(str(config.backtest.initial_cash)), {})
    records: list[CycleRecord] = []
    equity_curve: list[tuple[datetime, Decimal]] = []
    held: set[str] = set()
    for timestamp in panel.timestamps_between(start, end):
        view = panel.slice_to(timestamp)
        prices = view.last_prices()
        decimal_prices = {symbol: Decimal(str(price)) for symbol, price in prices.items()}
        equity = book.equity(decimal_prices)
        if view.n_rows < config.derived.warmup_bars:
            records.append(CycleRecord(timestamp, (), {}, {}, (), {}, (), (), equity, "warmup"))
            continue
        equity_curve.append((timestamp, equity))
        if timestamp.hour != config.selection.hour_utc:
            continue

        eligible = universe.eligible(view, config.universe)
        sigmas = volatility.forecast(view, eligible, config.vol)
        signals = signal.compute(view, eligible, sigmas, config.signal)
        selected = selection.select(signals, held, config.selection)
        target_weights = exposure.scale(
            weights.assign(selected, sigmas, config.weights), sigmas, view, config.exposure
        )
        orders = plan_orders(
            book.weights(decimal_prices), target_weights, equity, decimal_prices,
            exchange_info, config.execution, config.limits,
        )
        fills: tuple[Fill, ...] = ()
        if timestamp != panel.end_time:
            next_timestamp = panel.next_timestamp(timestamp)
            book, fills = _apply_orders(
                book, orders, panel.prices_at(next_timestamp), next_timestamp, config
            )
            held = set(selected)
        records.append(
            CycleRecord(
                timestamp, eligible, signals, sigmas, selected, target_weights,
                orders, fills, book.equity(decimal_prices), None,
            )
        )
    return BacktestResult(tuple(equity_curve), tuple(records))
