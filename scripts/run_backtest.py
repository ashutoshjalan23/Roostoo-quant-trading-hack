"""Command-line backtest entry point."""

from __future__ import annotations

import argparse
import json
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from qtrend.backtest.simulator import run_backtest
from qtrend.config import load_config
from qtrend.execution import PairInfo
from qtrend.strategy.baselines import top_volume

try:
    from scripts.fetch_history import load_cached_panel
except ModuleNotFoundError:
    from fetch_history import load_cached_panel


def interval_timedelta(interval: str) -> timedelta:
    """Convert the validated config interval to a timedelta."""
    count = int(interval[:-1])
    unit = interval[-1]
    if unit == "m":
        return timedelta(minutes=count)
    if unit == "h":
        return timedelta(hours=count)
    if unit == "d":
        return timedelta(days=count)
    raise ValueError(f"unsupported interval {interval!r}")


def pair_info_from_json(path: Path) -> dict[str, PairInfo]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("pair metadata must be a JSON object")
    return {
        symbol: PairInfo(
            step_size=Decimal(str(values["step_size"])),
            min_notional=Decimal(str(values["min_notional"])),
        )
        for symbol, values in sorted(document.items())
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--csv", type=Path, action="append", required=True)
    parser.add_argument("--pair-info", type=Path, required=True)
    parser.add_argument("--baselines", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    interval = interval_timedelta(config.data.interval)
    panel = load_cached_panel(args.csv, interval)
    info = pair_info_from_json(args.pair_info)
    result = run_backtest(
        panel, config, info, config.backtest.start, config.backtest.in_sample_end
    )
    output = {
        "bars": panel.n_rows,
        "symbols": panel.symbols,
        "equity_points": len(result.equity_curve),
        "cycles": len(result.records),
        "warmup_cycles": sum(record.halted_reason == "warmup" for record in result.records),
    }
    if args.baselines:
        visible = panel.slice_to(config.backtest.in_sample_end)
        output["top_volume_baseline"] = top_volume(
            visible, config.selection.top_k, int(config.derived.volume_bars)
        )
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
