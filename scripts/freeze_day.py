"""Archive finished local days as Parquet, for the data branch.

Fetches the days afresh from the API (independent of the rolling cache, and
late reports have arrived by then), runs the same QC and hourly aggregation
as the rolling pipeline, and writes immutable files per day:

  hourly/YYYY/MM/YYYY-MM-DD.parquet   station, time (UTC, hour ending), var, value, flagged
  daily/YYYY/YYYY-MM-DD.parquet       one row per station: extremes, sums, means
  stations.json                       station list (its git history records changes)

Hourly vars are the sensor codes, with wind as WG (scalar mean m/s) and WR
(direction of the vector mean). An hour belongs to the day it ends in.

  (no day given)      every day since the newest archived one up to yesterday,
                      at most --catch-up days back; scheduled runs that GitHub
                      skips or delays are made good by the next one
  --date D            one day
  --from D --to D     a range: one request per station and sensor for each
                      block of days instead of one per day
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from meteo import api, hourly  # noqa: E402
from meteo.timeutil import LOCAL, local_day_bounds, parse_api_date  # noqa: E402

# A daily statistic needs this share of the day's hours, otherwise it is left empty.
DAILY_COMPLETE = 0.9

HOURLY_SCHEMA = pa.schema([
    ("station", pa.string()), ("time", pa.timestamp("s", tz="UTC")),
    ("var", pa.string()), ("value", pa.float64()), ("flagged", pa.int16()),
])


def fetch_day(pairs, start, end, workers):
    def one(pair):
        code, sensor = pair
        try:
            records = api.timeseries(code, sensor, start, end)
        except RuntimeError as exc:
            return pair, None, str(exc)
        rows = sorted((parse_api_date(r["DATE"]), r["VALUE"]) for r in records if r["VALUE"] is not None)
        return pair, rows, None

    raw, errors = {}, []
    with ThreadPoolExecutor(workers) as pool:
        for pair, rows, err in pool.map(one, pairs):
            if err:
                errors.append(err)
            elif rows:
                raw[pair] = rows
    return raw, errors


def hourly_rows(per_sensor, flagged, hours):
    rows = []
    in_day = set(hours)
    for sensor, by_code in per_sensor.items():
        for code, by_hour in by_code.items():
            for h, v in by_hour.items():
                if h not in in_day:
                    continue
                n = flagged.get(sensor if sensor != "WIND" else "WG", {}).get(code, {}).get(h, 0)
                if sensor == "WIND":
                    rows.append((code, h, "WG", float(v[0]), n))
                    rows.append((code, h, "WR", float(v[1]), n))
                else:
                    rows.append((code, h, sensor, float(v), n))
    rows.sort(key=lambda r: (r[0], r[2], r[1]))
    return rows


def daily_rows(per_sensor, hours, day):
    need = DAILY_COMPLETE * len(hours)

    def values(sensor, code, pick=lambda v: v):
        by_hour = per_sensor.get(sensor, {}).get(code, {})
        return [pick(by_hour[h]) for h in hours if h in by_hour]

    def stat(vals, fn, digits=1, complete=True):
        if not vals or (complete and len(vals) < need):
            return None
        return round(float(fn(vals)), digits)

    codes = sorted({c for by_code in per_sensor.values() for c in by_code})
    out = []
    for code in codes:
        lt, n, lf = values("LT", code), values("N", code), values("LF", code)
        out.append({
            "station": code, "date": day,
            "tmin": stat(lt, min), "tmax": stat(lt, max),
            "tmean": stat(lt, lambda v: sum(v) / len(v)),
            "n_sum": stat(n, sum),
            "gust_max": stat(values("WG.BOE", code), max, complete=False),
            "wind_mean": stat(values("WIND", code, lambda v: v[0]), lambda v: sum(v) / len(v)),
            "lf_mean": stat(lf, lambda v: sum(v) / len(v), 0),
            "lf_min": stat(lf, min, 0),
            "p_mean": stat(values("LD.RED", code), lambda v: sum(v) / len(v)),
            "sun_h": stat(values("SD", code), lambda v: sum(v) / 60),
            "gs_mean": stat(values("GS", code), lambda v: sum(v) / len(v), 0),
            "hs_max": stat(values("HS", code), max, 0, complete=False),
            "hs_end": stat(values("HS", code)[-1:], max, 0, complete=False),
        })
    return out


def write_day(out, day, per_sensor, flagged):
    start, end = local_day_bounds(day)
    hours = [start + timedelta(hours=k + 1) for k in range(int((end - start).total_seconds() // 3600))]
    rows = hourly_rows(per_sensor, flagged, hours)
    if not rows:
        print(f"{day}: no data, skipped")
        return
    hourly_path = out / f"hourly/{day:%Y/%m}/{day}.parquet"
    daily_path = out / f"daily/{day:%Y}/{day}.parquet"
    hourly_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_arrays([pa.array(col) for col in zip(*rows)], schema=HOURLY_SCHEMA)
    pq.write_table(table, hourly_path, compression="zstd")
    daily = daily_rows(per_sensor, hours, day)
    daily_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(daily), daily_path, compression="zstd")
    print(f"{day}: {len(rows)} hourly values, {len(daily)} stations, "
          f"{hourly_path.stat().st_size // 1024} kB + {daily_path.stat().st_size // 1024} kB")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="local day YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--from", dest="first", help="first local day of a range")
    parser.add_argument("--to", dest="last", help="last local day of a range (default: yesterday)")
    parser.add_argument("--out", default="archive", help="checkout of the data branch")
    parser.add_argument("--force", action="store_true", help="overwrite archived days")
    parser.add_argument("--block", type=int, default=10, help="days fetched per request")
    parser.add_argument("--catch-up", type=int, default=30, help="days to look back without --date/--from")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    yesterday = datetime.now(LOCAL).date() - timedelta(days=1)
    out = Path(args.out)
    if args.first:
        first, last = date.fromisoformat(args.first), date.fromisoformat(args.last) if args.last else yesterday
    elif args.date:
        first = last = date.fromisoformat(args.date)
    else:
        # From the day after the newest archived one; an empty archive starts with yesterday.
        archived = [date.fromisoformat(p.stem) for p in out.glob("hourly/*/*/*.parquet")]
        last = yesterday
        first = yesterday
        if archived:
            first = max(max(archived) + timedelta(days=1), yesterday - timedelta(days=args.catch_up - 1))
    if local_day_bounds(last)[1] > datetime.now(LOCAL):
        sys.exit(f"{last} is not over yet")
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    todo = [d for d in days if args.force or not (out / f"hourly/{d:%Y/%m}/{d}.parquet").exists()]
    if not todo:
        print("all days already archived")
        return

    geo = api.stations()
    stations = api.station_table(geo)
    pairs = [p for p in api.available_pairs(api.sensors()) if p[0] in stations]
    for b in range(0, len(todo), args.block):
        block = todo[b:b + args.block]
        start, end = local_day_bounds(block[0])[0], local_day_bounds(block[-1])[1]
        raw, errors = fetch_day(pairs, start, end, args.workers)
        if len(errors) > 0.1 * len(pairs):
            sys.exit(f"{len(errors)} of {len(pairs)} requests failed, not archiving incomplete days:\n"
                     + "\n".join(errors[:10]))
        per_sensor, flagged = hourly.build(raw)
        for day in block:
            write_day(out, day, per_sensor, flagged)
        if errors:
            print(f"{len(errors)} failed requests in {block[0]}–{block[-1]}")

    (out / "stations.json").write_text(
        json.dumps(sorted(stations.values(), key=lambda s: s["code"]), ensure_ascii=False, indent=1) + "\n")


if __name__ == "__main__":
    main()
