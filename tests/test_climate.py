from datetime import date, timedelta

import numpy as np

from meteo import climate


def days(first, n):
    return [first + timedelta(days=i) for i in range(n)]


def test_same_date_ranks_the_calendar_day_over_the_years():
    dates = days(date(2015, 1, 1), 365 * 11 + 3)
    series = np.zeros(len(dates))
    oct25 = [i for i, d in enumerate(dates) if (d.month, d.day) == (10, 25)]
    series[oct25] = np.arange(len(oct25), dtype=float)   # every year warmer
    r = climate.same_date(series, dates, oct25[-1])
    assert r == {"warm": 1, "cold": len(oct25), "years": len(oct25), "first": 2015}
    assert climate.same_date(series, dates, oct25[2])["warm"] == len(oct25) - 2
    # Too few years: no ranking.
    assert climate.same_date(series[:365 * 3], dates[:365 * 3], oct25[1]) is None


def test_since_names_the_last_day_as_extreme():
    dates = days(date(2015, 1, 1), 4000)
    series = np.full(len(dates), 5.0)
    series[100] = 30.0
    series[3000] = 25.0
    series[3990] = 26.0
    assert climate.since(series, dates, 3990) == {"since": dates[100].isoformat()}
    assert climate.since(series, dates, 3000) == {"since": dates[100].isoformat()}
    # A day matched within the gap says nothing.
    series[3980] = 27.0
    assert climate.since(series, dates, 3990) is None
    # Highest of the whole record after more than five years.
    series[3995] = 40.0
    assert climate.since(series, dates, 3995) == {"record": True, "first": "2015-01-01"}
    assert climate.since(series, dates, 100) is None   # record, but too early to say so
    assert climate.since(-series, dates, 3995, higher=False) == {"record": True, "first": "2015-01-01"}


def test_network_temperature_does_not_depend_on_the_stations_present():
    codes = [f"S{i}" for i in range(30)]
    meta = {"period": [2015, 2025], "window_days": 15,
            "stations": {c: {"years_t": 11, "years_n": 11, "quality": "ok"} for c in codes}}
    normal = np.linspace(0, 29, 30)[None, :].repeat(366, axis=0)   # station i is i degrees warm
    values = {f: normal.copy() if f.startswith("t") else np.zeros((366, 30)) for f in climate.NORMAL_FIELDS}
    normals = climate.Normals(codes, values, meta)
    n = 10
    hist = {f: np.tile(normal[0] + 2.0, (n, 1)) for f in ("tmean", "tmin", "tmax")}
    hist["n_sum"] = np.zeros((n, 30))
    hist["tmean"][5, :8] = np.nan                     # the cold stations are missing that day
    history = climate.History(date(2026, 10, 1), codes, hist)
    net = climate.network(history, normals)
    assert np.allclose(net["tmean_anom"], 2.0)
    assert np.allclose(net["tmean"], 14.5 + 2.0)      # mean normal plus anomaly, also on day 5
    hist["tmean"][6, :15] = np.nan                    # too few stations
    assert np.isnan(climate.network(history, normals)["tmean"][6])


def test_slot_matches_build_normals():
    import build_normals as bn
    for d in (date(2015, 1, 1), date(2016, 2, 29), date(2023, 3, 1), date(2024, 12, 31)):
        assert climate.slot(d) == bn.slot(d)
