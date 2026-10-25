from datetime import datetime, timedelta, timezone

import numpy as np

import detect_events as de

T0 = datetime(2026, 10, 20, tzinfo=timezone.utc)
N_ST = 40


def network(hours=96, t=10.0):
    times = [T0 + timedelta(hours=h) for h in range(hours)]
    data = {v: np.full((hours, N_ST), np.nan) for v in de.VARS}
    data["LT"][:] = t
    data["N"][:] = 0.0
    data["WG.BOE"][:] = 5.0
    stations = [{"code": f"S{i}", "name": f"Ort {i}", "name_it": f"Luogo {i}"} for i in range(N_ST)]
    return data, times, stations


def test_quiet_weather_has_no_events():
    assert de.detect(*network()) == []


def test_daily_cycle_is_not_a_cold_front():
    data, times, stations = network()
    hour = np.arange(len(times)) % 24
    data["LT"][:] = (10 + 8 * np.sin(hour / 24 * 2 * np.pi))[:, None]
    assert de.detect(data, times, stations) == []


def test_cold_front_with_rain():
    data, times, stations = network()
    data["LT"][50:] = 2.0               # 8 K colder from hour 50 on, for good
    data["LT"][50:, 3] = -4.0           # one station drops most
    data["N"][40:52] = 2.5              # 30 mm in 12 hours everywhere
    data["N"][40:52, 7] = 6.0
    (e,) = de.detect(data, times, stations)
    assert e["types"] == ["rain", "cold"]   # the rain starts first
    assert e["cold"]["median"] == -8.0
    assert e["cold"]["station"]["name"] == "Ort 3"
    assert e["rain"]["station"] == {"value": 72.0, "name": "Ort 7", "name_it": "Luogo 7"}
    assert e["start"] == "2026-10-21T23:00Z" and not e["ongoing"]


def test_storm_needs_several_stations_and_hours():
    data, times, stations = network()
    data["WG.BOE"][30, :6] = 25.0       # one hour only
    data["WG.BOE"][60:63, :4] = 25.0    # four stations only
    assert de.detect(data, times, stations) == []
    data["WG.BOE"][60:63, :6] = 25.0
    data["WG.BOE"][61, 2] = 31.0
    (e,) = de.detect(data, times, stations)
    assert e["types"] == ["storm"] and e["storm"]["stations"] == 6
    assert e["storm"]["station"]["value"] == 31.0


def test_storm_counts_against_each_stations_own_gusts():
    data, times, stations = network()
    # Summits: 25 m/s is their usual windy hour; valleys: 18 m/s is rare.
    thr = np.full(N_ST, 17.2)
    thr[:10] = 28.0
    data["WG.BOE"][60:63, :10] = 25.0
    assert de.detect(data, times, stations, thr) == []
    data["WG.BOE"][60:63, 10:15] = 18.0
    (e,) = de.detect(data, times, stations, thr)
    assert e["storm"]["stations"] == 5


def test_gust_thresholds_need_a_year():
    g = np.full((de.GUST_RECORD, 3), 5.0)
    g[:100, 0] = 30.0                    # a windy summit
    g[:, 2] = np.nan
    g[:10, 2] = 3.0                      # too short a record
    thr = de.gust_thresholds(g)
    assert thr[0] == 30.0 and thr[1] == de.GUST_FLOOR and thr[2] == de.GUST


def test_thunderstorm_cooling_one_afternoon_is_no_cold_spell():
    data, times, stations = network()
    data["LT"][60:64] = 2.0              # 8 K colder for four hours, then as before
    assert de.detect(data, times, stations) == []


def test_events_a_day_apart_stay_separate_and_newest_is_ongoing():
    data, times, stations = network(hours=120)
    data["LT"][20:24, :16] = 33.0  # a minority: no 24-hour drop of the median after
    data["LT"][100:, :16] = 33.0
    newer, older = de.detect(data, times, stations)
    assert older["types"] == newer["types"] == ["heat"]
    assert newer["ongoing"] and not older["ongoing"]
    assert newer["days"] == ["2026-10-24", "2026-10-25"]


def test_rolling_sum_needs_most_hours():
    a = np.ones((40, 1))
    a[10:15] = np.nan
    s = de.rolling_sum(a, 24, 20)
    assert np.isnan(s[22, 0]) and np.isnan(s[23, 0])  # not yet 24 hours; then 5 missing
    assert s[34, 0] == 20.0 and s[38, 0] == 24.0


def test_recent_events_replace_the_archive_after_the_cutoff():
    old = [{"id": "a", "start": "2026-06-01T10:00Z"}, {"id": "b", "start": "2026-10-01T10:00Z"}]
    new = [{"id": "b2", "start": "2026-10-01T12:00Z"}, {"id": "c", "start": "2026-10-20T10:00Z"}]
    got = de.merge(new, old, de.parse_iso("2026-07-01T00:00Z"))
    assert [e["id"] for e in got] == ["c", "b2", "a"]
