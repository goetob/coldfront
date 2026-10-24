"""Quantities derived from the hourly station series.

Series are lists over the hours of hourly.json, with None for gaps.
"""

import numpy as np

# Magnus formula over water (Alduchov and Eskridge 1996).
A, B = 17.625, 243.04


def dewpoint(t, rh):
    """Dew point (°C) from temperature (°C) and relative humidity (%)."""
    if t is None or rh is None or rh <= 0:
        return None
    rh = min(rh, 100.0)
    g = np.log(rh / 100) + A * t / (B + t)
    return round(float(B * g / (A - g)), 1)


def relative_humidity(t, td):
    """Relative humidity (%) from temperature and dew point fields or values."""
    rh = 100 * np.exp(A * td / (B + td) - A * t / (B + t))
    return np.clip(rh, 1, 100)


def rolling_sum(series, hours, need=0.9):
    """Sum over the last `hours` hours ending at each hour; None where too much is missing."""
    out = []
    for k in range(len(series)):
        if k + 1 < hours:
            out.append(None)
            continue
        window = [v for v in series[k + 1 - hours:k + 1] if v is not None]
        out.append(round(sum(window) * hours / len(window), 1) if len(window) >= need * hours else None)
    return out


def change(series, hours):
    """Difference to the value `hours` earlier."""
    return [None if k < hours or v is None or series[k - hours] is None else round(v - series[k - hours], 1)
            for k, v in enumerate(series)]


def daily_extremes(series, day_of_hour, n_days, need=0.8):
    """Minimum and maximum per local day: ([min per day], [max per day]).

    A day needs `need` of its hours; today (the last day) counts as far as it has run.
    """
    lo, hi = [None] * n_days, [None] * n_days
    for d in range(n_days):
        idx = [k for k, dk in enumerate(day_of_hour) if dk == d]
        vals = [series[k] for k in idx if series[k] is not None]
        if vals and len(vals) >= need * len(idx):
            lo[d], hi[d] = min(vals), max(vals)
    return lo, hi
