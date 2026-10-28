"""Fog and low stratus (Hochnebel) from the stations, by day.

A heuristic, shown as "Nebel oder Hochnebel": a station lies in fog when,
over an hour in which the terrain lets the sun reach it, it had no sunshine,
saturated air and no precipitation, while a station at least TOP_RISE m
higher within NEAR m had sunshine and drier air and is no colder than the
normal lapse rate allows (the stable layering a fog sea needs). It must
hold for two hours in a row. The fog top lies between the highest station
in fog and the lowest sunny station above it.

Only by day and only at stations with a sunshine sensor: at night there is
nothing to tell fog from clear, damp air. The terrain horizon matters, as
from November several valley stations lie in the shade of the mountains
around noon (Obervintl, Welschnofen) and read no sun on the clearest days.

Checked against November to January of 2024/25 and 2025/26 (11 days with
fog, 44 station hours): it finds the fog sea of 8 January 2025 (Branzoll,
Auer and Brixen in fog, by the afternoon up to Deutschnofen at 1470 m) and
the Pustertal fog of 9 December 2025 (Bruneck all day, sun from 1320 m).
On clear days, where the horizon says the sun is behind the mountains, the
stations read sunshine in 1.5 % of the hours.
"""

from datetime import datetime, timedelta

import numpy as np

FOG_RH, FOG_SUN = 95.0, 0.0     # in fog: humidity at least, sunshine at most (minutes)
SUN_MIN, SUN_RH = 30.0, 90.0    # above it: sunshine at least (minutes), humidity below
TOP_RISE, NEAR = 150, 25000     # the sunny station: at least this much higher (m), this close (m)
LAPSE, LAPSE_SLACK = 0.0065, 1.0  # and no colder than 0.65 K per 100 m less 1 K
VISIBLE = 0.75                  # share of the hour the sun stands above the horizon
HORIZON_MARGIN = 1.0            # degrees above the terrain horizon
AZ_STEP = 2                     # degrees between horizon rays


def sun_position(lat, lon, when):
    """(elevation, azimuth) of the sun in degrees, azimuth clockwise from north.

    `when` is a naive UTC datetime. The low-precision formulas of the
    Astronomical Almanac, good to about 0.01 degrees.
    """
    d = (when - datetime(2000, 1, 1, 12)).total_seconds() / 86400
    g = np.radians((357.529 + 0.98560028 * d) % 360)
    q = (280.459 + 0.98564736 * d) % 360
    lam = np.radians((q + 1.915 * np.sin(g) + 0.020 * np.sin(2 * g)) % 360)
    eps = np.radians(23.439 - 0.00000036 * d)
    ra = np.arctan2(np.cos(eps) * np.sin(lam), np.cos(lam))
    dec = np.arcsin(np.sin(eps) * np.sin(lam))
    gmst = (18.697374558 + 24.06570982441908 * d) % 24
    ha = np.radians((gmst * 15 + lon) % 360) - ra
    phi = np.radians(lat)
    el = np.arcsin(np.sin(phi) * np.sin(dec) + np.cos(phi) * np.cos(dec) * np.cos(ha))
    az = np.arctan2(-np.sin(ha), np.tan(dec) * np.cos(phi) - np.sin(phi) * np.cos(ha))
    return float(np.degrees(el)), float(np.degrees(az) % 360)


def horizon(terrain, x, y, z, start=200, stop=40000):
    """Elevation angle (degrees) of the terrain horizon seen from (x, y, z), every AZ_STEP degrees."""
    a = terrain.transform
    dist = np.arange(start, stop, abs(a.a))
    out = np.zeros(360 // AZ_STEP)
    for i, az in enumerate(range(0, 360, AZ_STEP)):
        r = np.radians(az)
        col = ((x + dist * np.sin(r) - a.c) / a.a).astype(int)
        row = ((y + dist * np.cos(r) - a.f) / a.e).astype(int)
        ok = (col >= 0) & (col < terrain.shape[1]) & (row >= 0) & (row < terrain.shape[0])
        h = np.nan_to_num(terrain.dem[row[ok], col[ok]], nan=-1e4)
        d = dist[ok]
        if d.size:  # with the earth's curvature
            out[i] = float(np.degrees(np.arctan2(h - z - d ** 2 / 12742000, d)).max())
    return out


def sun_share(hz, lat, lon, hour_end):
    """Share of the hour ending at `hour_end` (naive UTC) in which the sun clears the horizon."""
    az_grid = np.arange(0, 360 + AZ_STEP, AZ_STEP)
    ring = np.append(hz, hz[0])
    n = 0
    for minute in (7.5, 22.5, 37.5, 52.5):
        el, az = sun_position(lat, lon, hour_end - timedelta(minutes=60 - minute))
        n += el > np.interp(az, az_grid, ring) + HORIZON_MARGIN
    return n / 4


def detect(stations, times, series, horizons):
    """Fog by station and hour.

    stations  [{code, alt, x, y, lat, lon}, ...]
    times     ["YYYY-MM-DDTHH:MMZ", ...], the hours they end at
    series    {sensor: {code: [value per hour]}} with SD (minutes), LF, LT and N
    horizons  {code: horizon(...)} for the stations with a sunshine sensor

    Returns ({code: [bool per hour]}, [(fog top low, high) or None per hour]);
    the top is None also where the stations in fog disagree on it.
    """
    sd, lf, lt, n = (series.get(s, {}) for s in ("SD", "LF", "LT", "N"))
    codes = [s for s in stations if s["code"] in horizons and s["code"] in sd]
    ends = [datetime.strptime(t, "%Y-%m-%dT%H:%MZ") for t in times]
    get = lambda m, c, k: m.get(c, [None] * len(times))[k]

    candidate = {s["code"]: [False] * len(times) for s in codes}
    support = {s["code"]: [None] * len(times) for s in codes}  # lowest sunny station above
    for k, end in enumerate(ends):
        sunny = [s for s in codes if (get(sd, s["code"], k) or 0) >= SUN_MIN
                 and get(lf, s["code"], k) is not None and get(lf, s["code"], k) < SUN_RH
                 and get(lt, s["code"], k) is not None]
        if not sunny:
            continue
        for s in codes:
            c = s["code"]
            sun, rh, t = get(sd, c, k), get(lf, c, k), get(lt, c, k)
            if sun is None or rh is None or t is None or sun > FOG_SUN or rh < FOG_RH or (get(n, c, k) or 0) > 0:
                continue
            above = [u["alt"] for u in sunny
                     if u["alt"] >= s["alt"] + TOP_RISE and np.hypot(u["x"] - s["x"], u["y"] - s["y"]) <= NEAR
                     and get(lt, u["code"], k) >= t - LAPSE * (u["alt"] - s["alt"]) + LAPSE_SLACK]
            if above and sun_share(horizons[c], s["lat"], s["lon"], end) >= VISIBLE:
                candidate[c][k], support[c][k] = True, min(above)

    fog = {c: [v[k] and ((k > 0 and v[k - 1]) or (k + 1 < len(v) and v[k + 1])) for k in range(len(v))]
           for c, v in candidate.items()}
    alt = {s["code"]: s["alt"] for s in codes}
    tops = []
    for k in range(len(times)):
        inside = [c for c in fog if fog[c][k]]
        if not inside:
            tops.append(None)
            continue
        # Each station puts the top between itself and its sunny station; a
        # common top lies in all of these ranges, if they overlap.
        low, high = max(alt[c] for c in inside), min(support[c][k] for c in inside)
        tops.append((low, high) if high > low else None)
    return fog, tops
