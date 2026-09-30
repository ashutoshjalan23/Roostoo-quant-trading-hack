"""Deterministic hourly backtester using the live strategy and order planner."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
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
    entry_prices: dict[str, Decimal]

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
    stopped_symbols: tuple[str, ...]


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
    entry_prices = dict(book.entry_prices)
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
            remaining = positions.get(order.symbol, Decimal(0)) - order.quantity
            if remaining < 0:
                raise ValueError(f"sell fill exceeds {order.symbol} position")
            if remaining == 0:
                positions.pop(order.symbol, None)
                entry_prices.pop(order.symbol, None)
            else:
                positions[order.symbol] = remaining
            cash += notional - fee
        else:
            if cash < notional + fee:
                raise ValueError("buy fill exceeds available cash")
            previous_quantity = positions.get(order.symbol, Decimal(0))
            previous_entry = entry_prices.get(order.symbol, price)
            new_quantity = previous_quantity + order.quantity
            entry_prices[order.symbol] = (
                previous_quantity * previous_entry + order.quantity * price
            ) / new_quantity
            positions[order.symbol] = new_quantity
            cash -= notional + fee
        fills.append(Fill(timestamp, order.symbol, order.side, order.quantity, price, fee))
    positions = {symbol: quantity for symbol, quantity in positions.items() if quantity != 0}
    return BookState(cash, positions, entry_prices), tuple(fills)


def _stopped_symbols(
    book: BookState, prices: Mapping[str, Decimal | float], stop_loss_pct: float
) -> tuple[str, ...]:
    """Return positions whose latest close is at or below the entry-price stop."""
    stop_fraction = Decimal(str(stop_loss_pct))
    return tuple(
        symbol
        for symbol, quantity in sorted(book.positions.items())
        if quantity > 0
        and Decimal(str(prices[symbol]))
        <= book.entry_prices[symbol] * (Decimal(1) - stop_fraction)
    )


def _apply_stop_loss(
    book: BookState,
    stopped: tuple[str, ...],
    prices: Mapping[str, Decimal | float],
    timestamp: datetime,
    fill_prices: Mapping[str, Decimal | float],
    config: Config,
    exchange_info: Mapping[str, PairInfo],
) -> tuple[BookState, tuple[Order, ...], tuple[Fill, ...]]:
    """Use the shared planner to liquidate triggered positions at the next bar."""
    current_weights = book.weights(prices)
    target_weights = dict(current_weights)
    for symbol in stopped:
        target_weights[symbol] = 0.0
    orders = plan_orders(
        current_weights,
        target_weights,
        book.equity(prices),
        prices,
        exchange_info,
        replace(config.execution, drift_band=0.0),
        config.limits,
        config.costs,
    )
    updated_book, fills = _apply_orders(book, orders, fill_prices, timestamp, config)
    return updated_book, orders, fills


def run_backtest(
    panel: Panel,
    config: Config,
    exchange_info: Mapping[str, PairInfo],
    start: datetime,
    end: datetime,
) -> BacktestResult:
    """Run the strategy hourly; decisions at `t` fill on the next bar."""
    book = BookState(Decimal(str(config.backtest.initial_cash)), {}, {})
    records: list[CycleRecord] = []
    equity_curve: list[tuple[datetime, Decimal]] = []
    held: set[str] = set()
    cooldown_until: dict[str, datetime] = {}
    bars_since_start = 0
    for timestamp in panel.timestamps_between(start, end):
        bars_since_start += 1
        prices = panel.prices_at(timestamp)
        decimal_prices = {symbol: Decimal(str(price)) for symbol, price in prices.items()}
        equity = book.equity(decimal_prices)
        if bars_since_start < config.derived.warmup_bars:
            records.append(
                CycleRecord(timestamp, (), {}, {}, (), {}, (), (), equity, "warmup", ())
            )
            continue
        equity_curve.append((timestamp, equity))

        if config.risk.stop_loss_enabled:
            stopped = _stopped_symbols(book, decimal_prices, config.risk.stop_loss_pct)
            if stopped:
                try:
                    fill_timestamp = panel.next_timestamp(timestamp)
                except IndexError:
                    fill_timestamp = None
                if fill_timestamp is not None and fill_timestamp <= end:
                    stop_targets = book.weights(decimal_prices)
                    stop_targets.update({symbol: 0.0 for symbol in stopped})
                    book, orders, fills = _apply_stop_loss(
                        book,
                        stopped,
                        decimal_prices,
                        fill_timestamp,
                        panel.prices_at(fill_timestamp),
                        config,
                        exchange_info,
                    )
                    completed_stops: list[str] = []
                    for symbol in stopped:
                        if symbol not in book.positions:
                            completed_stops.append(symbol)
                            cooldown_until[symbol] = fill_timestamp + timedelta(
                                hours=config.risk.stop_cooldown_hours
                            )
                            held.discard(symbol)
                    if orders:
                        records.append(
                            CycleRecord(
                                timestamp, (), {}, {}, (), stop_targets, orders, fills,
                                equity, None, tuple(completed_stops),
                            )
                        )
                        continue
        if timestamp.hour != config.selection.hour_utc:
            continue

        # Strategy indicators are only consumed on rebalance hours. Avoid constructing
        # full-prefix views and recalculating historical indicators on every hourly bar.
        view = panel.slice_to(timestamp)

        try:
            fill_timestamp = panel.next_timestamp(timestamp)
        except IndexError:
            continue
        if fill_timestamp > end:
            continue

        eligible = universe.eligible(view, config.universe)
        sigmas = volatility.forecast(view, eligible, config.vol)
        signals = signal.compute(view, eligible, sigmas, config.signal)
        selected = selection.select(signals, held, config.selection)
        selected = tuple(
            symbol
            for symbol in selected
            if timestamp >= cooldown_until.get(symbol, timestamp)
        )
        target_weights = exposure.scale(
            weights.assign(selected, sigmas, config.weights), sigmas, view, config.exposure
        )
        orders = plan_orders(
            book.weights(decimal_prices), target_weights, equity, decimal_prices,
            exchange_info, config.execution, config.limits,
            config.costs,
        )
        fills: tuple[Fill, ...] = ()
        book, fills = _apply_orders(
            book, orders, panel.prices_at(fill_timestamp), fill_timestamp, config
        )
        held = set(selected)
        records.append(
            CycleRecord(
                timestamp, eligible, signals, sigmas, selected, target_weights,
                orders, fills, equity, None,
                (),
            )
        )
    return BacktestResult(tuple(equity_curve), tuple(records))
