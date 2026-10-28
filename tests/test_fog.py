from datetime import datetime
from types import SimpleNamespace

import numpy as np
from rasterio.transform import Affine

from meteo import fog


def test_sun_at_noon_on_the_winter_solstice():
    # Bozen, 21 December: the sun culminates at about 11:13 UTC, 90 - 46.5 - 23.44 degrees high.
    el, az = fog.sun_position(46.5, 11.35, datetime(2025, 12, 21, 11, 13))
    assert abs(el - 20.06) < 0.3 and abs(az - 180) < 1.5
    el, _ = fog.sun_position(46.5, 11.35, datetime(2025, 12, 21, 18, 0))
    assert el < 0


def test_horizon_of_a_wall_to_the_south():
    # Flat land at 500 m, a 2000 m wall from 2 km south of the station on.
    dem = np.full((200, 100), 500.0)
    dem[120:, :] = 2000.0  # rows grow southwards, 100 m each; the station is at row 100
    terrain = SimpleNamespace(dem=dem, shape=dem.shape, transform=Affine(100, 0, 0, 0, -100, 20000))
    hz = fog.horizon(terrain, 5050, 20000 - 10050, 500)
    assert abs(hz[180 // fog.AZ_STEP] - np.degrees(np.arctan(1500 / 1950))) < 1.5
    assert abs(hz[0]) < 0.5  # north: open


STATIONS = [
    {"code": "V", "name": "Tal", "alt": 250, "x": 0, "y": 0, "lat": 46.5, "lon": 11.35},
    {"code": "H", "name": "Hang", "alt": 1200, "x": 8000, "y": 0, "lat": 46.5, "lon": 11.45},
]
# Noon hours of 9 December 2025, in UTC.
TIMES = ["2025-12-09T10:00Z", "2025-12-09T11:00Z", "2025-12-09T12:00Z"]
OPEN = {"V": np.zeros(180), "H": np.zeros(180)}


def series(**over):
    s = {"SD": {"V": [0, 0, 0], "H": [60, 60, 60]},
         "LF": {"V": [99, 99, 99], "H": [55, 55, 55]},
         "LT": {"V": [2, 2, 2], "H": [6, 6, 6]},
         "N": {"V": [0, 0, 0], "H": [0, 0, 0]}}
    for key, value in over.items():
        sensor, code = key.split("_")
        s[sensor][code] = value
    return s


def test_fog_in_the_valley_under_sun_on_the_slope():
    flags, tops = fog.detect(STATIONS, TIMES, series(), OPEN)
    assert flags["V"] == [True, True, True] and not any(flags["H"])
    assert tops == [(250, 1200)] * 3


def test_fog_needs_two_hours():
    flags, _ = fog.detect(STATIONS, TIMES, series(SD_V=[0, 30, 30]), OPEN)
    assert not any(flags["V"])


def test_no_fog_without_sun_above_or_with_rain():
    assert not any(fog.detect(STATIONS, TIMES, series(SD_H=[0, 0, 0]), OPEN)[0]["V"])
    assert not any(fog.detect(STATIONS, TIMES, series(N_V=[0.4, 0.2, 0.2]), OPEN)[0]["V"])
    assert not any(fog.detect(STATIONS, TIMES, series(LF_V=[80, 80, 80]), OPEN)[0]["V"])


def test_no_fog_where_the_mountains_hide_the_sun():
    shaded = {"V": np.full(180, 40.0), "H": np.zeros(180)}
    assert not any(fog.detect(STATIONS, TIMES, series(), shaded)[0]["V"])


def test_no_fog_when_the_air_above_is_cold():
    # 950 m higher and 8 K colder: steeper than the normal lapse rate, no fog sea.
    assert not any(fog.detect(STATIONS, TIMES, series(LT_H=[-6, -6, -6]), OPEN)[0]["V"])
