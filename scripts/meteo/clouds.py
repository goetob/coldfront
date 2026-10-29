"""Clouds from the satellite: EUMETSAT's Cloud Type product of MTG on the map grid.

Cloud Type classifies every 2 km pixel of the full disk every 10 minutes
(clear, snow, very low to very high opaque, semi-transparent, fractional).
The map shows three layers: low (very low, low and fractional), medium and
high (opaque and semi-transparent); the thin ones count half. The pixels are
interpolated bilinearly to the 100 m grid and softened a little more, so
cloud edges are soft over about 2 km rather than stepped.

Cloud-free snow is drawn under the relief. Its pixels are too coarse for
the valleys, so the snow follows the terrain instead: the snow line is the
elevation that best splits snow from snow-free pixels in the clear part of
the picture, and snow lies above it near pixels that saw snow. It cannot
run down into a valley that way, and it stops where the satellite saw none.
The line is only as good as the pixels: on steep slopes one spans several
hundred metres of elevation, and the line may be off by about that much.

Both sides of the line weigh the same, as shares of all snow and of all
snow-free pixels. Counting misplaced pixels instead lets the far more
numerous snow-free ones win: on 22 October 2026, after the cold front, the
line so counted climbed from 1700 to 2950 m between 11 and 16 o'clock as the
clouds cleared off forested slopes, whose snow the satellite does not see,
while the stations kept their snow from about 1900 m; weighed it stays at
1700 to 1950 m.

Where snow lies near a pixel, it counts if any daylight picture of the last
24 hours saw snow there: from one picture to the next Cloud Type loses snow
on shaded or forested slopes (on 22 October 2026 it lost the Dolomites in
the afternoon) and the snow on the map would flicker.

Cloud Type finds snow only in daylight, and even then it sometimes takes
fresh snow for cloud. When a picture shows no snow at all, the snow of the
last daylight picture that had some stays on the map, for up to three days.

By day the visible channel (0.6 micron, 500 m, from EUMETView) gives the
clouds their texture; at night they are drawn flat.

Not a fog detector: Cloud Type sees only the top layer, and fog in the
narrow valleys is often under higher cloud or narrower than a pixel.
"""

import numpy as np
from pyproj import Transformer
from scipy.ndimage import binary_dilation, gaussian_filter, map_coordinates

# Cloud Type classes (flag_meanings of cloud_type) per layer, with the weight they count with.
LAYERS = {
    "low": {5: 1.0, 6: 1.0, 14: 0.5},    # very low and low opaque, fractional
    "mid": {7: 1.0},                     # medium opaque
    "high": {8: 1.0, 9: 1.0, 10: 0.5, 11: 0.5, 12: 0.5, 13: 1.0},  # high opaque, semi-transparent
}
SNOW, CLEAR = (3, 4), (1, 2)
DAY = 3  # quality_illumination: 1 night, 2 twilight, 3 day

CLOUD_SOFTEN = 8    # cells (100 m): Gaussian softening of the cloud layers after the interpolation
SNOW_EDGE = 120.0   # m: the snow line fades in over this much elevation
SNOW_NEAR = 25      # cells (100 m): snow only this close to a pixel that saw snow
SNOW_MIN = 0.005    # share of the clear province that must be snow to draw any
SNOW_KEEP = 72      # hours the snow of the last daylight picture with snow stays
SNOW_SEEN = 24     # hours of daylight pictures whose snow pixels together say where snow lies
SNOW_PIXELS = 10    # snow pixels (about 4 km² each) a picture needs to count as having snow

_GRID = {}


def grid_coords(terrain, crs, transform):
    """Fractional (row, col) of every map cell in the satellite picture."""
    key = (str(crs), tuple(transform), terrain.shape)
    if key not in _GRID:
        fx, fy = terrain.centers
        x, y = np.meshgrid(fx, fy)
        gx, gy = Transformer.from_crs("EPSG:25832", crs, always_xy=True).transform(x, y)
        col, row = ~transform * (gx, gy)
        _GRID.clear()
        _GRID[key] = (np.asarray(row) - 0.5, np.asarray(col) - 0.5)
    return _GRID[key]


def on_grid(field, coords):
    """Bilinear interpolation of a pixel field to the map grid."""
    return map_coordinates(field.astype(float), coords, order=1, mode="nearest")


def cover(classes, coords, soften=CLOUD_SOFTEN):
    """{layer: share of the cell covered, 0..1} and the snow and clear shares, on the map grid.
    Only the cloud layers are softened; snow follows the terrain (snow_cover)."""
    out = {}
    for name, weights in LAYERS.items():
        w = np.zeros(classes.shape)
        for c, v in weights.items():
            w[classes == c] = v
        out[name] = on_grid(w, coords)
        if soften:
            out[name] = gaussian_filter(out[name], soften)
    out["snow"] = on_grid(np.isin(classes, SNOW), coords)
    out["clear"] = on_grid(np.isin(classes, CLEAR), coords)
    return out


def snow_line(snow, clear, dem, inside):
    """Elevation that best splits snow from snow-free cells where the sky is clear, or None.
    Errors on each side count as shares of their class (balanced), not as cell counts."""
    sure = inside & np.isfinite(dem)
    s, c = dem[sure & (snow > 0.5)], dem[sure & (clear > 0.5)]
    if s.size < SNOW_MIN * (s.size + c.size) or s.size == 0:
        return None
    if c.size == 0:
        return float(s.min())
    levels = np.arange(np.floor(min(s.min(), c.min()) / 50) * 50, max(s.max(), c.max()) + 50, 50)
    # Misplaced shares for each candidate line: of the snow below it plus of the snow-free above it.
    wrong = np.searchsorted(np.sort(s), levels) / s.size + (c.size - np.searchsorted(np.sort(c), levels)) / c.size
    return float(levels[int(np.argmin(wrong))])


def snow_cover(snow, clear, dem, inside, seen=None):
    """Snow share 0..1 of each cell: above the snow line and near pixels that saw snow.

    The line comes from `snow` and `clear` of one picture; `seen` (default `snow`) is
    where snow was seen, e.g. in all daylight pictures of the last SNOW_SEEN hours."""
    line = snow_line(snow, clear, dem, inside)
    if line is None:
        return np.zeros(dem.shape), None
    above = np.clip((np.nan_to_num(dem, nan=-1e4) - line) / SNOW_EDGE + 0.5, 0, 1)
    near = binary_dilation((snow if seen is None else seen) > 0.3, iterations=SNOW_NEAR)
    near = gaussian_filter(near.astype(float), SNOW_NEAR / 4)
    return above * near * inside, line


def has_snow(classes):
    """Whether a Cloud Type picture shows enough snow to place a snow line."""
    return enough_snow(np.isin(classes, SNOW))


def enough_snow(mask):
    """Whether a mask of snow pixels has enough of them to count."""
    return bool(mask.sum() >= SNOW_PIXELS)


def texture(vis, inside, lo=0.15, hi=0.8):
    """Brightness of the visible picture, 0..1, to modulate the cloud opacity by day."""
    t = np.clip((vis - lo) / (hi - lo), 0, 1)
    return np.where(inside, t, 0)


def is_day(illumination):
    """Daylight over (nearly) the whole picture, so the visible channel is usable.
    Pixels cut off by the customisation are 0 and do not count."""
    seen = illumination > 0
    return bool(seen.any() and np.mean(illumination[seen] == DAY) > 0.95)
