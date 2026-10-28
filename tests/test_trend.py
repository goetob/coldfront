import numpy as np

from meteo import trend

Z = np.array([float(z) for z in range(250, 3450, 50)])  # 64 stations, 14 above 2800 m


def test_straight_line_stays_straight():
    t = 15 - 0.0065 * Z + np.random.default_rng(2).normal(0, 0.3, Z.size)
    f, hinge, top = trend.hinge_fit(Z, t)
    assert hinge is None and top is None
    assert np.max(np.abs(f(Z) - (15 - 0.0065 * Z))) < 0.3


def test_summits_leave_a_warm_layer():
    # 27 October, midnight: mild up to 2400 m, then 0.8 K per 100 m colder.
    rng = np.random.default_rng(1)
    t = np.where(Z < 2400, 5 - 0.001 * Z, 2.6 - 0.008 * (Z - 2400)) + rng.normal(0, 0.3, Z.size)
    f, _, top = trend.hinge_fit(Z, t)
    assert top is not None and 2200 <= top <= 2600
    assert abs(float(f(3300)) - (2.6 - 0.008 * 900)) < 0.5
    # A single line would put the summits several degrees too warm.
    b, a = np.polyfit(Z, t, 1)
    assert a + b * 3300 - (2.6 - 0.008 * 900) > 2


def test_no_summit_hinge_steeper_than_dry_air():
    t = np.where(Z < 2400, 5 - 0.001 * Z, 2.6 - 0.02 * (Z - 2400))
    assert trend.hinge_fit(Z, t)[2] is None


def test_no_summit_hinge_without_enough_summits():
    z = Z[Z < 2400]  # 7 stations above 2000 m
    t = np.where(z < 2000, 5 - 0.001 * z, 3 - 0.008 * (z - 2000)) + np.random.default_rng(3).normal(0, 0.3, z.size)
    assert trend.hinge_fit(z, t)[2] is None
