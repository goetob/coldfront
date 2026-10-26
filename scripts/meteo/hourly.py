"""Aggregate raw 5/10-minute station records to hourly values.

  LT, LF, LD.RED, HS  instantaneous value at the full hour
  N                   sum over the hour ending at the full hour (mm)
  SD                  sunshine over the hour (minutes)
  GS                  mean global radiation over the hour (W/m²)
  WG, WR              vector mean over the hour, combined into WIND: [speed m/s, direction deg]
  WG.BOE              maximum gust over the hour (m/s)

Hours are aware UTC datetimes and label the hour that ends at them.
"""

from collections import defaultdict
from datetime import timedelta

import numpy as np

from . import qc
from .timeutil import hour_ending

INSTANT = ("LT", "LF", "LD.RED", "HS")
# Sums need most of the hour to be present so gaps don't read as dry or dark.
COMPLETE = 0.8


def aggregate(sensor, rows):
    """{hour: value} for one station and sensor; rows are (t, v) sorted by time."""
    if not rows:
        return {}
    step = np.median(np.diff([t.timestamp() for t, _ in rows])) if len(rows) > 1 else 600
    per_hour = 3600 / max(step, 60)
    buckets = defaultdict(list)
    for t, v in rows:
        buckets[hour_ending(t)].append((t, v))
    out = {}
    for h, obs in buckets.items():
        vals = [v for _, v in obs]
        if sensor in INSTANT:
            at_hour = [v for t, v in obs if t == h]
            if at_hour:
                # Snow depth sensors drift a few cm below zero on bare ground.
                out[h] = max(0.0, at_hour[0]) if sensor == "HS" else at_hour[0]
        elif sensor == "N":
            if len(obs) >= COMPLETE * per_hour:
                out[h] = round(sum(vals), 2)
        elif sensor == "SD":
            if len(obs) >= COMPLETE * per_hour:
                out[h] = round(min(60.0, sum(vals) / 60 * per_hour / len(obs)), 1)
        elif sensor == "GS":
            if len(obs) >= COMPLETE * per_hour:
                out[h] = round(float(np.mean(vals)), 1)
        elif sensor == "WG.BOE":
            out[h] = max(vals)
        else:
            out[h] = obs
    return out


def wind_mean(speeds, dirs):
    """Hourly vector mean from the samples of WG and WR: {hour: [speed, direction]}.

    Speed is the scalar mean (what an anemometer reports), direction the
    direction of the mean vector, so a wind swinging around north stays north.
    """
    out = {}
    for h in set(speeds) & set(dirs):
        d_at = dict(dirs[h])
        pairs = [(v, d_at[t]) for t, v in speeds[h] if t in d_at]
        if not pairs:
            continue
        s, d = np.array(pairs, dtype=float).T
        rad = np.radians(d)
        u, v = -(s * np.sin(rad)).mean(), -(s * np.cos(rad)).mean()
        direction = (np.degrees(np.arctan2(-u, -v)) + 360) % 360
        out[h] = [round(float(s.mean()), 2), round(float(direction)) % 360]
    return out


def build(raw, alt=None):
    """raw: {(code, sensor): [(t, v), ...]} -> (per_sensor, flagged).

    alt: {code: elevation in m}; without it, temperatures are not checked
    against the elevation trend (qc.temp_outliers).

    per_sensor  {sensor: {code: {hour: value}}}, wind merged into WIND
    flagged     {sensor: {code: {hour: n}}}, raw values QC left out
    """
    per_sensor = defaultdict(dict)
    flagged = defaultdict(dict)
    for (code, sensor), rows in raw.items():
        good, bad = qc.check(sensor, rows)
        if bad.any():
            counts = defaultdict(int)
            for (t, _), b in zip(rows, bad):
                if b:
                    counts[hour_ending(t)] += 1
            flagged[sensor][code] = dict(counts)
        if good:
            per_sensor[sensor][code] = aggregate(sensor, good)

    wg, wr = per_sensor.pop("WG", {}), per_sensor.pop("WR", {})
    per_sensor["WIND"] = {code: wind_mean(wg[code], wr[code]) for code in set(wg) & set(wr)}
    check_gusts(per_sensor, flagged)
    if alt:
        check_temps(per_sensor, flagged, alt)
    check_snow(per_sensor, flagged)
    return dict(per_sensor), dict(flagged)


def check_temps(per_sensor, flagged, alt):
    """Drop hourly temperatures far off the elevation trend or frozen (qc.temp_outliers)."""
    temps = per_sensor.get("LT", {})
    if not any(temps.values()):
        return
    codes = sorted(temps)
    first = min(min(by_hour) for by_hour in temps.values() if by_hour)
    last = max(max(by_hour) for by_hour in temps.values() if by_hour)
    hours = [first + timedelta(hours=k) for k in range(int((last - first).total_seconds() // 3600) + 1)]
    row = {h: k for k, h in enumerate(hours)}
    t = np.full((len(hours), len(codes)), np.nan)
    for j, code in enumerate(codes):
        for h, v in temps[code].items():
            t[row[h], j] = v
    z = np.array([alt.get(code, np.nan) for code in codes], dtype=float)
    for k, j in np.argwhere(qc.temp_outliers(t, z)):
        code, h = codes[j], hours[k]
        del temps[code][h]
        counts = flagged.setdefault("LT", {}).setdefault(code, {})
        counts[h] = counts.get(h, 0) + 1


def check_snow(per_sensor, flagged):
    """Drop faulty snow depths (qc.snow_outliers) from every station's hourly series."""
    lt = per_sensor.get("LT", {})
    for code, by_hour in per_sensor.get("HS", {}).items():
        hours = sorted(by_hour)
        temps = [lt.get(code, {}).get(h, np.nan) for h in hours]
        for k in np.flatnonzero(qc.snow_outliers([by_hour[h] for h in hours], temps)):
            del by_hour[hours[k]]
            counts = flagged.setdefault("HS", {}).setdefault(code, {})
            counts[hours[k]] = counts.get(hours[k], 0) + 1


def check_gusts(per_sensor, flagged):
    """Drop hourly gusts that do not fit their mean wind or the network (qc.gust_outliers)."""
    gusts = per_sensor.get("WG.BOE", {})
    if not gusts:
        return
    codes = sorted(gusts)
    hours = sorted({h for by_hour in gusts.values() for h in by_hour})
    row = {h: k for k, h in enumerate(hours)}
    g = np.full((len(hours), len(codes)), np.nan)
    m = np.full_like(g, np.nan)
    wind = per_sensor.get("WIND", {})
    for j, code in enumerate(codes):
        for h, v in gusts[code].items():
            g[row[h], j] = v
        for h, v in wind.get(code, {}).items():
            if h in row:
                m[row[h], j] = v[0]
    for k, j in np.argwhere(qc.gust_outliers(g, m)):
        code, h = codes[j], hours[k]
        del gusts[code][h]
        counts = flagged.setdefault("WG.BOE", {}).setdefault(code, {})
        counts[h] = counts.get(h, 0) + 1
