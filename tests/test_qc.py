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
