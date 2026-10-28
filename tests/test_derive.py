import numpy as np
import pytest

from meteo import derive


def test_dewpoint_and_back():
    td = derive.dewpoint(20.0, 50.0)
    assert td == pytest.approx(9.3, abs=0.1)
    assert float(derive.relative_humidity(np.array(20.0), np.array(td))) == pytest.approx(50, abs=0.5)
    assert derive.dewpoint(5.0, 100.0) == 5.0
    assert derive.dewpoint(None, 50) is None


def test_relative_humidity_is_capped():
    assert float(derive.relative_humidity(np.array(2.0), np.array(3.0))) == 100


def test_rolling_sum_needs_most_hours():
    s = [1.0] * 30
    out = derive.rolling_sum(s, 24)
    assert out[22] is None and out[23] == 24.0
    s[10:15] = [None] * 5  # 5 of 24 missing: below 90 %
    assert derive.rolling_sum(s, 24)[23] is None


def test_change():
    assert derive.change([1, 2, None, 6], 2) == [None, None, None, 4]


def test_daily_extremes():
    lo, hi = derive.daily_extremes([3, 1, 4, 7, 5, 9], [0, 0, 0, 1, 1, 1], 2)
    assert (lo, hi) == ([1, 5], [4, 9])
    # A day missing more than a fifth of its hours has no extremes.
    lo, hi = derive.daily_extremes([3, 1, 4, None, 5, 9], [0, 0, 0, 1, 1, 1], 2)
    assert (lo, hi) == ([1, None], [4, None])


def stations(lapse, t_ref, z_ref=2000, valley=None):
    """Temperatures on a column of stations from 900 to 3400 m.

    With `valley`, cold air pools below 1800 m: the temperature rises from
    `valley` at 900 m to the free-air value at 1800 m.
    """
    alt = list(range(900, 3500, 100))
    t = [t_ref - lapse * (z - z_ref) / 100 for z in alt]
    if valley is not None:
        t18 = t[alt.index(1800)]
        t = [valley + (t18 - valley) * (z - 900) / 900 if z < 1800 else v for z, v in zip(alt, t)]
    return alt, t


def test_freezing_level_plain_lapse_rate():
    assert derive.freezing_level(*stations(0.6, 6.0)) == 3000


def test_freezing_level_ignores_valley_inversion():
    # Mild aloft, cold valleys at night: a line through all stations would
    # cross 0 °C near 6700 m; the summits put it at 3000 m.
    alt, t = stations(0.6, 6.0, valley=-4.0)
    b, a = np.polyfit(alt, t, 1)
    assert -a / b > 6000
    assert derive.freezing_level(alt, t) == 3000


def test_freezing_level_below_the_summits_uses_all_stations():
    assert derive.freezing_level(*stations(0.6, -6.0)) == 1000


def test_freezing_level_none_when_too_flat_or_too_high():
    assert derive.freezing_level(*stations(0.1, 3.0)) is None
    assert derive.freezing_level(*stations(0.6, 20.0)) is None  # 5300 m
    assert derive.freezing_level([1000, 2000], [5.0, -1.0]) is None
    alt, t = stations(0.6, 6.0)
    assert derive.freezing_level(alt, [None] * len(t)) is None
