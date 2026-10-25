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
    data["LT"][50:] = 2.0               # 8 K colder from hour 50 on
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
    data["WG.BOE"][30, :5] = 25.0       # one hour only
    data["WG.BOE"][60:63, :2] = 25.0    # two stations only
    assert de.detect(data, times, stations) == []
    data["WG.BOE"][60:63, :4] = 25.0
    data["WG.BOE"][61, 2] = 31.0
    (e,) = de.detect(data, times, stations)
    assert e["types"] == ["storm"] and e["storm"]["stations"] == 4
    assert e["storm"]["station"]["value"] == 31.0


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
