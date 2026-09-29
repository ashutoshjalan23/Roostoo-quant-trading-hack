"""Paper and dry-run cycle entry point."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from qtrend.config import load_config
from qtrend.engine.journal import Journal
from qtrend.engine.loop import run_cycle


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--journal", type=Path, default=Path("logs/cycles.jsonl"))
    args = parser.parse_args()
    config = load_config(args.config)
    if not args.dry_run and config.meta.mode != "hold":
        raise SystemExit("live execution requires an exchange adapter; use --dry-run or hold mode")
    args.journal.parent.mkdir(parents=True, exist_ok=True)
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "working-tree"
    result = run_cycle(
        reconcile=lambda: {"mode": config.meta.mode, "cash": config.backtest.initial_cash},
        decide=lambda state: {"mode": state["mode"], "target_weights": {}},
        plan=lambda decision: [],
        execute=lambda orders: orders,
        journal=Journal(args.journal, commit),
        data_age_seconds=0,
        max_data_age_seconds=config.safety.max_data_age_seconds,
        unexplained_equity_move=0,
        max_unexplained_equity_move=config.safety.max_unexplained_equity_move,
        portfolio_volatility=0.0,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
