from datetime import date, datetime, timedelta, timezone

import numpy as np

import build_archive as ba
from meteo import climate
from meteo.timeutil import iso

STATIONS = [{"code": "B", "name": "Bozen", "name_it": "Bolzano", "alt": 245},
            {"code": "S", "name": "Salurn", "name_it": "Salorno", "alt": 212},
            {"code": "G", "name": "Gipfel", "name_it": "Vetta", "alt": 3000}]


def history(first, n, tmin, tmax):
    """Two stations with the given lowest and highest values on every day."""
    shape = (n, 2)
    return climate.History(first, ["A", "B"], {
        "tmin": np.broadcast_to(tmin, shape).astype(float).copy(),
        "tmax": np.broadcast_to(tmax, shape).astype(float).copy(),
        "tmean": np.zeros(shape), "n_sum": np.zeros(shape)})


def test_the_four_kinds_follow_dwd_and_meteoswiss():
    tmin = np.array([20.0, 19.9, 0.0, -0.1, np.nan])
    tmax = np.array([30.0, 29.9, 0.0, -0.1, np.nan])
    assert climate.kenntag(tmin, "tn")[0].tolist() == [True, False, False, False, False]
    assert climate.kenntag(tmax, "hd")[0].tolist() == [True, False, False, False, False]
    assert climate.kenntag(tmin, "fd")[0].tolist() == [False, False, False, True, False]
    assert climate.kenntag(tmax, "ed")[0].tolist() == [False, False, False, True, False]
    assert climate.kenntag(tmin, "fd")[1].tolist() == [True] * 4 + [False]


def test_counts_per_year_and_up_to_the_same_day():
    first = date(2015, 1, 1)
    n = (date(2017, 7, 31) - first).days + 1
    dates = [first + timedelta(days=i) for i in range(n)]
    summer = np.array([(d.month, d.day) >= (7, 1) and d.month <= 8 for d in dates])
    tmin = np.where(summer, 21.0, 5.0)[:, None]
    h = history(first, n, tmin, 25.0)
    h.values["tmin"][[i for i, d in enumerate(dates) if d.year == 2016 and d.month < 4], 1] = np.nan  # B misses a quarter
    c = climate.kenntage_years(h, dates[-1])
    assert c["years"] == [2015, 2016, 2017]
    assert c["tn"][:, 0].tolist() == [62, 62, 31]          # July and August; 2017 up to 31 July
    assert c["to_date"]["tn"][:, 0].tolist() == [31, 31, 31]
    assert np.isnan(c["tn"][1, 1])                          # too few days in 2016
    assert not np.isfinite(climate.kenntage_mean(c["tn"], c["years"], [2015, 2016])).any()  # fewer than 5 years


def test_a_year_the_archive_starts_in_is_not_counted():
    first = date(2015, 6, 1)
    n = (date(2016, 12, 31) - first).days + 1
    c = climate.kenntage_years(history(first, n, 21.0, 25.0), date(2016, 12, 31))
    assert np.isnan(c["tn"][0]).all() and c["tn"][1].tolist() == [366, 366]


def test_day_lists_tropical_nights_and_hot_days_lowest_first():
    rows = {"B": {"tmin": 20.4, "tmax": 33.0}, "S": {"tmin": 21.0, "tmax": 29.0}, "G": {"tmin": -2.0, "tmax": -0.5},
            "X": {"tmin": 25.0, "tmax": 35.0}}  # no longer in the station list
    k = ba.day_kenntage(rows, STATIONS)
    assert [s["name"] for s in k["tn"]] == ["Salurn", "Bozen"]
    assert [s["name_it"] for s in k["hd"]] == ["Bolzano"]
    assert (k["fd"], k["ed"], k["stations"]) == (1, 1, 4)


def test_today_counts_the_night_once_it_is_over():
    # 26 July 2026, local time = UTC + 2; hours ending 01:00 to 10:00 local.
    start = datetime(2026, 7, 25, 23, tzinfo=timezone.utc)
    times = [start + timedelta(hours=k) for k in range(10)]
    window = {"times": [iso(t) for t in times],
              "series": {"LT": {"B": [22.0] * 9 + [31.0], "S": [22.0] * 8 + [19.5, 24.0], "G": [None] * 10}}}
    k = ba.today_kenntage(window, STATIONS)
    assert (k["date"], k["hour"]) == ("2026-07-26", 10)
    assert [s["name"] for s in k["tn"]] == ["Bozen"] and [s["name"] for s in k["hd"]] == ["Bozen"]
    # At 08:00 the night is not over yet.
    early = {"times": window["times"][:8], "series": {"LT": {c: v[:8] for c, v in window["series"]["LT"].items()}}}
    assert ba.today_kenntage(early, STATIONS)["tn"] == []
