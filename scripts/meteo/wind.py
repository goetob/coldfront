"""Wind at the stations: strength classes, station exposure, foehn.

Wind is not interpolated, because valleys turn it within a few hundred
metres. What helps reading the station arrows is knowing where a station
stands, and a scale of strength that is the same on every map.
"""

import math

import numpy as np

# Beaufort-based classes in m/s: lower bounds, names and colours.
# Sand to plum, so wind never reads as temperature (slate/brick) or rain (petrol).
CLASSES = [
    (0.0, "still", "#9a958a"),
    (1.5, "schwach", "#bf8a3c"),
    (5.5, "mäßig", "#a05d26"),
    (10.8, "stark", "#7d3826"),
    (17.2, "stürmisch", "#5c2246"),
    (24.5, "Sturm", "#2e1236"),
]
LEVELS = [c[0] for c in CLASSES]
COLORS = [c[2] for c in CLASSES]
# Gusts from this strength get their value written next to the station.
GUST_LABEL = 17.2

EXPOSURES = {
    "summit": "Gipfel und Kämme",
    "slope": "Hänge und Hochflächen",
    "valley": "Täler",
}


def strength_class(speed):
    return int(np.searchsorted(LEVELS, speed, side="right") - 1)


def exposure(station, terrain, radius=3000):
    """Where a station stands relative to the terrain within `radius`.

    summit  at least 300 m above the mean elevation around it
    valley  in the lowest 15 % of the elevation range around it
    slope   anything in between (slopes, plateaus, passes, high basins)
    """
    a = terrain.transform
    col = int((station["x"] - a.c) / a.a)
    row = int((station["y"] - a.f) / a.e)
    k = int(radius / a.a)
    win = terrain.dem[max(row - k, 0):row + k + 1, max(col - k, 0):col + k + 1]
    yy, xx = np.mgrid[:win.shape[0], :win.shape[1]]
    disk = np.hypot(yy - min(row, k), xx - min(col, k)) <= k
    around = win[disk & np.isfinite(win)]
    if around.size == 0:
        return "slope", 0
    tpi = station["alt"] - float(around.mean())
    lo, hi = float(around.min()), float(around.max())
    rel = (station["alt"] - lo) / max(hi - lo, 1.0)
    if tpi >= 300:
        cls = "summit"
    elif rel <= 0.15:
        cls = "valley"
    else:
        cls = "slope"
    return cls, round(tpi)


def foehn(wind, gust, humidity, exposure_class):
    """Hours with signs of north foehn at one station: [bool, ...].

    A heuristic, shown as "Föhnverdacht": wind with a northern component of
    at least 3 m/s and gusts of 8 m/s, and dry air, either below 40 % or
    after a drop of 10 points within three hours to at most 55 %.
    Summits are left out; foehn is felt where the air comes down.
    """
    out = [False] * len(wind)
    if exposure_class == "summit" or humidity is None:
        return out
    for k, w in enumerate(wind):
        if w is None or humidity[k] is None:
            continue
        speed, direction = w
        g = gust[k] if gust and gust[k] is not None else 0
        before = humidity[k - 3] if k >= 3 else None
        dry = humidity[k] <= 40 or (before is not None and humidity[k] <= 55 and before - humidity[k] >= 10)
        northern = math.cos(math.radians(direction)) > 0.3
        out[k] = bool(northern and speed >= 3 and g >= 8 and dry)
    return out
