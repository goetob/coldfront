"""Keep a rolling window of raw station data up to date.

Each station/sensor pair lives in data/raw/timeseries/<station>__<sensor>.json
as [[date, value], ...]. A run asks the API only for what is new since the
pair's last value (with some overlap for late reports), merges it, and drops
values older than --keep hours. data/raw is not committed: in CI it lives in
the Actions cache, and if that is lost the next run fetches the full window.

Also writes the newest value of every sensor (sensors.json), the station
list (stations.json) and appends a line to fetch_log.jsonl.

Stdlib only, so it runs on a bare CI runner.
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from meteo import api  # noqa: E402
from meteo.timeutil import parse_api_date  # noqa: E402

OVERLAP = timedelta(hours=2)


def read_rows(path):
    return json.loads(path.read_text()) if path.exists() else []


def write_rows(path, rows):
    path.write_text("[\n" + ",\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n]\n")


def merge(rows, records, oldest):
    """Merge API records into [[date, value], ...]; newest data wins, old rows go."""
    by_date = dict(rows)
    for rec in records:
        by_date[rec["DATE"]] = rec["VALUE"]
    keyed = [(parse_api_date(d), d, v) for d, v in by_date.items()]
    return [[d, v] for t, d, v in sorted(keyed) if t >= oldest]


def fetch_pairs(pairs, out, start_for, oldest, now, workers):
    """Fetch and merge every pair; returns (new rows, errors, empty pairs)."""
    def one(pair):
        code, sensor = pair
        path = out / f"{code}__{sensor}.json"
        rows = read_rows(path)
        start = start_for(rows)
        try:
            records = api.timeseries(code, sensor, start, now)
        except RuntimeError as exc:
            return 0, str(exc), False
        merged = merge(rows, records, oldest)
        if merged:
            write_rows(path, merged)
        elif path.exists():
            path.unlink()
        return len(records), None, not records

    added, errors, empty = 0, [], 0
    with ThreadPoolExecutor(workers) as pool:
        for n, err, is_empty in pool.map(one, pairs):
            added += n
            if err:
                errors.append(err)
            empty += is_empty
    return added, errors, empty


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=80,
                        help="history to fetch for a pair without local data")
    parser.add_argument("--keep", type=int, default=96, help="hours of raw data to keep")
    parser.add_argument("--out", default="data/raw")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    oldest = now - timedelta(hours=args.keep)
    out = Path(args.out)
    series_dir = out / "timeseries"
    series_dir.mkdir(parents=True, exist_ok=True)

    stations = api.stations()
    (out / "stations.json").write_text(json.dumps(stations, ensure_ascii=False))
    latest = api.sensors()
    (out / "sensors.json").write_text(json.dumps(latest, ensure_ascii=False))
    pairs = api.available_pairs(latest)

    def start_for(rows):
        if not rows:
            return now - timedelta(hours=args.hours)
        return max(now - timedelta(hours=args.hours), parse_api_date(rows[-1][0]) - OVERLAP)

    added, errors, empty = fetch_pairs(pairs, series_dir, start_for, oldest, now, args.workers)

    # Pairs the API no longer lists age out with the window.
    listed = {f"{c}__{s}.json" for c, s in pairs}
    for path in series_dir.glob("*.json"):
        if path.name not in listed:
            rows = [r for r in read_rows(path) if parse_api_date(r[0]) >= oldest]
            if rows:
                write_rows(path, rows)
            else:
                path.unlink()

    newest = max((parse_api_date(s["DATE"]) for s in latest if s.get("DATE")), default=None)
    manifest = {
        "fetched_at": now.isoformat(timespec="seconds"),
        "newest_value": newest.isoformat() if newest else None,
        "pairs": len(pairs),
        "rows": added,
        "empty": empty,
        "errors": errors,
    }
    with open(out / "fetch_log.jsonl", "a") as log:
        log.write(json.dumps(manifest) + "\n")
    print(json.dumps({k: v for k, v in manifest.items() if k != "errors"}, indent=2))
    print(f"{len(errors)} errors", *errors[:10], sep="\n")
    if len(errors) == len(pairs):
        sys.exit(1)


if __name__ == "__main__":
    main()
