"""Fetch station data from the South Tyrol weather service (Landeswetterdienst).

Uses the open Meteo API of the Autonomous Province of Bolzano:
https://geoservices.buergernetz.bz.it/services/meteo/v1/
(the old daten.buergernetz.bz.it host was retired in 2026)

Merges each run into data/raw/timeseries/<station>__<sensor>.json, so
repeated runs extend the record and a passing weather event stays
reproducible after the API stops serving it.

Stdlib only, so it runs on a bare CI runner.
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://geoservices.buergernetz.bz.it/services/meteo/v1"

# LT = air temperature, N = precipitation, WG = wind speed,
# WR = wind direction, WG.BOE = gust, LF = relative humidity, LD.RED = pressure
SENSORS = ["LT", "N", "WG", "WR", "WG.BOE", "LF", "LD.RED"]


def get_json(path, params=None, retries=3):
    url = f"{BASE}/{path}"
    if params:
        url += "?" + urlencode(params)
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": "coldfront/0.1", "Accept": "application/json"})
            with urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == retries - 1:
                raise RuntimeError(f"{url}: {exc}") from exc
            time.sleep(2 ** (attempt + 1))


def station_codes(stations):
    features = stations.get("features", stations) if isinstance(stations, dict) else stations
    codes = []
    for f in features:
        props = f.get("properties", f)
        code = props.get("SCODE") or props.get("scode")
        if code:
            codes.append(code)
    return codes


def available_pairs(sensors):
    """(station, sensor) pairs the API reports as existing, limited to SENSORS."""
    pairs = set()
    for s in sensors:
        code, typ = s.get("SCODE"), s.get("TYPE")
        if code and typ in SENSORS:
            pairs.add((code, typ))
    return sorted(pairs)


def merge(path, data):
    """Merge API records into a [[date, value], ...] file, newest data winning."""
    rows = dict(json.loads(path.read_text())) if path.exists() else {}
    for rec in data:
        rows[rec["DATE"]] = rec["VALUE"]
    # Dates carry their own CET/CEST suffix; sort on the numeric part.
    merged = sorted(rows.items(), key=lambda kv: kv[0][:19])
    path.write_text("[\n" + ",\n".join(json.dumps(r, ensure_ascii=False) for r in merged) + "\n]\n")
    return len(data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=72)
    parser.add_argument("--out", default="data/raw")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=args.hours)
    out = Path(args.out)
    (out / "timeseries").mkdir(parents=True, exist_ok=True)

    stations = get_json("stations")
    (out / "stations.json").write_text(json.dumps(stations, ensure_ascii=False))
    sensors = get_json("sensors")
    (out / "sensors.json").write_text(json.dumps(sensors, ensure_ascii=False))

    pairs = available_pairs(sensors)
    if not pairs:
        # Fall back to asking for every sensor at every station.
        pairs = [(c, s) for c in station_codes(stations) for s in SENSORS]
    print(f"{len(station_codes(stations))} stations, {len(pairs)} station/sensor pairs")

    # The API interprets YYYYMMDDHHMM bounds as local time (Europe/Rome).
    local = ZoneInfo("Europe/Rome")
    date_from = start.astimezone(local).strftime("%Y%m%d%H%M")
    date_to = now.astimezone(local).strftime("%Y%m%d%H%M")

    def fetch(pair):
        code, typ = pair
        try:
            data = get_json("timeseries", {
                "station_code": code,
                "sensor_code": typ,
                "output_format": "JSON",
                "date_from": date_from,
                "date_to": date_to,
            })
        except RuntimeError as exc:
            return pair, None, str(exc)
        if not isinstance(data, list):
            return pair, None, f"{code}/{typ}: unexpected response"
        return pair, merge(out / "timeseries" / f"{code}__{typ}.json", data), None

    errors, empty = [], 0
    with ThreadPoolExecutor(args.workers) as pool:
        for (code, typ), n, err in pool.map(fetch, pairs):
            if err:
                errors.append(err)
            elif not n:
                empty += 1

    manifest = {
        "fetched_at": now.isoformat(),
        "date_from": date_from,
        "date_to": date_to,
        "pairs": len(pairs),
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
