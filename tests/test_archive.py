from datetime import date, datetime, timedelta, timezone

import pyarrow as pa

from meteo import archive
from meteo.timeutil import local_day_bounds


def day_tables(d, value=1.0, stations=("A", "B")):
    start, end = local_day_bounds(d)
    hours = [start + timedelta(hours=k + 1) for k in range(int((end - start).total_seconds() // 3600))]
    rows = [(s, h, "LT", value, 0) for s in stations for h in hours]
    hourly = pa.Table.from_arrays([pa.array(c) for c in zip(*rows)], schema=archive.HOURLY_SCHEMA)
    daily = pa.Table.from_pylist([{"station": s, "date": d, "tmean": value} for s in stations],
                                 schema=archive.DAILY_SCHEMA)
    return hourly, daily


def test_hours_belong_to_the_local_day_they_end_in():
    # 22:00Z on 24 October is midnight CEST: the last hour of the 24th.
    t = pa.table({"time": pa.array([datetime(2026, 10, 24, 22, tzinfo=timezone.utc),
                                    datetime(2026, 10, 24, 23, tzinfo=timezone.utc)], pa.timestamp("s", tz="UTC"))})
    assert archive._day_of_rows(t, "hourly").to_pylist() == [date(2026, 10, 24), date(2026, 10, 25)]


def test_day_and_month_files_read_alike(tmp_path):
    for k in range(1, 6):
        archive.write_day(tmp_path, date(2015, 3, k), *day_tables(date(2015, 3, k), value=k))
    before = archive.read(tmp_path, "hourly")
    archive.write_month(tmp_path, (2015, 3), fold_days=True)
    assert not list(tmp_path.glob("hourly/*/*/*.parquet")) and not list(tmp_path.glob("daily/*/*.parquet"))
    after = archive.read(tmp_path, "hourly")
    order = [("station", "ascending"), ("time", "ascending")]
    assert after.sort_by(order).equals(before.sort_by(order))
    # 29 March 2015 has 23 hours; 1–5 March all have 24.
    assert after.num_rows == 2 * 5 * 24
    assert set(archive.read_daily(tmp_path)) == {date(2015, 3, k) for k in range(1, 6)}
    # A month file counts the whole month as archived.
    assert archive.missing_days(tmp_path, (2015, 3)) == []
    assert len(archive.archived_days(tmp_path)) == 31


def test_day_file_wins_over_month_file(tmp_path):
    h, d = day_tables(date(2015, 3, 2), value=1.0)
    archive.write_month(tmp_path, (2015, 3), h, d)
    archive.write_day(tmp_path, date(2015, 3, 2), *day_tables(date(2015, 3, 2), value=7.0))
    rows = archive.read_daily(tmp_path, [date(2015, 3, 2)])[date(2015, 3, 2)]
    assert rows["A"]["tmean"] == 7.0
    assert set(archive.read(tmp_path, "hourly", [date(2015, 3, 2)])["value"].to_pylist()) == {7.0}
    # Folding keeps the corrected day.
    archive.write_month(tmp_path, (2015, 3), fold_days=True)
    assert archive.read_daily(tmp_path)[date(2015, 3, 2)]["A"]["tmean"] == 7.0


def test_partial_month_file_and_day_files(tmp_path):
    # The backfill writes a month file for the days before the first day file.
    for k in (1, 2):
        archive.write_month(tmp_path, (2026, 7), *day_tables(date(2026, 7, k), value=k))
    archive.write_day(tmp_path, date(2026, 7, 3), *day_tables(date(2026, 7, 3), value=3))
    got = archive.read_daily(tmp_path)
    assert {d: r["A"]["tmean"] for d, r in got.items()} == {date(2026, 7, k): k for k in (1, 2, 3)}
    assert archive.read(tmp_path, "hourly", [date(2026, 7, 2)]).num_rows == 48


def test_months_and_columns(tmp_path):
    assert archive.months(date(2015, 11, 5), date(2016, 2, 1)) == [(2015, 11), (2015, 12), (2016, 1), (2016, 2)]
    archive.write_day(tmp_path, date(2015, 3, 1), *day_tables(date(2015, 3, 1)))
    t = archive.read(tmp_path, "daily", columns=["station", "tmean"])
    assert t.schema.names == ["station", "date", "tmean"]
    assert archive.read(tmp_path / "empty", "hourly").num_rows == 0
