"""Run the pre-registered weekly long/short momentum candidate."""

from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from qtrend.backtest.long_short import run_long_short_backtest
from qtrend.backtest.simulator import BacktestResult
from qtrend.config import load_config
from qtrend.execution import PairInfo

try:
    from scripts.fetch_history import load_cached_panel
    from scripts.run_backtest import interval_timedelta, pair_info_from_json, performance_summary
    from scripts.run_stop_loss_matrix import _year_ago, fortnight_pnl_distribution
except ModuleNotFoundError:
    from fetch_history import load_cached_panel
    from run_backtest import interval_timedelta, pair_info_from_json, performance_summary
    from run_stop_loss_matrix import _year_ago, fortnight_pnl_distribution


def run_matrix(
    *,
    config_path: Path,
    cache_dir: Path,
    pair_info_path: Path,
    symbol_map_path: Path,
    two_week_start: datetime,
) -> dict[str, object]:
    base = load_config(config_path)
    symbol_map = json.loads(symbol_map_path.read_text(encoding="utf-8"))
    panel = load_cached_panel(
        sorted(cache_dir.glob("*.zip")), interval_timedelta(base.data.interval), symbol_map
    )
    pair_info: dict[str, PairInfo] = pair_info_from_json(pair_info_path)
    end = panel.end_time
    ranges = {
        "trailing_1y": (_year_ago(end, 1), end),
        "trailing_3y": (_year_ago(end, 3), end),
        "trailing_5y": (_year_ago(end, 5), end),
        "consecutive_14d_since_q3_2025": (two_week_start, end),
    }
    cases: list[dict[str, object]] = []
    for name, (report_from, range_end) in ranges.items():
        bt = replace(
            base.backtest,
            start=report_from,
            report_from=report_from,
            in_sample_end=range_end,
            holdout_start=range_end,
            window_days=14,
            step_days=14 if name.endswith("q3_2025") else 7,
        )
        config = replace(base, backtest=bt)
        curve = run_long_short_backtest(panel, config, pair_info, report_from, range_end)
        result = BacktestResult(tuple(curve), ())
        case: dict[str, object] = {
            "range": name,
            "report_from": report_from.isoformat(),
            "in_sample_end": range_end.isoformat(),
            "performance": performance_summary(result, config),
        }
        if name.endswith("q3_2025"):
            case["fortnight_pnl_distribution"] = fortnight_pnl_distribution(
                result, report_from, range_end, 14
            )
        cases.append(case)
    return {
        "strategy": "weekly cross-sectional 30-day momentum, 1-day skip, top/bottom quintile",
        "as_of_utc": end.isoformat(),
        "symbols": panel.symbols,
        "bars": panel.n_rows,
        "initial_cash_usd": base.backtest.initial_cash,
        "gross_target": (
            "1.0x before fees/slippage; target haircutted by entry costs to avoid borrowing"
        ),
        "long_count": 2,
        "short_count": 2,
        "rebalance": "Monday 00:00 UTC, next hourly close fill",
        "volatility_scale": (
            "min(1, 10% / sample std of last 8 completed weekly net returns); 1x until 8 exist"
        ),
        "short_collateral": "100% collateral; no leverage; short PnL capped at posted collateral",
        "fees_and_slippage": {
            "fee_per_side": base.costs.taker_fee,
            "slippage_bps_per_order": base.costs.slippage_bps,
        },
        "case_count": len(cases),
        "cases": cases,
        "holdout_used": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--pair-info", type=Path, required=True)
    parser.add_argument("--symbol-map", type=Path, required=True)
    parser.add_argument("--two-week-start", type=datetime.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    start = args.two_week_start
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    report = run_matrix(
        config_path=args.config,
        cache_dir=args.cache_dir,
        pair_info_path=args.pair_info,
        symbol_map_path=args.symbol_map,
        two_week_start=start,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
