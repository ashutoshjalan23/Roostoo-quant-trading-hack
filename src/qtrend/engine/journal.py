"""Append-only JSONL journal for decisions and operational events."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any


class Journal:
    def __init__(self, path: Path, commit: str):
        self.path = path
        self.commit = commit

    def append(self, event: dict[str, Any]) -> None:
        record = {"commit": self.commit, **event}
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    record, sort_keys=True, separators=(",", ":"), default=_json_default
                )
                + "\n"
            )

    def read(self) -> tuple[dict[str, Any], ...]:
        if not self.path.exists():
            return ()
        with self.path.open(encoding="utf-8") as stream:
            return tuple(json.loads(line) for line in stream if line.strip())


def _json_default(value: object) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"journal value is not JSON serializable: {type(value).__name__}")
