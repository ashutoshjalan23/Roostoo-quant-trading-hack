"""Run the requested fixed-percent stop-loss matrix over a cached hourly panel."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from qtrend.backtest.simulator import BacktestResult, run_backtest
from qtrend.config import load_config
from qtrend.execution import PairInfo

try:
    from scripts.fetch_history import load_cached_panel
    from scripts.run_backtest import interval_timedelta, pair_info_from_json, performance_summary
except ModuleNotFoundError:
    from fetch_history import load_cached_panel
    from run_backtest import interval_timedelta, pair_info_from_json, performance_summary


def _at_or_before(
    curve: tuple[tuple[datetime, object], ...], target: datetime
) -> float:
    for timestamp, equity in reversed(curve):
        if timestamp <= target:
            return float(equity)
    raise ValueError(f"equity curve has no observation at or before {target.isoformat()}")


def fortnight_pnl_distribution(
    result: BacktestResult,
    start: datetime,
    end: datetime,
    window_days: int,
) -> dict[str, object]:
    """Summarize consecutive complete fixed-day windows and their dollar PnL frequencies."""
    if window_days <= 0 or end <= start:
        raise ValueError("window_days and period boundaries must be positive")
    curve = tuple((timestamp, value) for timestamp, value in result.equity_curve)
    pnls: list[float] = []
    cursor = start
    step = timedelta(days=window_days)
    while cursor + step <= end:
        next_cursor = cursor + step
        pnls.append(_at_or_before(curve, next_cursor) - _at_or_before(curve, cursor))
        cursor = next_cursor
    if not pnls:
        return {"count": 0, "histogram": []}
    edges = np.histogram_bin_edges(pnls, bins="auto")
    counts, edges = np.histogram(pnls, bins=edges)
    return {
        "count": len(pnls),
        "window_days": window_days,
        "partial_tail_days": (end - cursor).total_seconds() / 86400.0,
        "median_pnl_usd": float(np.median(pnls)),
        "p10_pnl_usd": float(np.percentile(pnls, 10)),
        "p90_pnl_usd": float(np.percentile(pnls, 90)),
        "worst_pnl_usd": float(min(pnls)),
        "best_pnl_usd": float(max(pnls)),
        "positive_windows": sum(value > 0 for value in pnls),
        "negative_windows": sum(value < 0 for value in pnls),
        "zero_windows": sum(value == 0 for value in pnls),
        "histogram": [
            {
                "pnl_low_inclusive_usd": float(edges[index]),
                "pnl_high_usd": float(edges[index + 1]),
                "frequency": int(count),
            }
            for index, count in enumerate(counts)
            if count
        ],
    }


def _year_ago(value: datetime, years: int) -> datetime:
    return value.replace(year=value.year - years)


def run_matrix(
    *,
    config_path: Path,
    csv_paths: list[Path],
    pair_info_path: Path,
    symbol_map_path: Path,
    stop_loss_percentages: list[float],
    two_week_start: datetime,
) -> dict[str, object]:
    base_config = load_config(config_path)
    interval = interval_timedelta(base_config.data.interval)
    symbol_map = json.loads(symbol_map_path.read_text(encoding="utf-8"))
    panel = load_cached_panel(csv_paths, interval, symbol_map)
    pair_info: dict[str, PairInfo] = pair_info_from_json(pair_info_path)
    end = panel.end_time
    ranges = {
        "trailing_1y": (_year_ago(end, 1), end),
        "trailing_3y": (_year_ago(end, 3), end),
        "trailing_5y": (_year_ago(end, 5), end),
        "consecutive_14d_since_q3_2025": (two_week_start, end),
    }
    cases: list[dict[str, object]] = []
    total = len(stop_loss_percentages) * len(ranges)
    completed = 0
    for stop_loss_percent in stop_loss_percentages:
        if not 0 < stop_loss_percent < 100:
            raise ValueError(f"stop loss must be a percentage in (0, 100), got {stop_loss_percent}")
        risk = replace(
            base_config.risk,
            stop_loss_enabled=True,
            stop_loss_pct=stop_loss_percent / 100.0,
            stop_cooldown_hours=24,
        )
        for range_name, (report_from, range_end) in ranges.items():
            completed += 1
            print(
                f"running case {completed}/{total}: {stop_loss_percent:g}% / {range_name}",
                file=sys.stderr,
                flush=True,
            )
            backtest = replace(
                base_config.backtest,
                start=report_from - timedelta(hours=base_config.derived.warmup_bars),
                report_from=report_from,
                in_sample_end=range_end,
                holdout_start=range_end,
                window_days=14,
                step_days=14 if range_name == "consecutive_14d_since_q3_2025" else 7,
            )
            config = replace(base_config, risk=risk, backtest=backtest)
            result = run_backtest(panel, config, pair_info, backtest.start, range_end)
            performance = performance_summary(result, config)
            case: dict[str, object] = {
                "range": range_name,
                "stop_loss_percent": stop_loss_percent,
                "report_from": report_from.isoformat(),
                "in_sample_end": range_end.isoformat(),
                "stop_events": sum(bool(record.stopped_symbols) for record in result.records),
                "stop_symbols": {
                    symbol: sum(symbol in record.stopped_symbols for record in result.records)
                    for symbol in panel.symbols
                },
                "performance": performance,
            }
            if range_name == "consecutive_14d_since_q3_2025":
                case["fortnight_pnl_distribution"] = fortnight_pnl_distribution(
                    result, report_from, range_end, 14
                )
            cases.append(case)
    return {
        "as_of_utc": end.isoformat(),
        "symbols": panel.symbols,
        "bars": panel.n_rows,
        "initial_cash_usd": base_config.backtest.initial_cash,
        "stop_loss_unit": "percent below weighted-average entry price; close-price trigger",
        "fill_rule": "stop is detected at an hourly close and filled at the next hourly close",
        "cooldown_hours_after_stop": 24,
        "data_and_execution_assumptions": {
            "data_source": base_config.data.source,
            "pair_info": str(pair_info_path),
            "fee_per_side": base_config.costs.taker_fee,
            "slippage_bps_per_order": base_config.costs.slippage_bps,
            "holdout_used": False,
        },
        "case_count": len(cases),
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--pair-info", required=True, type=Path)
    parser.add_argument("--symbol-map", required=True, type=Path)
    parser.add_argument("--stop-loss-percentages", required=True, nargs="+", type=float)
    parser.add_argument("--two-week-start", required=True, type=datetime.fromisoformat)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    two_week_start = args.two_week_start
    if two_week_start.tzinfo is None:
        two_week_start = two_week_start.replace(tzinfo=UTC)
    csv_paths = sorted(args.cache_dir.glob("*.zip"))
    if not csv_paths:
        parser.error(f"no ZIP archives found in {args.cache_dir}")
    report = run_matrix(
        config_path=args.config,
        csv_paths=csv_paths,
        pair_info_path=args.pair_info,
        symbol_map_path=args.symbol_map,
        stop_loss_percentages=args.stop_loss_percentages,
        two_week_start=two_week_start,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
