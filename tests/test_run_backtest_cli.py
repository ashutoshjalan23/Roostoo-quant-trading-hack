"""Phase 9 end-to-end cached-panel backtest CLI test."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

from scripts.run_backtest import main

from test_config import _toml_value, valid_document


def test_run_backtest_cli_loads_cache_and_metadata(tmp_path, capsys, monkeypatch):
    document = valid_document()
    document["data"]["warmup_halflives"] = 1.0
    document["universe"]["volume_window_days"] = 1
    document["universe"]["min_daily_dollar_volume"] = 1_000.0
    document["vol"]["min_observations"] = 1
    document["signal"]["horizons_days"] = [1]
    document["selection"]["top_k"] = 1
    document["selection"]["keep_rank"] = 2
    document["selection"]["hour_utc"] = 1
    document["weights"]["max_single_name"] = 1.0
    document["exposure"]["rho_window_days"] = 1
    document["exposure"]["max_leverage"] = 0.5
    document["limits"]["max_order_fraction"] = 0.5
    document["backtest"]["report_from"] = "2024-01-02T06:00:00Z"
    config = tmp_path / "config.toml"
    config.write_text(
        "\n".join(
            f"[{section}]\n" + "\n".join(
                f"{key} = {_toml_value(value)}" for key, value in values.items()
            )
            for section, values in document.items()
        ),
        encoding="utf-8",
    )
    csv_path = tmp_path / "AAA-2024.csv"
    rows = []
    for index in range(72):
        opened = datetime(2024, 1, 1, tzinfo=UTC) + index * timedelta(hours=1)
        close = int((opened + timedelta(hours=1) - timedelta(milliseconds=1)).timestamp() * 1000)
        rows.append(f"{int(opened.timestamp() * 1000)},100,101,99,100,10,{close},10000")
    csv_path.write_text("\n".join(rows), encoding="utf-8")
    pair_info = tmp_path / "pair-info.json"
    pair_info.write_text('{"AAA": {"step_size": "0.01", "min_notional": "10"}}', encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_backtest.py", "--config", str(config), "--csv", str(csv_path),
            "--pair-info", str(pair_info), "--baselines",
        ],
    )
    assert main() == 0
    assert '"bars": 72' in capsys.readouterr().out
