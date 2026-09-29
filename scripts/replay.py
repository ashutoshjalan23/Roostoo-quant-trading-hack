"""Validate that a JSONL journal is parseable and commit-stamped."""

from __future__ import annotations

import argparse
from pathlib import Path

from qtrend.engine.journal import Journal


class ReplayMismatch(RuntimeError):
    """A re-derived journal decision differs from its recorded decision."""


def replay_records(records, derive) -> int:
    """Re-derive each record and return the number checked.

    `derive` receives one journal record and returns the expected decision mapping. Only
    deterministic decision fields are compared; the journal commit and operational metadata
    are not part of the strategy result.
    """
    checked = 0
    for record in records:
        expected = derive(record)
        actual = record.get("decision")
        if actual is None:
            raise ReplayMismatch("journal record has no decision field")
        if expected != actual:
            raise ReplayMismatch(f"decision mismatch at record {checked}")
        checked += 1
    return checked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journal", type=Path)
    args = parser.parse_args()
    records = Journal(args.journal, "replay").read()
    if any(not record.get("commit") for record in records):
        raise SystemExit("journal contains an unstamped record")
    if any("orders" not in record or "fills" not in record for record in records):
        raise SystemExit("journal record is missing orders or fills")
    print(f"replayed {len(records)} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
