"""Turn the raw window in data/raw into hourly values: data/processed/hourly.json.

See meteo/hourly.py for how each sensor is aggregated and meteo/qc.py for
the plausibility checks. Times are UTC.

  --hours N            the newest N complete hours (rolling page)
  --start T --hours N  N hours from local hour T (an event)
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from meteo import api, hourly  # noqa: E402
from meteo.timeutil import iso, parse_api_date, parse_local_hour  # noqa: E402


def load_raw(raw_dir, codes):
    raw = {}
    for path in sorted((raw_dir / "timeseries").glob("*.json")):
        code, sensor = path.stem.split("__")
        if code not in codes:
            continue
        rows = [(parse_api_date(d), v) for d, v in json.loads(path.read_text()) if v is not None]
        if rows:
            raw[(code, sensor)] = rows
    return raw


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", default="data/raw")
    parser.add_argument("--out", default="data/processed/hourly.json")
    parser.add_argument("--hours", type=int, help="number of hours to keep (default: all)")
    parser.add_argument("--start", help="first hour, local time YYYY-MM-DDTHH")
    args = parser.parse_args()
    raw_dir = Path(args.raw)

    stations = api.station_table(json.loads((raw_dir / "stations.json").read_text()))
    per_sensor, flagged = hourly.build(load_raw(raw_dir, stations), {c: s["alt"] for c, s in stations.items()})

    hours = sorted({h for by_code in per_sensor.values() for s in by_code.values() for h in s})
    # Drop the ragged ends where only a few stations have reported yet.
    counts = [sum(h in s for s in per_sensor["LT"].values()) for h in hours]
    full = max(counts)
    hours = [h for h, c in zip(hours, counts) if c >= 0.8 * full]

    if args.start:
        first = parse_local_hour(args.start)
        hours = [h for h in hours if h >= first][: args.hours]
    elif args.hours:
        hours = hours[-args.hours:]
    if not hours:
        sys.exit("no hours in the requested window")

    series = {
        sensor: {code: [s.get(h) for h in hours] for code, s in sorted(by_code.items())}
        for sensor, by_code in sorted(per_sensor.items())
    }
    series = {k: {c: v for c, v in by_code.items() if any(x is not None for x in v)}
              for k, by_code in series.items()}
    in_window = set(hours)
    qc = {sensor: {code: n for code, by_hour in by_code.items()
                   if (n := sum(c for h, c in by_hour.items() if h in in_window))}
          for sensor, by_code in flagged.items()}
    used = sorted({c for by_code in series.values() for c in by_code})

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "times": [iso(h) for h in hours],
        "stations": [stations[c] for c in used],
        "series": series,
        "qc": {k: v for k, v in qc.items() if v},
    }, ensure_ascii=False, separators=(",", ":")))
    n_flagged = sum(sum(v.values()) for v in qc.values())
    print(f"{len(hours)} hours {hours[0]:%d.%m %H:%M}–{hours[-1]:%d.%m %H:%M} UTC, "
          f"{len(used)} stations, " + ", ".join(f"{k}:{len(v)}" for k, v in series.items())
          + f", {n_flagged} raw values flagged")


if __name__ == "__main__":
    main()
