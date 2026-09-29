"""Phase 7 simulator acceptance tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from qtrend.backtest.simulator import run_backtest
from qtrend.config import load_config
from qtrend.data.synthetic import constant_prices
from qtrend.execution import PairInfo
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
