from datetime import datetime, timedelta, timezone

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


def bare_mask(parts, start_bare=False):
    """qc.snow_bare over consecutive hours given as [(hours, depth, temperature), ...]."""
    depth = [d for n, d, _ in parts for _ in range(n)]
    temp = [t for n, _, t in parts for _ in range(n)]
    return qc.snow_bare(depth, temp, range(len(depth)), start_bare)


def test_grass_on_bare_ground_counts_as_no_snow():
    # The snow cover melts to 0 cm, then the grass grows to 30 cm in the warmth,
    # the readings jumping by a few cm from hour to hour (Schnals Finail).
    grass = [(1, 10.0 + k / 10 + (3.0 if k % 2 else -3.0), 9.0) for k in range(200)]
    mask = bare_mask([(48, 40.0, 1.0), (60, 0.0, 8.0)] + grass)
    assert not mask[:48 + 48].any()            # the snow, and the bare ground until it counts as bare
    assert mask[48 + 60:].all()                # all of the grass
    # A cold night does not make it snow: the grass neither grows nor stays
    # 5 cm above the day before for three hours.
    jitter = [(1, 15.0 if k % 2 else 3.0, 1.0) for k in range(48)]
    assert bare_mask([(60, 3.0, 8.0)] + jitter, start_bare=True).all()


def test_new_snow_on_bare_ground_counts_from_where_it_starts():
    # Bare, grass at 20 cm; it turns cold and snow builds up 3 cm an hour.
    snow = [(1, 20.0 + 3 * k, -1.0) for k in range(1, 12)]
    mask = bare_mask([(48, 20.0, 9.0), (6, 20.0, 1.0)] + snow + [(48, 53.0, -2.0)], start_bare=True)
    assert mask[:54].all()
    # The first hour above the grass counts as well, though only the next ones
    # stand 5 cm above it.
    assert not mask[54:].any()
    # The same rise in the warmth is not snow.
    warm = [(1, 20.0 + 3 * k, 6.0) for k in range(1, 12)]
    assert bare_mask([(48, 20.0, 9.0)] + warm, start_bare=True).all()


def test_a_thin_snow_cover_is_bare_only_when_it_is_warm():
    # Seiser Alm Zallinger reads 8 cm on bare ground in May.
    assert bare_mask([(48, 30.0, 0.0), (48, 8.0, 6.0), (24, 15.0, 9.0)])[96:].all()
    # 8 cm of snow in the frost stay.
    assert not bare_mask([(48, 30.0, 0.0), (48, 8.0, -4.0), (24, 15.0, -4.0)]).any()
    # 5 cm or less for two days: bare, whatever the temperature.
    assert bare_mask([(48, 4.0, -4.0), (24, 15.0, 9.0)])[48:].all()


def test_bare_ground_after_a_gap():
    # Pens Tramintal: bare at the start of December 2020, back after a gap with 112 cm in the frost.
    depth = [1.0] * 48 + [112.0] * 10
    hours = list(range(48)) + list(range(400, 410))
    assert not qc.snow_bare(depth, [-6.0] * 58, hours, True)[48:].any()
    # In the warmth what comes back is not snow.
    assert qc.snow_bare(depth, [9.0] * 58, hours, True)[48:].all()
