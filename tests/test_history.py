"""Phase 9 history-ingestion utility tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from scripts.fetch_history import (
    build_panel,
    bulk_jobs,
    coverage_report,
    load_cached_panel,
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


def test_build_panel_does_not_fill_leading_rows_from_a_future_observation():
    rows = (
        (datetime(2024, 1, 1, 1, tzinfo=UTC), "AAA", 10.0, 100.0),
        (datetime(2024, 1, 1, 2, tzinfo=UTC), "BBB", 12.0, 120.0),
    )
    panel = build_panel(rows, timedelta(hours=1))
    view = panel.slice_to(panel.end_time)
    assert view.close("BBB")[0] != view.close("BBB")[0]  # NaN: no past close exists.
    assert view.close("BBB")[1] == 12.0
    assert view.quote_volume("BBB").tolist() == [0.0, 120.0]
    assert view.stale_mask("BBB").tolist() == [True, False]


def test_build_panel_sets_missing_bar_volume_to_zero_without_reusing_old_volume():
    rows = (
        (datetime(2024, 1, 1, 1, tzinfo=UTC), "AAA", 10.0, 100.0),
        (datetime(2024, 1, 1, 3, tzinfo=UTC), "AAA", 12.0, 120.0),
    )
    view = build_panel(rows, timedelta(hours=1)).slice_to(rows[-1][0])
    assert view.close("AAA").tolist() == [10.0, 10.0, 12.0]
    assert view.quote_volume("AAA").tolist() == [100.0, 0.0, 120.0]


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


def test_load_cached_panel_reads_verified_monthly_zip(tmp_path):
    csv_bytes = (
        b"1704067200000,100,101,99,100.5,10,1704070799999,12345\n"
        b"1704070800000,100.5,102,100,101,11,1704074399999,13579\n"
    )
    archive_path = tmp_path / "AAAUSDT-1h-2024-01.zip"
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("AAAUSDT-1h-2024-01.csv", csv_bytes)
    content = buffer.getvalue()
    archive_path.write_bytes(content)
    archive_path.with_suffix(".zip.CHECKSUM").write_text(sha256(content).hexdigest())

    panel = load_cached_panel(
        [archive_path], timedelta(hours=1), {"AAAUSDT": "AAAUSD"}
    )
    view = panel.slice_to(panel.end_time)
    assert panel.symbols == ("AAAUSD",)
    assert view.close("AAAUSD").tolist() == [100.5, 101.0]
    assert view.quote_volume("AAAUSD").tolist() == [12345.0, 13579.0]
