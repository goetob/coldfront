"""Archive finished local days as Parquet, for the data branch.

Fetches the days afresh from the API (independent of the rolling cache, and
late reports have arrived by then), runs the same QC and hourly aggregation
as the rolling pipeline, and writes immutable files per day:

  hourly/YYYY/MM/YYYY-MM-DD.parquet   station, time (UTC, hour ending), var, value, flagged
  daily/YYYY/YYYY-MM-DD.parquet       one row per station: extremes, sums, means
  hourly/YYYY-MM.parquet, daily/YYYY-MM.parquet   the same for a whole month
  stations.json                       station list (its git history records changes)

Layout and reading in meteo/archive.py.

Hourly vars are the sensor codes, with wind as WG (scalar mean m/s) and WR
(direction of the vector mean). An hour belongs to the day it ends in.

  (no day given)      every day since the newest archived one up to yesterday,
                      at most --catch-up days back; scheduled runs that GitHub
                      skips or delays are made good by the next one
  --date D            one day
  --from D --to D     a range: one request per station and sensor for each
                      block of days instead of one per day
  --month YYYY-MM     the missing days of a month in one block, written into
                      the month file (the backfill); --compact also folds the
                      month's day files into it
  --todo --from --to  print the months with missing days, newest first
  --compact           (daily run) fold the day files of months that are over
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).resolve().parent))
from meteo import api, archive, hourly  # noqa: E402
from meteo.timeutil import LOCAL, local_day_bounds, parse_api_date  # noqa: E402

# A daily statistic needs this share of the day's hours, otherwise it is left empty.
DAILY_COMPLETE = 0.9
# Fetched before the days archived and dropped: on bare ground new snow is told
# from grass by the readings of the day before it (meteo/qc.snow_bare).
LEAD = timedelta(days=1)


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


def day_tables(day, per_sensor, flagged):
    """Hourly and daily table of one local day, or None without data."""
    start, end = local_day_bounds(day)
    hours = [start + timedelta(hours=k + 1) for k in range(int((end - start).total_seconds() // 3600))]
    rows = hourly_rows(per_sensor, flagged, hours)
    if not rows:
        print(f"{day}: no data, skipped")
        return None
    table = pa.Table.from_arrays([pa.array(col) for col in zip(*rows)], schema=archive.HOURLY_SCHEMA)
    daily = pa.Table.from_pylist(daily_rows(per_sensor, hours, day), schema=archive.DAILY_SCHEMA)
    return table, daily


def fetch_block(pairs, block, workers, out=None, alt=None):
    """Hourly and daily tables per day of a contiguous block of days; `out` is the archive, alt {code: elevation}."""
    start, end = local_day_bounds(block[0])[0] - LEAD, local_day_bounds(block[-1])[1]
    raw, errors = fetch_day(pairs, start, end, workers)
    if len(errors) > 0.1 * len(pairs):
        sys.exit(f"{len(errors)} of {len(pairs)} requests failed, not archiving incomplete days:\n"
                 + "\n".join(errors[:10]))
    if errors:
        print(f"{len(errors)} failed requests in {block[0]}–{block[-1]}")
    per_sensor, flagged = hourly.build(raw, hourly.bare_before(out, start), alt=alt)
    return {d: t for d in block if (t := day_tables(d, per_sensor, flagged))}


def network():
    geo = api.stations()
    stations = api.station_table(geo)
    pairs = [p for p in api.available_pairs(api.sensors()) if p[0] in stations]
    return stations, pairs


def elevations(stations):
    return {code: s["alt"] for code, s in stations.items()}


def write_stations(out, stations):
    (out / "stations.json").write_text(
        json.dumps(sorted(stations.values(), key=lambda s: s["code"]), ensure_ascii=False, indent=1) + "\n")


def freeze_month(out, month, yesterday, force, fold, workers):
    """Fetch the month's missing days in one block and write them into the month file."""
    days = [d for d in archive.month_days(month) if d <= yesterday]
    todo = days if force else archive.missing_days(out, month, yesterday)
    fresh = {}
    if todo:
        stations, pairs = network()
        # One block from the first to the last missing day: one request per pair.
        span = [todo[0] + timedelta(days=i) for i in range((todo[-1] - todo[0]).days + 1)]
        fresh = {d: t for d, t in fetch_block(pairs, span, workers, out, elevations(stations)).items() if d in set(todo)}
        write_stations(out, stations)
    if not fresh and not fold:
        print(f"{month[0]}-{month[1]:02d}: nothing to do")
        return
    hourly_t = pa.concat_tables([t[0] for t in fresh.values()]) if fresh else None
    daily_t = pa.concat_tables([t[1] for t in fresh.values()]) if fresh else None
    archive.write_month(out, month, hourly_t, daily_t, fold_days=fold)
    path = archive.month_path(out, "hourly", month)
    if path.exists():
        print(f"{month[0]}-{month[1]:02d}: {len(fresh)} days fetched, "
              f"{path.stat().st_size // 1024} kB + {archive.month_path(out, 'daily', month).stat().st_size // 1024} kB")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="local day YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--from", dest="first", help="first local day of a range")
    parser.add_argument("--to", dest="last", help="last local day of a range (default: yesterday)")
    parser.add_argument("--month", help="local month YYYY-MM, written as one month file")
    parser.add_argument("--todo", action="store_true", help="list the months of --from/--to with missing days")
    parser.add_argument("--compact", action="store_true", help="fold day files of finished months")
    parser.add_argument("--out", default="archive", help="checkout of the data branch")
    parser.add_argument("--force", action="store_true", help="overwrite archived days")
    parser.add_argument("--block", type=int, default=10, help="days fetched per request")
    parser.add_argument("--catch-up", type=int, default=30, help="days to look back without --date/--from")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    yesterday = datetime.now(LOCAL).date() - timedelta(days=1)
    out = Path(args.out)
    if args.month:
        y, m = map(int, args.month.split("-"))
        last = min(date.fromisoformat(args.last), yesterday) if args.last else yesterday
        freeze_month(out, (y, m), last, args.force, args.compact, args.workers)
        return
    if args.todo:
        first = date.fromisoformat(args.first)
        last = date.fromisoformat(args.last) if args.last else yesterday
        for month in archive.months(first, last)[::-1]:
            if archive.missing_days(out, month, min(last, yesterday)):
                print(f"{month[0]}-{month[1]:02d}")
        return
    if args.first:
        first, last = date.fromisoformat(args.first), date.fromisoformat(args.last) if args.last else yesterday
    elif args.date:
        first = last = date.fromisoformat(args.date)
    else:
        # From the day after the newest archived one; an empty archive starts with yesterday.
        archived = archive.archived_days(out)
        last = yesterday
        first = yesterday
        if archived:
            first = max(max(archived) + timedelta(days=1), yesterday - timedelta(days=args.catch_up - 1))
    if local_day_bounds(last)[1] > datetime.now(LOCAL):
        sys.exit(f"{last} is not over yet")
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    have = archive.archived_days(out)
    todo = [d for d in days if args.force or d not in have]
    if todo:
        stations, pairs = network()
        for b in range(0, len(todo), args.block):
            for day, (h, d) in fetch_block(pairs, todo[b:b + args.block], args.workers, out, elevations(stations)).items():
                archive.write_day(out, day, h, d)
                print(f"{day}: {h.num_rows} hourly values, {d.num_rows} stations")
        write_stations(out, stations)
    else:
        print("all days already archived")

    if args.compact:
        # Months before the current one are over; their day files go into the month file.
        this_month = (datetime.now(LOCAL).date().year, datetime.now(LOCAL).date().month)
        for month in sorted({(d.year, d.month) for d in archive.day_files(out)}):
            if month < this_month:
                freeze_month(out, month, yesterday, False, True, args.workers)


if __name__ == "__main__":
    main()
