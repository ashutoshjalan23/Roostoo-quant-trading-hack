"""Phase 9 history-ingestion utility tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

from scripts.fetch_history import (
    build_panel,
    bulk_jobs,
    coverage_report,
    month_periods,
    parse_kline_csv,
    snap_close_time,
    timestamp_unit,
    verify_sha256,
)


def test_timestamp_units_and_close_snapping():
    assert timestamp_unit(1_700_000_000_000) == "ms"
    assert timestamp_unit(1_700_000_000_000_000) == "us"
    value = datetime(2024, 1, 1, 1, 59, 59, tzinfo=UTC)
    assert snap_close_time(value, timedelta(hours=1)) == datetime(2024, 1, 1, 2, tzinfo=UTC)


def test_parser_handles_headerless_rows_and_uses_quote_volume():
    content = "1704067200000,100,101,99,100.5,10,1704070799999,12345\n"
    rows = parse_kline_csv(content, "BTCUSD", timedelta(hours=1))
    assert rows[0][1:] == ("BTCUSD", 100.5, 12345.0)


def test_checksum_is_verified():
    content = b"history"
    verify_sha256(content, sha256(content).hexdigest())


def test_build_panel_forward_fills_gaps_and_marks_them_stale():
    rows = (
        (datetime(2024, 1, 1, 1, tzinfo=UTC), "AAA", 10.0, 100.0),
        (datetime(2024, 1, 1, 3, tzinfo=UTC), "AAA", 12.0, 120.0),
    )
    panel = build_panel(rows, timedelta(hours=1))
    view = panel.slice_to(panel.end_time)
    assert view.close("AAA").tolist() == [10.0, 10.0, 12.0]
    assert view.stale_mask("AAA").tolist() == [False, True, False]


def test_build_panel_seeds_leading_rows_from_first_observation():
    rows = (
        (datetime(2024, 1, 1, 1, tzinfo=UTC), "AAA", 10.0, 100.0),
        (datetime(2024, 1, 1, 2, tzinfo=UTC), "BBB", 12.0, 120.0),
    )
    panel = build_panel(rows, timedelta(hours=1))
    view = panel.slice_to(panel.end_time)
    assert view.close("BBB").tolist() == [12.0, 12.0]
    assert view.stale_mask("BBB").tolist() == [True, False]


def test_bulk_jobs_and_coverage_are_deterministic():
    start = datetime(2024, 1, 15, tzinfo=UTC)
    end = datetime(2024, 3, 2, tzinfo=UTC)
    assert month_periods(start, end) == ((2024, 1), (2024, 2), (2024, 3))
    jobs = bulk_jobs(
        "https://example/{symbol}-{year}-{month}.csv", ("BBB", "AAA"), ((2024, 2),),
        Path("cache"),
    )
    assert jobs[0][0].endswith("AAA-2024-02.csv")
    rows = ((datetime(2024, 1, 1, 1, tzinfo=UTC), "AAA", 1.0, 1.0),)
    report = coverage_report(
        build_panel(rows, timedelta(hours=1)), start=rows[0][0], end=rows[0][0]
    )
    assert report == {"expected_bars": 1, "observed_bars": 1, "complete": True}
