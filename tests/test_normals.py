from datetime import date, timedelta

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

import build_normals as bn
from meteo import archive


def daily(years, stations=("A",), seasonal=10.0, rain_every=4):
    rows = []
    for y in years:
        d = date(y, 1, 1)
        while d.year == y:
            k = d.timetuple().tm_yday
            t = 8 - seasonal * np.cos(2 * np.pi * (k - 15) / 365)
            for s in stations:
                rows.append({"station": s, "date": d, "tmean": t, "tmin": t - 5, "tmax": t + 5,
                             "n_sum": 8.0 if k % rain_every == 0 else 0.0})
            d += timedelta(days=1)
    return pa.Table.from_pylist(rows, schema=archive.DAILY_SCHEMA)


def test_slots_keep_february_29():
    assert bn.slot(date(2016, 2, 29)) == 59
    assert bn.slot(date(2015, 3, 1)) == bn.slot(date(2016, 3, 1)) == 60
    assert bn.slot(date(2015, 12, 31)) == 365


def test_reference_period_is_complete_years():
    days = {date(2015, 1, 1), date(2026, 10, 24)}
    assert bn.reference_years(days, date(2026, 10, 25)) == list(range(2015, 2026))
    assert bn.reference_years({date(2026, 7, 26)}, date(2026, 10, 25)) == []


def test_normals_follow_the_season():
    years = list(range(2015, 2026))
    table, meta = bn.build(daily(years), years)
    assert meta["A"] == {"years_t": 11, "years_n": 11, "quality": "ok"}
    rows = {r["doy"]: r for r in table.to_pylist()}
    assert len(rows) == 366
    jan, jul = rows[bn.slot(date(2001, 1, 15)) + 1], rows[bn.slot(date(2001, 7, 15)) + 1]
    # The ±15-day window flattens the extremes of the cosine only a little.
    assert -2.1 < jan["tmean"] < -1.6 and 17.6 < jul["tmean"] < 18.1
    assert abs(jul["tmax"] - jul["tmean"] - 5) < 1e-3
    assert jul["tmean_p10"] < jul["tmean"] < jul["tmean_p90"]
    assert abs(jul["n_freq"] - 0.25) < 0.03
    assert abs(jul["n_mean"] - 2.0) < 0.3
    assert jul["n_wet_p90"] == 8.0


def test_short_stations_are_marked():
    years = list(range(2015, 2026))
    t = pa.concat_tables([daily(years, ("A",)), daily(years[-6:], ("B",)), daily(years[-3:], ("C",))])
    table, meta = bn.build(t, years)
    assert [meta[s]["quality"] for s in "ABC"] == ["ok", "short", "none"]
    assert set(table["station"].to_pylist()) == {"A", "B"}


def test_one_month_a_year_is_not_a_year_of_data(tmp_path):
    years = [2015, 2016, 2017, 2018, 2019]
    for y in years:
        t = daily([y])
        archive.write_month(tmp_path, (y, 1), None, t.filter(pc.less(t["date"], pa.scalar(date(y, 2, 1)))))
    normals, meta = bn.build(archive.read(tmp_path, "daily"), years)
    assert meta["A"]["quality"] == "none" and normals.num_rows == 0
