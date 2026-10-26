from datetime import date, timedelta

import pyarrow as pa

import recheck as rg
from meteo import archive
from meteo.timeutil import local_day_bounds


def month_with_a_fault(root, bad=82.8):
    """Two days of 12 stations with gusts of 8 m/s and one faulty gust."""
    rows, daily = [], []
    for d in (date(2018, 10, 28), date(2018, 10, 29)):
        start, end = local_day_bounds(d)
        hours = [start + timedelta(hours=k + 1) for k in range(int((end - start).total_seconds() // 3600))]
        for j in range(12):
            code = f"S{j}"
            for h in hours:
                rows.append((code, h, "WG", 3.0, 0))
                v = bad if (code == "S0" and d.day == 29 and h == hours[10]) else 8.0
                rows.append((code, h, "WG.BOE", v, 0))
            daily.append({"station": code, "date": d, "gust_max": bad if (code == "S0" and d.day == 29) else 8.0})
    hourly = pa.Table.from_arrays([pa.array(c) for c in zip(*rows)], schema=archive.HOURLY_SCHEMA)
    archive.write_month(root, (2018, 10), hourly, pa.Table.from_pylist(daily, schema=archive.DAILY_SCHEMA))


def test_recheck_removes_the_gust_and_corrects_the_day(tmp_path):
    month_with_a_fault(tmp_path)
    h, d = archive.month_path(tmp_path, "hourly", (2018, 10)), archive.month_path(tmp_path, "daily", (2018, 10))
    assert rg.recheck(h, d) == 1
    rows = archive.read_daily(tmp_path)
    assert rows[date(2018, 10, 29)]["S0"]["gust_max"] == 8.0
    assert rows[date(2018, 10, 28)]["S1"]["gust_max"] == 8.0
    gusts = archive.read(tmp_path, "hourly").filter(pa.compute.equal(pa.compute.field("var"), "WG.BOE"))
    assert max(gusts["value"].to_pylist()) == 8.0
    # Applied again, nothing changes.
    assert rg.recheck(h, d) == 0


def test_recheck_leaves_clean_files_alone(tmp_path):
    month_with_a_fault(tmp_path, bad=8.0)
    h = archive.month_path(tmp_path, "hourly", (2018, 10))
    before = h.stat().st_mtime_ns
    assert rg.recheck(h, archive.month_path(tmp_path, "daily", (2018, 10))) == 0
    assert h.stat().st_mtime_ns == before


def test_recheck_removes_a_snow_spike_and_corrects_the_day(tmp_path):
    d = date(2018, 10, 30)
    start, end = local_day_bounds(d)
    hours = [start + timedelta(hours=k + 1) for k in range(24)]
    snow = [0.0] * 24
    snow[7] = 123.1                       # Seiser Alm Zallinger in the rain of Vaia
    rows = [("Z", h, "HS", v, 0) for h, v in zip(hours, snow)]
    rows += [("Y", h, "HS", 40.0 + k, 0) for k, h in enumerate(hours)]   # a real snowfall, 23 cm
    hourly = pa.Table.from_arrays([pa.array(c) for c in zip(*rows)], schema=archive.HOURLY_SCHEMA)
    daily = pa.Table.from_pylist([{"station": "Z", "date": d, "hs_max": 123.0, "hs_end": 0.0},
                                  {"station": "Y", "date": d, "hs_max": 63.0, "hs_end": 63.0}],
                                 schema=archive.DAILY_SCHEMA)
    archive.write_day(tmp_path, d, hourly, daily)
    assert rg.recheck(archive.day_path(tmp_path, "hourly", d), archive.day_path(tmp_path, "daily", d)) == 1
    rows = archive.read_daily(tmp_path)[d]
    assert (rows["Z"]["hs_max"], rows["Z"]["hs_end"]) == (0.0, 0.0)
    assert (rows["Y"]["hs_max"], rows["Y"]["hs_end"]) == (63.0, 63.0)
