"""Aggregate the raw 5/10-minute station records to hourly values.

  LT, LF, LD.RED  instantaneous value at the full hour
  N               sum over the hour ending at the full hour (mm)
  WG, WR          vector mean over the hour: speed (m/s) and direction (deg)
  WG.BOE          maximum gust over the hour (m/s)

Times are UTC. Writes data/processed/hourly.json.
"""

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

OFFSETS = {"CET": 1, "CEST": 2}


def parse_date(s):
    """'2026-10-18T21:50:00CEST' -> aware UTC datetime."""
    local, zone = s[:19], s[19:]
    tz = timezone(timedelta(hours=OFFSETS[zone]))
    return datetime.fromisoformat(local).replace(tzinfo=tz).astimezone(timezone.utc)


def hour_ending(t):
    """The full hour an observation belongs to: (h-1, h]."""
    floor = t.replace(minute=0, second=0, microsecond=0)
    return floor if t == floor else floor + timedelta(hours=1)


def load(path):
    return [(parse_date(d), v) for d, v in json.loads(path.read_text()) if v is not None]


def aggregate(sensor, rows):
    buckets = defaultdict(list)
    step = np.median(np.diff([t.timestamp() for t, _ in rows])) if len(rows) > 1 else 600
    per_hour = 3600 / step
    for t, v in rows:
        buckets[hour_ending(t)].append((t, v))
    out = {}
    for h, obs in buckets.items():
        if sensor in ("LT", "LF", "LD.RED"):
            at_hour = [v for t, v in obs if t == h]
            if at_hour:
                out[h] = at_hour[0]
        elif sensor == "N":
            # Require most of the hour to be present so gaps don't read as dry.
            if len(obs) >= 0.8 * per_hour:
                out[h] = round(sum(v for _, v in obs), 2)
        elif sensor == "WG.BOE":
            out[h] = max(v for _, v in obs)
        else:
            out[h] = [v for _, v in obs]
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", default="data/raw")
    parser.add_argument("--out", default="data/processed/hourly.json")
    args = parser.parse_args()
    raw = Path(args.raw)

    stations = {}
    for f in json.loads((raw / "stations.json").read_text())["features"]:
        p = f["properties"]
        x, y = f["geometry"]["coordinates"]
        stations[p["SCODE"]] = {
            "code": p["SCODE"], "name": p["NAME_D"], "name_it": p["NAME_I"],
            "alt": p["ALT"], "x": round(x, 1), "y": round(y, 1),
            "lon": p["LONG"], "lat": p["LAT"],
        }

    per_sensor = defaultdict(dict)  # sensor -> code -> {hour: value}
    for path in sorted((raw / "timeseries").glob("*.json")):
        code, sensor = path.stem.split("__")
        rows = load(path)
        if rows and code in stations:
            per_sensor[sensor][code] = aggregate(sensor, rows)

    # Wind: combine speed and direction samples into a vector mean per hour.
    wind = {}
    for code in set(per_sensor.get("WG", {})) & set(per_sensor.get("WR", {})):
        speeds, dirs = per_sensor["WG"][code], per_sensor["WR"][code]
        hourly = {}
        for h in set(speeds) & set(dirs):
            s, d = np.array(speeds[h]), np.radians(dirs[h])
            if len(s) != len(d) or len(s) == 0:
                continue
            u, v = -(s * np.sin(d)).mean(), -(s * np.cos(d)).mean()
            direction = (np.degrees(np.arctan2(-u, -v)) + 360) % 360
            hourly[h] = [round(float(s.mean()), 2), round(float(direction))]
        wind[code] = hourly
    per_sensor.pop("WG", None)
    per_sensor.pop("WR", None)
    per_sensor["WIND"] = wind

    hours = sorted({h for series in per_sensor.values() for s in series.values() for h in s})
    # Drop the ragged ends where only a few stations have reported yet.
    counts = [sum(h in s for s in per_sensor["LT"].values()) for h in hours]
    full = max(counts)
    hours = [h for h, c in zip(hours, counts) if c >= 0.8 * full]

    series = {
        sensor: {code: [s.get(h) for h in hours] for code, s in by_code.items()}
        for sensor, by_code in per_sensor.items()
    }
    used = sorted({c for by_code in series.values() for c in by_code})

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "times": [h.strftime("%Y-%m-%dT%H:%MZ") for h in hours],
        "stations": [stations[c] for c in used],
        "series": series,
    }, ensure_ascii=False, separators=(",", ":")))
    print(f"{len(hours)} hours {hours[0]:%d.%m %H:%M}–{hours[-1]:%d.%m %H:%M} UTC, "
          f"{len(used)} stations, " + ", ".join(f"{k}:{len(v)}" for k, v in series.items()))


if __name__ == "__main__":
    main()
