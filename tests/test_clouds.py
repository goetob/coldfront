import numpy as np

from meteo import clouds


def test_snow_line_splits_snow_from_bare_ground():
    # A slope from 500 to 3000 m, snow from 1800 m up; a few snow-free cells above it.
    dem = np.repeat(np.linspace(500, 3000, 251)[:, None], 20, axis=1)
    snow = (dem >= 1800).astype(float)
    snow[240:, :2] = 0  # wind-blown ridges
    clear = 1 - snow
    inside = np.ones(dem.shape, bool)
    assert abs(clouds.snow_line(snow, clear, dem, inside) - 1800) <= 50


def test_no_snow_line_without_snow():
    dem = np.full((10, 10), 1000.0)
    inside = np.ones(dem.shape, bool)
    assert clouds.snow_line(np.zeros(dem.shape), np.ones(dem.shape), dem, inside) is None


def test_snow_stays_out_of_the_valley():
    # A valley at 500 m between ridges at 2500 m, snow from 1500 m, seen in 2 km
    # pixels (20 cells) that count as snow where most of them is.
    x = np.linspace(-1, 1, 200)
    dem = np.repeat((500 + 2000 * x ** 2)[None, :], 100, axis=0)
    blocks = (dem >= 1500).reshape(5, 20, 10, 20).mean(axis=(1, 3)) > 0.5
    coarse = np.kron(blocks, np.ones((20, 20)))
    snow, line = clouds.snow_cover(coarse, 1 - coarse, dem, np.ones(dem.shape, bool))
    assert abs(line - 1500) <= 350  # a pixel on this slope spans 700 m of elevation
    assert snow[dem < line - clouds.SNOW_EDGE].max() == 0
    assert snow[dem < 1000].max() == 0
    assert snow[50, 0] > 0.9


def test_cover_weights_thin_and_fractional_clouds_half():
    classes = np.array([[5, 7, 8, 11, 14, 3, 1]])
    coords = (np.zeros(7), np.arange(7, dtype=float))
    c = clouds.cover(classes, coords)
    assert list(c["low"]) == [1, 0, 0, 0, 0.5, 0, 0]
    assert list(c["mid"]) == [0, 1, 0, 0, 0, 0, 0]
    assert list(c["high"]) == [0, 0, 1, 0.5, 0, 0, 0]
    assert list(c["snow"]) == [0, 0, 0, 0, 0, 1, 0]


def test_cover_is_bilinear_between_pixels():
    classes = np.array([[7, 1]])
    c = clouds.cover(classes, (np.zeros(3), np.array([0, 0.5, 1])))
    assert np.allclose(c["mid"], [1, 0.5, 0])


def test_daylight_ignores_pixels_cut_off():
    light = np.full((10, 10), clouds.DAY)
    light[:3] = 0
    assert clouds.is_day(light)
    light[5] = 2  # twilight
    assert not clouds.is_day(light)
    assert not clouds.is_day(np.zeros((3, 3)))
