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
"""

import numpy as np

RANGE = {
    "LT": (-45, 45), "LF": (0, 105), "LD.RED": (900, 1080),
    "WG": (0, 75), "WG.BOE": (0, 95), "WR": (0, 360),
    "N": (0, 50), "HS": (-30, 800), "SD": (0, 600), "GS": (0, 1500),
}
SPIKE = {"LT": 8.0, "LD.RED": 8.0}
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
