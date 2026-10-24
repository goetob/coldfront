"""Daily maps and station histories from the archive on the data branch.

Reads the Parquet files that freeze_day.py writes and the current hourly
window, and writes

  web/public/archive/<var>/<date>.webp   tmax, tmin, nsum per local day (+ -s thumbnails)
  web/src/data/archive.json              the days, newest first, with their key figures
  web/src/data/stations.json             the station list
  web/src/data/stations/<code>.json      last 7 days hourly and 30 days daily per station

Days never change once archived, so their maps are drawn once and kept;
those older than --days are removed.
"""

import argparse
import json
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

import fields
import render_maps as rm
from meteo import wind as windlib
from meteo.timeutil import iso, parse_iso

ROOT = Path(__file__).resolve().parent.parent
MAPS = ("tmax", "tmin", "nsum")
HOURLY_VARS = ("LT", "N", "WG", "WR", "WG.BOE", "LF", "LD.RED", "HS", "SD")
DAILY_VARS = ("tmin", "tmax", "tmean", "n_sum", "gust_max", "wind_mean", "lf_min", "sun_h", "hs_end")


def read_daily(archive, days):
    out = {}
    for d in days:
        path = archive / f"daily/{d:%Y}/{d}.parquet"
        if path.exists():
            out[d] = {r["station"]: r for r in pq.read_table(path).to_pylist()}
    return out


def read_hourly(archive, days):
    """{(station, var): {time: value}} from the archived hourly files."""
    out = {}
    for d in days:
        path = archive / f"hourly/{d:%Y/%m}/{d}.parquet"
        if not path.exists():
            continue
        t = pq.read_table(path).to_pydict()
        for code, time, var, value in zip(t["station"], t["time"], t["var"], t["value"]):
            out.setdefault((code, var), {})[time.astimezone(timezone.utc)] = value
    return out


_CTX = {}


def render_day(d):
    """Maps and key figures of one archived day."""
    stations, daily, terrain, r, redraw = (_CTX[k] for k in ("stations", "daily", "terrain", "renderer", "redraw"))
    rows = daily[d]
    stamp = d.isoformat()

    def column(key):
        return [rows.get(s["code"], {}).get(key) for s in stations]

    def xy(values):
        return ([s["x"] for s, v in zip(stations, values) if v is not None],
                [s["y"] for s, v in zip(stations, values) if v is not None])

    def extreme(key, pick):
        vals = [(v, s["name"]) for s, v in zip(stations, column(key)) if v is not None]
        return dict(zip(("value", "name"), pick(vals))) if vals else None

    figures, drawn = {}, []
    for var, key, kind in (("tmax", "tmax", "t"), ("tmin", "tmin", "t"), ("nsum", "n_sum", "n")):
        values = column(key)
        if sum(v is not None for v in values) < rm.MIN_STATIONS:
            continue
        if kind == "t":
            field, _ = fields.temperature(stations, values, terrain)
        else:
            field, _ = fields.precipitation(stations, values, terrain, method="krige")
        figures[f"{var}_area"] = round(float(field[terrain.inside].mean()), 1)
        drawn.append(var)
        if redraw or not (r.out / var / f"{stamp}.webp").exists():
            if kind == "t":
                r.temperature(field, xy(values), stamp, var=var)
            else:
                r.precipitation(field, xy(values), stamp, rm.P_DAY, var=var)
    return {
        "date": stamp,
        "maps": drawn,
        **figures,
        "warmest": extreme("tmax", max),
        "coldest": extreme("tmin", min),
        "wettest": extreme("n_sum", max),
        "windiest": extreme("gust_max", max),
        "sunniest": extreme("sun_h", max),
        "snow_stations": sum((v or 0) >= 1 for v in column("hs_end")),
        "stations": len(rows),
    }


def station_histories(stations, archive, days, daily, window, station_days, daily_days):
    """Per station: hourly values of the last days and daily values of the last weeks."""
    hourly = read_hourly(archive, days[-(station_days + 1):])
    # The rolling window carries the hours since the last archived day.
    if window:
        for sensor, by_code in window["series"].items():
            for code, values in by_code.items():
                for t, v in zip(window["times"], values):
                    if v is None:
                        continue
                    when = parse_iso(t)
                    if sensor == "WIND":
                        hourly.setdefault((code, "WG"), {})[when] = v[0]
                        hourly.setdefault((code, "WR"), {})[when] = v[1]
                    elif sensor in HOURLY_VARS:
                        hourly.setdefault((code, sensor), {})[when] = v
    newest = max((t for series in hourly.values() for t in series), default=datetime.now(timezone.utc))
    times = [newest - timedelta(hours=h) for h in range(station_days * 24 - 1, -1, -1)]
    recent = [d for d in days if d in daily][-daily_days:]
    out = {}
    for st in stations:
        code = st["code"]
        series = {var: [hourly.get((code, var), {}).get(t) for t in times] for var in HOURLY_VARS}
        series = {k: v for k, v in series.items() if any(x is not None for x in v)}
        per_day = {k: [daily[d].get(code, {}).get(k) for d in recent] for k in DAILY_VARS}
        per_day = {k: v for k, v in per_day.items() if any(x is not None for x in v)}
        if not series and not per_day:
            continue
        out[code] = {
            **{k: st[k] for k in ("code", "name", "name_it", "alt", "lon", "lat", "exposure")},
            "hourly": {"times": [iso(t) for t in times], **series},
            "daily": {"dates": [d.isoformat() for d in recent], **per_day},
        }
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", default=ROOT / "archive", type=Path, help="checkout of the data branch")
    parser.add_argument("--hourly", default=ROOT / "data/processed/hourly.json", type=Path)
    parser.add_argument("--out", default=ROOT / "web/public/archive", type=Path)
    parser.add_argument("--data", default=ROOT / "web/src/data", type=Path)
    parser.add_argument("--days", type=int, default=90, help="days of daily maps")
    parser.add_argument("--station-days", type=int, default=7)
    parser.add_argument("--daily-days", type=int, default=30)
    parser.add_argument("--redraw", action="store_true", help="draw all maps again")
    parser.add_argument("--workers", type=int, default=os.cpu_count())
    args = parser.parse_args()

    archived = sorted(date.fromisoformat(p.stem) for p in args.archive.glob("daily/*/*.parquet"))
    if not archived:
        raise SystemExit(f"no archived days in {args.archive}")
    days = [d for d in archived if d > archived[-1] - timedelta(days=args.days)]
    daily = read_daily(args.archive, days)

    terrain = fields.Terrain.load(ROOT / "data/geo")
    stations = json.loads((args.archive / "stations.json").read_text())
    for st in stations:
        st["exposure"], st["tpi"] = windlib.exposure(st, terrain)
    terrain.inside, terrain.centers  # computed once, before workers fork

    r = rm.Renderer(terrain, args.out)
    _CTX.update(stations=stations, daily=daily, terrain=terrain, renderer=r, redraw=args.redraw)
    with ProcessPoolExecutor(args.workers, mp_context=multiprocessing.get_context("fork")) as pool:
        summary = list(pool.map(render_day, days))

    keep = {d.isoformat() for d in days}
    for var in MAPS:
        for f in (args.out / var).glob("*.webp"):
            if f.stem.removesuffix("-s") not in keep:
                f.unlink()

    window = json.loads(args.hourly.read_text()) if args.hourly.exists() else None
    histories = station_histories(stations, args.archive, days, daily, window, args.station_days, args.daily_days)

    args.data.mkdir(parents=True, exist_ok=True)
    (args.data / "archive.json").write_text(json.dumps({
        "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "days": summary[::-1],
        "extent": list(terrain.extent),  # xmin, xmax, ymin, ymax of the maps, EPSG:25832
        "legend": {
            "temperature": {"levels": rm.T_LEVELS.tolist(), "colors": rm.T_COLORS},
            "nsum": {"levels": rm.P_DAY, "colors": rm.P_COLORS[: len(rm.P_DAY)]},
            "wind": {"levels": windlib.LEVELS, "colors": windlib.COLORS, "names": [c[1] for c in windlib.CLASSES]},
        },
    }, ensure_ascii=False, indent=1))
    (args.data / "stations.json").write_text(json.dumps(
        [{k: s[k] for k in ("code", "name", "name_it", "alt", "x", "y", "exposure")} for s in stations
         if s["code"] in histories], ensure_ascii=False))
    sdir = args.data / "stations"
    sdir.mkdir(exist_ok=True)
    for f in sdir.glob("*.json"):
        if f.stem not in histories:
            f.unlink()
    for code, h in histories.items():
        (sdir / f"{code}.json").write_text(json.dumps(h, ensure_ascii=False, separators=(",", ":")))
    print(f"{len(days)} days ({days[0]}–{days[-1]}), {len(histories)} stations")


if __name__ == "__main__":
    main()
