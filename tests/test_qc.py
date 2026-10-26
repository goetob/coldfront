from datetime import datetime, timedelta, timezone

import numpy as np

from meteo import qc

T0 = datetime(2026, 10, 23, tzinfo=timezone.utc)


def series(values, step=10):
    return [(T0 + timedelta(minutes=step * i), v) for i, v in enumerate(values)]


def test_range():
    good, bad = qc.check("LT", series([5, 60, 6]))
    assert [v for _, v in good] == [5, 6]
    assert bad.tolist() == [False, True, False]


def test_spike_only_when_it_returns():
    _, bad = qc.check("LT", series([5, 5, 20, 5, 5]))
    assert bad.tolist() == [False, False, True, False, False]
    # A real step (front passage) is kept.
    _, bad = qc.check("LT", series([15, 15, 5, 5, 5]))
    assert not bad.any()


def test_stuck_temperature():
    _, bad = qc.check("LT", series([3.0] * 80))  # > 12 h
    assert bad.all()
    _, bad = qc.check("LT", series([3.0] * 30))
    assert not bad.any()


def test_saturated_humidity_and_calm_are_not_stuck():
    _, bad = qc.check("LF", series([100.0] * 100))
    assert not bad.any()
    _, bad = qc.check("WG", series([0.0] * 100))
    assert not bad.any()
    _, bad = qc.check("WG", series([2.3] * 40))  # frozen anemometer, > 6 h
    assert bad.all()


def test_snow_depth_tolerates_small_negative_values():
    _, bad = qc.check("HS", series([-5.0, -40.0]))
    assert bad.tolist() == [False, True]


def network_gusts(n=12, gust=10.0, mean=4.0):
    import numpy as np
    return np.full((1, n), gust), np.full((1, n), mean)


def test_gust_far_above_its_mean_wind_is_dropped():
    g, m = network_gusts()
    g[0, 0], m[0, 0] = 95.0, 0.4        # Pens Tramintal, July 2018
    g[0, 1], m[0, 1] = 50.0, 25.0       # a real summit storm
    g[0, 2], m[0, 2] = 28.0, 2.0        # a thunderstorm gust in a calm hour
    assert qc.gust_outliers(g, m)[0].tolist()[:3] == [True, False, False]


def test_gust_alone_in_the_network_is_dropped_even_with_a_second_fault():
    import numpy as np
    g, m = network_gusts()
    m[:] = np.nan                       # no mean wind to compare with
    g[0, :2] = [90.0, 83.0]             # two faulty sensors in the same hour
    assert qc.gust_outliers(g, m)[0, :2].all()
    g[0, :5] = [52.0, 48.0, 47.0, 40.0, 39.0]   # Vaia: many stations high
    assert not qc.gust_outliers(g, m).any()
    # Too few stations to judge the network; the range still applies.
    assert not qc.gust_outliers(g[:, :4] * 0 + [[60, 10, 10, 10]], m[:, :4]).any()
    assert qc.gust_outliers(np.array([[80.0, 10.0]]), np.array([[np.nan, np.nan]]))[0, 0]


def test_snow_spikes_come_and_go_snowfall_stays():
    import numpy as np
    assert qc.snow_spikes([0, 0, 0, 123.1, 0, 0, 0]).tolist() == [False] * 3 + [True] + [False] * 3
    assert qc.snow_spikes([107, 103, 0, 0, 101, 101]).tolist() == [False, False, True, True, False, False]
    # Heavy snow builds up and stays; it is never a spike, however fast.
    assert not qc.snow_spikes([10, 18, 27, 36, 44, 50, 50, 49, 48]).any()
    # A lasting step is kept: which side is right cannot be told.
    assert not qc.snow_spikes([0, 0, 0, 397, 394, 397, 389]).any()
    # Four hours is no longer a spike; gaps are skipped.
    assert not qc.snow_spikes([10, 60, 60, 60, 60, 10]).any()
    assert qc.snow_spikes([10, np.nan, 10, 60, np.nan, 10]).tolist() == [False, False, False, True, False, False]


def test_snow_cannot_build_up_when_it_is_warm():
    depth = [0, 0, 218, 218, 217, 218, 0, 0]          # Seiser Alm Zallinger, July 2018
    assert qc.snow_outliers(depth, [12.0] * 8).tolist() == [False, False] + [True] * 4 + [False, False]
    # The same step in frost is kept: a sensor back from a drop-out, perhaps.
    assert not qc.snow_outliers([0, 0, 397, 394, 397, 389], [-5.7] * 6).any()
    # Heavy snowfall near freezing, 10 cm an hour, is kept.
    assert not qc.snow_outliers([40, 50, 60, 70, 80, 80], [0.5] * 6).any()
    # Without a temperature nothing is decided on warmth.
    assert not qc.snow_outliers([0, 0, 218, 218], [float("nan")] * 4).any()


def network_temps(hours=120, n=60, seed=0):
    """Hourly temperatures of n stations (about as many as the network has) from 200 to 3200 m on a lapse rate, with a daily cycle."""
    rng = np.random.default_rng(seed)
    alt = np.linspace(200, 3200, n)
    cycle = 5 * np.sin(np.arange(hours) * 2 * np.pi / 24)[:, None]
    return 25 - 0.0065 * alt[None, :] + cycle + rng.normal(0, 0.7, (hours, n)), alt


def test_a_station_far_too_warm_for_days_is_dropped():
    t, alt = network_temps()
    t[30:80, -2] += 18                    # Prettau Lengspitze, summer 2022
    bad = qc.temp_outliers(t, alt)
    assert bad[30:80, -2].all() and not bad[:30, -2].any() and not bad[80:, -2].any()
    assert bad[:, :-2].sum() == 0


def test_a_drifting_station_goes_from_where_it_left_the_others():
    t, alt = network_temps()
    t[20:30, 20] += 7                     # Sarnthein, autumn 2015: drifting off, then 10 K
    t[30:70, 20] += 11
    bad = qc.temp_outliers(t, alt)
    assert bad[20:70, 20].all() and not bad[:20, 20].any() and not bad[70:, 20].any()


def test_single_hours_far_off_the_trend_are_dropped():
    t, alt = network_temps()
    t[50, 3] = -39.3                      # Gargazon, April 2025
    t[70, 12] += 20
    bad = qc.temp_outliers(t, alt)
    assert np.argwhere(bad).tolist() == [[50, 3], [70, 12]]


def test_cold_pools_and_foehn_stay():
    t, alt = network_temps()
    t[10:60, 24] -= 11                    # Pfitsch St. Jakob in January 2017
    t[70:82, 10] += 12                    # foehn breaking through in Schlanders
    t[90:100, 30] -= 12
    assert not qc.temp_outliers(t, alt).any()


def test_a_summit_far_too_cold_for_a_day_is_dropped():
    t, alt = network_temps()
    t[40:70, -1] -= 14.5
    bad = qc.temp_outliers(t, alt)
    assert bad[40:70, -1].all() and bad.sum() == 30


def test_frozen_temperatures_with_noise_are_dropped_except_near_zero():
    t, alt = network_temps()
    t -= 15                                            # winter: 0 °C at about 1500 m
    t[20:50, 50] = -13.3 - 0.1 * (np.arange(30) % 2)   # Graun Elferspitze, March 2017
    t[20:50, 25] = 0.1 * (np.arange(30) % 3)           # wet snow at 0 °C
    bad = qc.temp_outliers(t, alt)
    assert bad[20:50, 50].all() and not bad[:, 25].any()


def test_without_elevations_only_frozen_values_are_checked():
    t, alt = network_temps()
    t[30:80, -2] += 18
    assert not qc.temp_outliers(t, np.full(len(alt), np.nan)).any()
