"""Phase 7 simulator acceptance tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from qtrend.backtest.simulator import (
    BookState,
    _apply_orders,
    _apply_stop_loss,
    _stopped_symbols,
    run_backtest,
)
from qtrend.config import load_config
from qtrend.data.synthetic import constant_prices
from qtrend.execution import Order, PairInfo
from test_config import _toml_value, valid_document


def test_simulator_is_deterministic_and_respects_warmup(tmp_path):
    document = valid_document()
    document["data"]["warmup_halflives"] = 1.0
    document["universe"]["volume_window_days"] = 1
    document["universe"]["min_daily_dollar_volume"] = 1_000.0
    document["vol"]["min_observations"] = 1
    document["signal"]["horizons_days"] = [1]
    document["selection"]["hour_utc"] = 1
    document["exposure"]["rho_window_days"] = 1
    document["limits"]["max_order_fraction"] = 0.5
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            f"[{section}]\n" + "\n".join(
                f"{key} = {_toml_value(value)}" for key, value in values.items()
            )
            for section, values in document.items()
        ),
        encoding="utf-8",
    )
    config = load_config(config_path)
    panel = constant_prices(
        symbols=("AAA",), n_bars=72, start=datetime(2024, 1, 1, tzinfo=UTC),
        interval=timedelta(hours=1), price=100.0, quote_volume_per_bar={"AAA": 10_000.0},
    )
    info = {"AAA": PairInfo(Decimal("0.01"), Decimal("10"))}
    first = run_backtest(panel, config, info, panel.start_time, panel.end_time)
    second = run_backtest(panel, config, info, panel.start_time, panel.end_time)

    assert first.equity_curve == second.equity_curve
    assert any(record.halted_reason == "warmup" for record in first.records)

    # A full historical panel must not bypass the configured warm-up at a later run start.
    later_start = panel.start_time + timedelta(hours=24)
    later = run_backtest(panel, config, info, later_start, panel.end_time)
    assert sum(record.halted_reason == "warmup" for record in later.records) == (
        config.derived.warmup_bars - 1
    )

    cutoff = datetime(2024, 1, 2, 1, tzinfo=UTC)
    bounded = run_backtest(panel, config, info, panel.start_time, cutoff)
    assert all(fill.timestamp <= cutoff for record in bounded.records for fill in record.fills)


def test_percentage_stop_triggers_at_entry_loss_threshold():
    book = BookState(
        cash=Decimal("0"),
        positions={"BTCUSD": Decimal("2")},
        entry_prices={"BTCUSD": Decimal("100")},
    )

    assert _stopped_symbols(book, {"BTCUSD": 95.0}, 0.05) == ("BTCUSD",)
    assert _stopped_symbols(book, {"BTCUSD": 95.01}, 0.05) == ()


def test_average_entry_price_updates_on_additional_buy(tmp_path):
    document = valid_document()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            f"[{section}]\n" + "\n".join(
                f"{key} = {_toml_value(value)}" for key, value in values.items()
            )
            for section, values in document.items()
        ),
        encoding="utf-8",
    )
    config = load_config(config_path)
    timestamp = datetime(2025, 1, 1, tzinfo=UTC)
    book, _ = _apply_orders(
        BookState(Decimal("1000"), {}, {}),
        (Order("AAA", "BUY", Decimal("1"), Decimal("100")),),
        {"AAA": 100.0},
        timestamp,
        config,
    )
    book, _ = _apply_orders(
        book,
        (Order("AAA", "BUY", Decimal("1"), Decimal("110")),),
        {"AAA": 110.0},
        timestamp + timedelta(hours=1),
        config,
    )

    assert book.entry_prices["AAA"] == Decimal("105")
    assert _stopped_symbols(book, {"AAA": 99.75}, 0.05) == ("AAA",)


def test_stop_liquidation_uses_next_bar_price_and_clears_entry(tmp_path):
    document = valid_document()
    document["limits"]["max_order_fraction"] = 1.0
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            f"[{section}]\n" + "\n".join(
                f"{key} = {_toml_value(value)}" for key, value in values.items()
            )
            for section, values in document.items()
        ),
        encoding="utf-8",
    )
    config = load_config(config_path)
    trigger = datetime(2025, 1, 1, tzinfo=UTC)
    fill_time = trigger + timedelta(hours=1)
    book = BookState(
        cash=Decimal("800"),
        positions={"AAA": Decimal("2")},
        entry_prices={"AAA": Decimal("100")},
    )

    updated, orders, fills = _apply_stop_loss(
        book,
        ("AAA",),
        {"AAA": 95.0},
        fill_time,
        {"AAA": 90.0},
        config,
        {"AAA": PairInfo(Decimal("0.01"), Decimal("10"))},
    )

    assert len(orders) == len(fills) == 1
    assert fills[0].timestamp == fill_time
    assert fills[0].price == Decimal("90.0")
    assert updated.positions == {}
    assert updated.entry_prices == {}
