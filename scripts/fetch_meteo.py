"""Fetch station data from the South Tyrol weather service (Landeswetterdienst).

Uses the open Meteo API of the Autonomous Province of Bolzano:
https://daten.buergernetz.bz.it/services/meteo/v1/

Writes raw API responses to data/raw/<run-timestamp>/ so a passing weather
event stays reproducible even after the API stops serving it.

Stdlib only, so it runs on a bare CI runner.
"""

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://daten.buergernetz.bz.it/services/meteo/v1"

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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=72)
    parser.add_argument("--out", default="data/raw")
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=args.hours)
    out = Path(args.out) / now.strftime("%Y%m%dT%H%MZ")
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

    # The API expects local-time-free YYYYMMDDHHMM bounds.
    date_from = start.strftime("%Y%m%d%H%M")
    date_to = now.strftime("%Y%m%d%H%M")

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
        name = f"{code}__{typ}.json"
        (out / "timeseries" / name).write_text(json.dumps(data, ensure_ascii=False))
        return pair, len(data) if isinstance(data, list) else None, None

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
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps({k: v for k, v in manifest.items() if k != "errors"}, indent=2))
    print(f"{len(errors)} errors", *errors[:10], sep="\n")
    if len(errors) == len(pairs):
        sys.exit(1)


if __name__ == "__main__":
    main()
