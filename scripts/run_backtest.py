"""Command-line backtest entry point."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import numpy as np

from qtrend.backtest.metrics import calmar, max_drawdown, sharpe, sortino
from qtrend.backtest.simulator import run_backtest
from qtrend.backtest.windows import rolling_windows
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


def performance_summary(result, config) -> dict[str, object]:
    """Summarize net performance from UTC daily closing equity observations."""
    start = config.backtest.report_from
    end = config.backtest.in_sample_end
    equity_by_day: dict[date, float] = {start.date(): config.backtest.initial_cash}
    for timestamp, value in result.equity_curve:
        if start <= timestamp <= end:
            equity_by_day[timestamp.astimezone(UTC).date()] = float(value)
    daily_equity = [equity_by_day[day] for day in sorted(equity_by_day)]
    daily_curve = [
        (datetime.combine(day, datetime.min.time(), tzinfo=UTC), equity_by_day[day])
        for day in sorted(equity_by_day)
    ]
    daily_returns = [
        current / previous - 1.0
        for previous, current in zip(daily_equity, daily_equity[1:], strict=False)
        if previous > 0
    ]
    initial_cash = float(config.backtest.initial_cash)
    ending_equity = daily_equity[-1]
    total_return = ending_equity / initial_cash - 1.0
    drawdown = max_drawdown(daily_equity)
    periods_per_year = 365.0
    calmar_value = (
        calmar(daily_returns, daily_equity, periods_per_year, min_drawdown=0.01)
        if drawdown >= 0.01
        else None
    )
    windows = rolling_windows(
        daily_curve, config.backtest.window_days, config.backtest.step_days
    )
    window_returns = [window.equity[-1] / window.equity[0] - 1.0 for window in windows]
    window_drawdowns = [max_drawdown(window.equity) for window in windows]
    window_distribution = (
        {
            "count": len(windows),
            "median_return": float(np.median(window_returns)),
            "p10_return": float(np.percentile(window_returns, 10)),
            "p90_return": float(np.percentile(window_returns, 90)),
            "worst_return": float(min(window_returns)),
            "max_drawdown": float(max(window_drawdowns)),
        }
        if windows
        else {"count": 0}
    )
    return {
        "report_from": start.isoformat(),
        "in_sample_end": end.isoformat(),
        "initial_cash_usd": initial_cash,
        "ending_equity_usd": ending_equity,
        "pnl_usd": ending_equity - initial_cash,
        "total_return": total_return,
        "max_drawdown": drawdown,
        "calmar": calmar_value,
        "calmar_undefined_below_drawdown": 0.01,
        "sharpe_daily_annualized": sharpe(daily_returns, periods_per_year),
        "sortino_daily_annualized_mar_0": sortino(daily_returns, periods_per_year, mar=0.0),
        "daily_observations": len(daily_returns),
        "daily_mar": 0.0,
        "annualization_days": periods_per_year,
        "taker_fee_per_side": config.costs.taker_fee,
        "slippage_bps_per_order": config.costs.slippage_bps,
        "rolling_window_days": config.backtest.window_days,
        "rolling_windows": window_distribution,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--csv", type=Path, action="append", required=True)
    parser.add_argument("--pair-info", type=Path, required=True)
    parser.add_argument(
        "--symbol-map", type=Path,
        help="JSON mapping data symbols to strategy symbols, e.g. BTCUSDT to BTCUSD",
    )
    parser.add_argument("--baselines", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    interval = interval_timedelta(config.data.interval)
    symbol_map = (
        json.loads(args.symbol_map.read_text(encoding="utf-8")) if args.symbol_map else None
    )
    panel = load_cached_panel(args.csv, interval, symbol_map)
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
        "performance": performance_summary(result, config),
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
