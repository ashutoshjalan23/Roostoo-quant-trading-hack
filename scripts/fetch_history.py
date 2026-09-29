"""Credential-free historical kline ingestion helpers and command-line entry point."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests

from qtrend.data.panel import Panel


def timestamp_unit(value: int) -> str:
    """Infer a raw timestamp unit independently for each input file."""
    magnitude = abs(value)
    if magnitude >= 100_000_000_000_000:
        return "us"
    if magnitude >= 100_000_000_000:
        return "ms"
    raise ValueError(f"timestamp magnitude is not milliseconds or microseconds: {value}")


def parse_timestamp(value: int) -> datetime:
    unit = timestamp_unit(value)
    seconds = value / (1_000_000 if unit == "us" else 1_000)
    return datetime.fromtimestamp(seconds, tz=UTC)


def snap_close_time(value: datetime, interval: timedelta) -> datetime:
    """Snap a raw close timestamp upward to the containing grid close."""
    if interval <= timedelta(0):
        raise ValueError("interval must be positive")
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    elapsed = value.astimezone(UTC) - epoch
    units = elapsed // interval
    boundary = epoch + units * interval
    return boundary if boundary == value.astimezone(UTC) else boundary + interval


def _header_map(row: list[str]) -> dict[str, int] | None:
    normalized = {cell.strip().lower(): index for index, cell in enumerate(row)}
    if "open time" in normalized or "open_time" in normalized:
        return normalized
    return None


def parse_kline_csv(
    content: str | bytes,
    symbol: str,
    interval: timedelta,
) -> tuple[tuple[datetime, str, float, float], ...]:
    """Parse CSV rows as `(close_time, symbol, close, quote_volume)` tuples."""
    text = content.decode("utf-8") if isinstance(content, bytes) else content
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return ()
    header = _header_map(rows[0])
    data = rows[1:] if header else rows
    if header:
        close_index = header.get("close", 4)
        volume_index = header.get("quote asset volume", header.get("quote_volume", 7))
        raw_close_index = header.get("close time", header.get("close_time", 6))
    else:
        close_index, volume_index, raw_close_index = 4, 7, 6
    parsed: list[tuple[datetime, str, float, float]] = []
    for row in data:
        if not row:
            continue
        close_time = parse_timestamp(int(float(row[raw_close_index])))
        parsed.append(
            (
                snap_close_time(close_time, interval),
                symbol,
                float(row[close_index]),
                float(row[volume_index]),
            )
        )
    return tuple(parsed)


def verify_sha256(content: bytes, expected: str) -> None:
    actual = hashlib.sha256(content).hexdigest()
    if actual.lower() != expected.strip().split()[0].lower():
        raise ValueError(f"SHA-256 mismatch: expected {expected.strip().split()[0]}, got {actual}")


def download_verified(url: str, checksum_url: str, destination: Path) -> Path:
    """Download one file and its sidecar, verifying bytes before caching them."""
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    checksum = requests.get(checksum_url, timeout=60)
    checksum.raise_for_status()
    verify_sha256(response.content, checksum.text)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(response.content)
    destination.with_suffix(destination.suffix + ".CHECKSUM").write_text(
        checksum.text, encoding="utf-8"
    )
    return destination


def download_periods(
    jobs: list[tuple[str, str, Path]],
    workers: int,
) -> tuple[Path, ...]:
    """Download jobs concurrently; return successes and report failures without raising."""
    if workers < 1:
        raise ValueError("workers must be positive")
    completed: list[Path] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(download_verified, url, checksum_url, destination): destination
            for url, checksum_url, destination in jobs
        }
        for future in as_completed(futures):
            destination = futures[future]
            try:
                completed.append(future.result())
            except (OSError, requests.RequestException, ValueError) as error:
                print(f"missing period {destination.name}: {error}")
    return tuple(sorted(completed))


def month_periods(start: datetime, end: datetime) -> tuple[tuple[int, int], ...]:
    """Return inclusive UTC calendar months intersecting the requested window."""
    if end < start:
        raise ValueError("end must not precede start")
    cursor = datetime(start.year, start.month, 1, tzinfo=UTC)
    final = datetime(end.year, end.month, 1, tzinfo=UTC)
    periods: list[tuple[int, int]] = []
    while cursor <= final:
        periods.append((cursor.year, cursor.month))
        cursor = (
            datetime(cursor.year + 1, 1, 1, tzinfo=UTC)
            if cursor.month == 12
            else datetime(cursor.year, cursor.month + 1, 1, tzinfo=UTC)
        )
    return tuple(periods)


def bulk_jobs(
    url_template: str,
    symbols: Iterable[str],
    periods: Iterable[tuple[int, int]],
    cache_dir: Path,
) -> tuple[tuple[str, str, Path], ...]:
    """Build deterministic public bulk-file download jobs."""
    jobs = []
    for symbol in sorted(set(symbols)):
        for year, month in sorted(set(periods)):
            filename = f"{symbol}-{year:04d}-{month:02d}.csv"
            url = url_template.format(symbol=symbol, year=year, month=f"{month:02d}")
            jobs.append((url, url + ".CHECKSUM", cache_dir / filename))
    return tuple(jobs)


def coverage_report(panel: Panel, start: datetime, end: datetime) -> dict[str, int | bool]:
    """Report expected versus observed hourly grid bars for a loaded panel."""
    if end < start:
        raise ValueError("end must not precede start")
    expected = int((end - start).total_seconds() // panel.step.total_seconds()) + 1
    observed = len(panel.timestamps_between(start, end))
    return {"expected_bars": expected, "observed_bars": observed, "complete": expected == observed}


def build_panel(
    rows: tuple[tuple[datetime, str, float, float], ...],
    interval: timedelta,
) -> Panel:
    """Build a close-time indexed, forward-filled Panel from parsed kline rows."""
    if not rows:
        raise ValueError("cannot build a panel from no rows")
    symbols = tuple(sorted({row[1] for row in rows}))
    first = min(row[0] for row in rows)
    last = max(row[0] for row in rows)
    timestamps: list[datetime] = []
    cursor = first
    while cursor <= last:
        timestamps.append(cursor)
        cursor += interval
    position = {timestamp: index for index, timestamp in enumerate(timestamps)}
    close = [[float("nan")] * len(symbols) for _ in timestamps]
    volume = [[float("nan")] * len(symbols) for _ in timestamps]
    stale = [[True] * len(symbols) for _ in timestamps]
    column = {symbol: index for index, symbol in enumerate(symbols)}
    for timestamp, symbol, price, quote_volume in rows:
        index = position.get(timestamp)
        if index is None:
            continue
        close[index][column[symbol]] = price
        volume[index][column[symbol]] = quote_volume
        stale[index][column[symbol]] = False
    for symbol_index in range(len(symbols)):
        last_price: float | None = None
        for row_index in range(len(timestamps)):
            if stale[row_index][symbol_index]:
                # A gap can only carry information already observed. Leading rows have no
                # prior close, so leave their price unknown; missing bars have no volume.
                close[row_index][symbol_index] = (
                    float("nan") if last_price is None else last_price
                )
                volume[row_index][symbol_index] = 0.0
            else:
                last_price = close[row_index][symbol_index]
    return Panel(
        index=timestamps,
        close=close,
        quote_volume=volume,
        stale=stale,
        symbols=symbols,
    )


def load_cached_panel(paths: Iterable[Path], interval: timedelta) -> Panel:
    """Parse cached CSV files and assemble one deterministic panel."""
    rows: list[tuple[datetime, str, float, float]] = []
    for path in sorted(paths):
        checksum_path = path.with_suffix(path.suffix + ".CHECKSUM")
        if checksum_path.exists():
            verify_sha256(path.read_bytes(), checksum_path.read_text(encoding="utf-8"))
        symbol = path.stem.split("-")[0]
        rows.extend(parse_kline_csv(path.read_bytes(), symbol, interval))
    return build_panel(tuple(rows), interval)


def load_pair_info(path: Path) -> dict[str, dict[str, str]]:
    """Read exchange-fetched pair metadata without embedding exchange figures."""
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("pair metadata must be a JSON object")
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, action="append", help="CSV file to parse")
    parser.add_argument("--url-template", help="Public bulk URL with {symbol}, {year}, {month}")
    parser.add_argument("--symbols", nargs="+", help="Symbols for bulk download")
    parser.add_argument("--start", type=datetime.fromisoformat)
    parser.add_argument("--end", type=datetime.fromisoformat)
    parser.add_argument("--cache-dir", type=Path, default=Path("data/cache"))
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--symbol", default="BTCUSD")
    args = parser.parse_args()
    if args.url_template:
        if not args.symbols or args.start is None or args.end is None:
            parser.error("bulk download requires --symbols, --start and --end")
        start = args.start.replace(tzinfo=UTC) if args.start.tzinfo is None else args.start
        end = args.end.replace(tzinfo=UTC) if args.end.tzinfo is None else args.end
        jobs = bulk_jobs(args.url_template, args.symbols, month_periods(start, end), args.cache_dir)
        completed = download_periods(list(jobs), args.workers)
        print(f"downloaded {len(completed)} of {len(jobs)} periods")
        return 0
    if not args.input:
        parser.error("provide --input or --url-template")
    panel = load_cached_panel(args.input, timedelta(hours=1))
    print(f"parsed {panel.n_rows} grid bars for {len(panel.symbols)} symbols")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
