from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np

from qtrend.backtest.long_short import LongShortBook, _close_short, _open_short
from qtrend.config import load_config
from qtrend.data.panel import Panel
from qtrend.execution import PairInfo


def test_short_ledger_marks_and_realizes_pnl_after_costs():
    config = load_config("configs/research-usd-1m.toml")
    book = LongShortBook(Decimal("1000"))
    _open_short(book, "AAA", Decimal("2"), Decimal("100"), Decimal("0.01"), config)
    assert book.cash < Decimal("800")  # collateral and entry costs are reserved
    assert book.equity({"AAA": 90.0}) > Decimal("1000")
    _close_short(book, "AAA", Decimal("2"), Decimal("90"), config)
    assert not book.shorts
    assert book.equity({"AAA": 90.0}) > Decimal("1010")  # realized gain, net of both fills


def test_short_loss_cannot_exceed_posted_collateral():
    config = load_config("configs/research-usd-1m.toml")
    book = LongShortBook(Decimal("1000"))
    _open_short(book, "AAA", Decimal("2"), Decimal("100"), Decimal("0.01"), config)
    assert book.equity({"AAA": 1000.0}) >= 0
    position = book.shorts["AAA"]
    assert position.collateral == Decimal("200")
    assert book.equity({"AAA": 250.0}) == book.cash


def test_short_signal_uses_winners_and_losers_and_respects_nav_budget():
    config = load_config("configs/research-usd-1m.toml")
    config = replace(config, backtest=replace(config.backtest, initial_cash=100_000))
    start = datetime(2024, 1, 1, tzinfo=UTC)
    hours = 24 * 50
    index = [start + timedelta(hours=i) for i in range(hours)]
    symbols = [f"C{i}" for i in range(10)]
    rows = np.arange(hours, dtype=float)[:, None]
    slopes = np.arange(-4, 6, dtype=float)[None, :]
    close = 100 + rows * slopes * 0.01
    panel = Panel(
        index=index,
        close=close,
        quote_volume=np.full_like(close, 1e8),
        stale=np.zeros_like(close, dtype=bool),
        symbols=symbols,
    )
    info = {symbol: PairInfo(Decimal("0.00001"), Decimal("1")) for symbol in symbols}
    report_from = datetime(2024, 2, 5, tzinfo=UTC)
    curve = __import__("qtrend.backtest.long_short", fromlist=["run_long_short_backtest"])
    result = curve.run_long_short_backtest(panel, config, info, report_from, panel.end_time)
    assert result
    assert result[-1][1] > Decimal("100000")
    assert max(value for _, value in result) < Decimal("120000")
