from types import SimpleNamespace

import numpy as np
from rasterio.transform import Affine

from meteo import wind


def test_strength_classes_follow_beaufort():
    assert wind.strength_class(0.4) == 0
    assert wind.strength_class(1.5) == 1
    assert wind.strength_class(9.0) == 2
    assert wind.strength_class(30.0) == len(wind.CLASSES) - 1


def terrain():
    """A 10 km square: a valley floor at 500 m rising to a 2500 m peak in the middle."""
    y, x = np.mgrid[:101, :101]
    r = np.hypot(x - 50, y - 50)
    dem = np.maximum(500, 2500 - r * 60).astype(float)
    return SimpleNamespace(dem=dem, transform=Affine(100, 0, 0, 0, -100, 10000))


def station(col, row, alt):
    return {"x": col * 100 + 50, "y": 10000 - row * 100 - 50, "alt": alt}


def test_exposure():
    t = terrain()
    assert wind.exposure(station(50, 50, 2500), t)[0] == "summit"
    assert wind.exposure(station(5, 5, 500), t)[0] == "valley"
    assert wind.exposure(station(28, 50, 1180), t)[0] == "slope"


def test_foehn_needs_north_wind_dry_air_and_gusts():
    hum = [80, 80, 80, 60, 50, 35]
    w = [[2, 0]] * 3 + [[5, 350], [5, 10], [5, 180]]
    g = [5, 5, 5, 12, 12, 12]
    flags = wind.foehn(w, g, hum, "valley")
    # Hour 3: dried by 20 points to 60 % - not yet dry enough; hour 4 qualifies;
    # hour 5 is dry but the wind comes from the south.
    assert flags == [False, False, False, False, True, False]
    assert not any(wind.foehn(w, g, hum, "summit"))
