"""Plausibility checks on the raw 10-minute values.

During a single event bad values get noticed on the maps; in continuous
operation nobody looks, so obvious faults are caught here. The checks are
conservative: a flagged value is left out of the hourly aggregate and
counted, never corrected.

  range   outside physical limits for the sensor (snow depth sensors read
          slightly below zero on bare ground; that is handled in hourly.py)
  spike   a single value that jumps away and straight back (LT, LD.RED)
  stuck   the same value for many hours where real air never holds still
          (LT; LF other than saturation; a non-zero wind speed)

Gusts are checked again on the hourly values (gust_outliers), against the
station's own mean wind and against the network: the archive back to 2015
holds 68 hours with gusts of 60 to 95 m/s, all faults, such as 95 m/s in a
mean wind of 0.4 m/s or a sensor reporting 80 to 95 m/s for weeks while no
other station passed 26. The strongest real gusts in it reach 53 m/s.
"""

import numpy as np

RANGE = {
    "LT": (-45, 45), "LF": (0, 105), "LD.RED": (900, 1080),
    "WG": (0, 75), "WG.BOE": (0, 75), "WR": (0, 360),
    "N": (0, 50), "HS": (-30, 800), "SD": (0, 600), "GS": (0, 1500),
}
SPIKE = {"LT": 8.0, "LD.RED": 8.0}
# A gust is out of place above GUST_FACTOR times its hour's mean wind plus
# GUST_OFFSET (in a storm, 50 m/s gusts come with 20 to 30 m/s of mean wind),
# or, from GUST_LONELY on, at more than twice the third strongest gust of the
# hour in the network.
GUST_FACTOR, GUST_OFFSET = 3.0, 25.0
GUST_LONELY, GUST_LONELY_RATIO, GUST_NETWORK = 35.0, 2.0, 10
STUCK_HOURS = {"LT": 12, "LF": 12, "WG": 6}


def check(sensor, rows):
    """Split [(t, v), ...] sorted by time into (good rows, mask of flagged rows)."""
    if not rows:
        return [], np.zeros(0, bool)
    t = np.array([r[0].timestamp() for r in rows])
    v = np.array([r[1] for r in rows], dtype=float)
    bad = np.zeros(len(v), bool)

    lo, hi = RANGE.get(sensor, (-np.inf, np.inf))
    bad |= (v < lo) | (v > hi)

    if sensor in SPIKE and len(v) >= 3:
        jump = SPIKE[sensor]
        up = v[1:-1] - v[:-2]
        down = v[1:-1] - v[2:]
        spike = (np.abs(up) > jump) & (np.abs(down) > jump) & (np.sign(up) == np.sign(down))
        bad[1:-1] |= spike

    if sensor in STUCK_HOURS:
        bad |= stuck(t, v, STUCK_HOURS[sensor] * 3600, sensor)

    good = [r for r, b in zip(rows, bad) if not b]
    return good, bad


def stuck(t, v, span, sensor):
    """Runs of identical values lasting at least `span` seconds."""
    out = np.zeros(len(v), bool)
    start = 0
    for i in range(1, len(v) + 1):
        if i == len(v) or v[i] != v[start]:
            value = v[start]
            exempt = (sensor == "LF" and value >= 99) or (sensor == "WG" and value == 0)
            if not exempt and t[i - 1] - t[start] >= span:
                out[start:i] = True
            start = i
    return out


def gust_outliers(gust, mean):
    """Mask of implausible hourly gusts; arrays [hour, station], NaN where missing."""
    with np.errstate(invalid="ignore"):
        windless = np.isfinite(mean) & (gust > GUST_FACTOR * mean + GUST_OFFSET)
        filled = np.where(np.isfinite(gust), gust, -np.inf)
        # The third strongest gust of the hour: two faulty sensors at once
        # must not vouch for each other.
        third = np.sort(filled, axis=1)[:, -3:-2] if gust.shape[1] >= 3 else np.full((len(gust), 1), np.inf)
        enough = np.isfinite(gust).sum(axis=1, keepdims=True) >= GUST_NETWORK
        lonely = enough & (gust >= GUST_LONELY) & (gust > GUST_LONELY_RATIO * third)
        return windless | lonely | (gust > RANGE["WG.BOE"][1])
