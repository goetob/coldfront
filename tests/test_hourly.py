from datetime import datetime, timedelta, timezone

import pytest

from meteo import hourly


def series(start, values, step=10):
    return [(start + timedelta(minutes=step * i), v) for i, v in enumerate(values)]


T0 = datetime(2026, 10, 23, 12, 10, tzinfo=timezone.utc)  # first sample of the hour ending 13:00
H = datetime(2026, 10, 23, 13, tzinfo=timezone.utc)


def test_precipitation_sums_the_hour_ending_at_the_full_hour():
    out = hourly.aggregate("N", series(T0, [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 9.9]))
    assert out[H] == pytest.approx(2.1)


def test_precipitation_with_gaps_is_missing_not_dry():
    rows = series(T0 - timedelta(hours=1), [0.0] * 12)  # a complete hour, then a gappy one
    del rows[7:10]
    out = hourly.aggregate("N", rows)
    assert out[H - timedelta(hours=1)] == 0
    assert H not in out


def test_temperature_takes_the_value_at_the_full_hour():
    out = hourly.aggregate("LT", series(T0, [1, 2, 3, 4, 5, 6]))
    assert out[H] == 6


def test_snow_depth_below_zero_reads_as_bare_ground():
    out = hourly.aggregate("HS", series(T0, [-3.0] * 6))
    assert out[H] == 0


def test_sunshine_in_minutes():
    out = hourly.aggregate("SD", series(T0, [600] * 6))
    assert out[H] == 60


def test_gust_is_the_maximum():
    out = hourly.aggregate("WG.BOE", series(T0, [3, 9, 4, 5, 6, 2]))
    assert out[H] == 9


def test_wind_direction_around_north_stays_north():
    speeds = hourly.aggregate("WG", series(T0, [4.0] * 6))
    dirs = hourly.aggregate("WR", series(T0, [350, 10, 350, 10, 350, 10]))
    speed, direction = hourly.wind_mean(speeds, dirs)[H]
    assert speed == 4.0
    assert direction in (0, 360) or direction == pytest.approx(0, abs=1)


def test_wind_pairs_samples_by_time():
    speeds = hourly.aggregate("WG", series(T0, [2.0] * 6))
    dirs = hourly.aggregate("WR", series(T0, [90] * 5))  # one direction sample missing
    assert hourly.wind_mean(speeds, dirs)[H] == [2.0, 90]


def test_build_merges_wind_and_counts_flags():
    raw = {
        ("A", "LT"): series(T0, [5, 5.1, 30, 5.2, 5.3, 5.4]),  # spike
        ("A", "WG"): series(T0, [1.0] * 6),
        ("A", "WR"): series(T0, [180] * 6),
    }
    per_sensor, flagged = hourly.build(raw)
    assert set(per_sensor) == {"LT", "WIND"}
    assert per_sensor["WIND"]["A"][H] == [1.0, 180]
    assert flagged["LT"]["A"] == {H: 1}
