"""Weekly cross-sectional long/short momentum backtest.

This is deliberately separate from the live spot-only simulator. Short positions here are
research accounting only; no exchange order is submitted. Prices are known at the decision
close and every rebalance fills at the next hourly close.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_DOWN, Decimal

import numpy as np

from qtrend.config import Config
from qtrend.data.panel import Panel
from qtrend.execution import PairInfo

_D = Decimal
_HOUR = 24
_FORMATION_HOURS = 31 * _HOUR
_SKIP_HOURS = _HOUR
_WEEKLY_VOL_TARGET = 0.10
_VOL_WINDOW = 8


@dataclass(slots=True)
class ShortPosition:
    quantity: Decimal
    entry_price: Decimal
    collateral: Decimal


@dataclass(slots=True)
class LongShortBook:
    cash: Decimal
    longs: dict[str, Decimal] = field(default_factory=dict)
    shorts: dict[str, ShortPosition] = field(default_factory=dict)

    def equity(self, prices: dict[str, float]) -> Decimal:
        result = self.cash
        for symbol, quantity in self.longs.items():
            result += quantity * _D(str(prices[symbol]))
        for symbol, position in self.shorts.items():
            mark = _D(str(prices[symbol]))
            pnl = position.quantity * (position.entry_price - mark)
            result += position.collateral + max(pnl, -position.collateral)
        return result


def _round_quantity(quantity: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        raise ValueError("pair step size must be positive")
    return (quantity / step).to_integral_value(rounding=ROUND_DOWN) * step


def _cost(notional: Decimal, config: Config) -> Decimal:
    return notional * (
        _D(str(config.costs.taker_fee)) + _D(str(config.costs.slippage_bps)) / _D(10000)
    )


def _close_long(
    book: LongShortBook, symbol: str, quantity: Decimal, price: Decimal, config: Config
) -> None:
    quantity = min(quantity, book.longs.get(symbol, _D(0)))
    if quantity <= 0:
        return
    notional = quantity * price
    book.cash += notional - _cost(notional, config)
    remaining = book.longs[symbol] - quantity
    if remaining == 0:
        del book.longs[symbol]
    else:
        book.longs[symbol] = remaining


def _buy_long(
    book: LongShortBook,
    symbol: str,
    quantity: Decimal,
    price: Decimal,
    step_size: Decimal,
    config: Config,
) -> None:
    notional = quantity * price
    charge = notional + _cost(notional, config)
    if charge > book.cash:
        affordable = _round_quantity(
            max(book.cash, _D(0))
            / (
                price
                * (
                    1
                    + _D(str(config.costs.taker_fee))
                    + _D(str(config.costs.slippage_bps)) / _D(10000)
                )
            ),
            step_size,
        )
        quantity = min(quantity, affordable)
        notional = quantity * price
        charge = notional + _cost(notional, config)
    if quantity > 0:
        book.cash -= charge
        book.longs[symbol] = book.longs.get(symbol, _D(0)) + quantity


def _close_short(
    book: LongShortBook, symbol: str, quantity: Decimal, price: Decimal, config: Config
) -> None:
    position = book.shorts[symbol]
    quantity = min(quantity, position.quantity)
    if quantity <= 0:
        return
    fraction = quantity / position.quantity
    collateral = position.collateral * fraction
    pnl = quantity * (position.entry_price - price)
    pnl = max(pnl, -collateral)
    notional = quantity * price
    book.cash += collateral + pnl - _cost(notional, config)
    position.quantity -= quantity
    position.collateral -= collateral
    if position.quantity == 0:
        del book.shorts[symbol]


def _open_short(
    book: LongShortBook,
    symbol: str,
    quantity: Decimal,
    price: Decimal,
    step_size: Decimal,
    config: Config,
) -> None:
    notional = quantity * price
    charge = notional + _cost(notional, config)
    if charge > book.cash:
        unit_cost = price * (
            1 + _D(str(config.costs.taker_fee)) + _D(str(config.costs.slippage_bps)) / _D(10000)
        )
        quantity = min(quantity, _round_quantity(max(book.cash, _D(0)) / unit_cost, step_size))
        notional = quantity * price
        charge = notional + _cost(notional, config)
    if quantity <= 0:
        return
    old = book.shorts.get(symbol)
    book.cash -= charge
    if old is None:
        book.shorts[symbol] = ShortPosition(quantity, price, notional)
    else:
        combined = old.quantity + quantity
        old.entry_price = (old.quantity * old.entry_price + quantity * price) / combined
        old.quantity = combined
        old.collateral += notional


def _weekly_scale(weekly_returns: list[float]) -> float:
    if len(weekly_returns) < _VOL_WINDOW:
        return 1.0
    sigma = float(np.std(weekly_returns[-_VOL_WINDOW:], ddof=1))
    return min(1.0, _WEEKLY_VOL_TARGET / sigma) if sigma > 0 else 1.0


def run_long_short_backtest(
    panel: Panel,
    config: Config,
    exchange_info: dict[str, PairInfo],
    start: datetime,
    end: datetime,
) -> list[tuple[datetime, Decimal]]:
    """Run 30-day momentum (skip one day), top/bottom quintile, weekly rebalance."""
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("backtest boundaries must be timezone-aware")
    cash = _D(str(config.backtest.initial_cash))
    book = LongShortBook(cash)
    curve: list[tuple[datetime, Decimal]] = []
    weekly_returns: list[float] = []
    last_week_start_equity: Decimal | None = None
    previous_rebalance: datetime | None = None
    symbols = tuple(symbol for symbol in panel.symbols if symbol in exchange_info)
    fee_and_slippage = config.costs.taker_fee + config.costs.slippage_bps / 10000
    # Gross target is haircut for entry costs, keeping collateral plus long notional
    # affordable from NAV without borrowing.
    affordability = 1.0 / (1.0 + fee_and_slippage)

    for timestamp in panel.timestamps_between(start, end):
        prices = panel.prices_at(timestamp)
        equity = book.equity(prices)
        if timestamp >= config.backtest.report_from:
            curve.append((timestamp, equity))
        if timestamp.weekday() != 0 or timestamp.hour != 0:
            continue
        view = panel.slice_to(timestamp)
        try:
            # Signal uses t-31d and t-1d closes: the latest day is skipped.
            ranked: list[tuple[float, str]] = []
            for symbol in symbols:
                stale = view.stale_mask(symbol)
                if len(stale) <= _FORMATION_HOURS or np.any(
                    stale[-(_FORMATION_HOURS + 1) : -(_SKIP_HOURS - 1)]
                ):
                    continue
                old_price = view.price_at_offset(symbol, _FORMATION_HOURS)
                recent_price = view.price_at_offset(symbol, _SKIP_HOURS)
                ranked.append((recent_price / old_price - 1.0, symbol))
        except (IndexError, KeyError):
            continue
        if len(ranked) < 5:
            continue
        ranked.sort(key=lambda item: (item[0], item[1]))
        count = max(1, len(ranked) // 5)
        short_names = {symbol for _, symbol in ranked[:count]}
        long_names = {symbol for _, symbol in ranked[-count:]}
        if long_names & short_names:
            continue

        if previous_rebalance is not None and last_week_start_equity and last_week_start_equity > 0:
            weekly_returns.append(float(equity / last_week_start_equity - 1))
        scale = _weekly_scale(weekly_returns)
        previous_rebalance = timestamp
        last_week_start_equity = equity
        try:
            fill_time = panel.next_timestamp(timestamp)
        except IndexError:
            break
        if fill_time > end:
            break
        fill_prices = {key: _D(str(value)) for key, value in panel.prices_at(fill_time).items()}
        nav = max(equity, _D(0))
        # Each side receives half the affordable, volatility-scaled NAV.
        target_side = nav * _D(str(0.5 * scale * affordability))
        targets_long = {symbol: target_side / len(long_names) for symbol in long_names}
        targets_short = {symbol: target_side / len(short_names) for symbol in short_names}

        # Execute all reductions/exits first to release cash and short collateral.
        for symbol, quantity in tuple(book.longs.items()):
            target = targets_long.get(symbol, _D(0))
            desired = _round_quantity(target / fill_prices[symbol], exchange_info[symbol].step_size)
            if quantity > desired:
                _close_long(book, symbol, quantity - desired, fill_prices[symbol], config)
        for symbol, position in tuple(book.shorts.items()):
            target = targets_short.get(symbol, _D(0))
            desired = _round_quantity(target / fill_prices[symbol], exchange_info[symbol].step_size)
            if position.quantity > desired:
                _close_short(book, symbol, position.quantity - desired, fill_prices[symbol], config)

        # Then add exposure. Cash constraints include short collateral and all modeled costs.
        for symbol, target in sorted(targets_long.items()):
            desired = _round_quantity(target / fill_prices[symbol], exchange_info[symbol].step_size)
            held = book.longs.get(symbol, _D(0))
            if desired > held:
                _buy_long(
                    book,
                    symbol,
                    desired - held,
                    fill_prices[symbol],
                    exchange_info[symbol].step_size,
                    config,
                )
        for symbol, target in sorted(targets_short.items()):
            desired = _round_quantity(target / fill_prices[symbol], exchange_info[symbol].step_size)
            held = book.shorts.get(symbol, ShortPosition(_D(0), _D(0), _D(0))).quantity
            if desired > held:
                _open_short(
                    book,
                    symbol,
                    desired - held,
                    fill_prices[symbol],
                    exchange_info[symbol].step_size,
                    config,
                )

    return curve
