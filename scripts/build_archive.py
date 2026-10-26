"""Daily maps and station histories from the archive on the data branch.

Reads the Parquet files that freeze_day.py writes and the current hourly
window, and writes

  web/public/archive/<var>/<date>.webp   tmax, tmin, nsum per local day (+ -s thumbnails),
                                         tanom (daily mean against its normal) once there are normals
  web/src/data/archive.json              the days, newest first, with their key figures,
                                         anomalies and how they rank among all archived years
  web/src/data/stations.json             the station list
  web/src/data/stations/<code>.json      last 7 days hourly and 30 days daily per station

Days never change once archived, so their maps are drawn once and kept;
those older than --days are removed. Anomaly maps are drawn again when the
reference period of the normals changes.

Only the daily values of all years and the hourly values of the last days
are read; the normals are precomputed by build_normals.py.
"""

import argparse
import hashlib
import json
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import fields
import render_maps as rm
from meteo import archive as arc
from meteo import climate
from meteo import wind as windlib
from meteo.timeutil import iso, parse_iso

ROOT = Path(__file__).resolve().parent.parent
MAPS = ("tmax", "tmin", "nsum", "tanom")
HOURLY_VARS = ("LT", "N", "WG", "WR", "WG.BOE", "LF", "LD.RED", "HS", "SD")
DAILY_VARS = ("tmin", "tmax", "tmean", "n_sum", "gust_max", "wind_mean", "lf_min", "sun_h", "hs_end")


def read_hourly(archive, days):
    """{(station, var): {time: value}} from the archived hourly files."""
    t = arc.read(archive, "hourly", days, columns=["station", "time", "var", "value"]).to_pydict()
    out = {}
    for code, time, var, value in zip(t["station"], t["time"], t["var"], t["value"]):
        out.setdefault((code, var), {})[time.astimezone(timezone.utc)] = value
    return out


def fingerprint(rows):
    """Digest of a day's daily values, to notice a corrected archive."""
    text = json.dumps(sorted(rows.items()), sort_keys=True, default=str)
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def r1(v):
    return None if v is None or not np.isfinite(v) else round(float(v), 1)


_CTX = {}


def render_day(d):
    """Maps and key figures of one archived day."""
    stations, daily, terrain, r, redraw = (_CTX[k] for k in ("stations", "daily", "terrain", "renderer", "redraw"))
    normals, redraw_anom = _CTX["normals"], _CTX["redraw_anom"]
    redraw = redraw or d in _CTX["changed"]
    rows = daily[d]
    stamp = d.isoformat()

    def column(key):
        return [rows.get(s["code"], {}).get(key) for s in stations]

    def xy(values):
        return ([s["x"] for s, v in zip(stations, values) if v is not None],
                [s["y"] for s, v in zip(stations, values) if v is not None])

    def extreme(key, pick):
        vals = [(v, s["name"], s.get("name_it") or s["name"]) for s, v in zip(stations, column(key)) if v is not None]
        return dict(zip(("value", "name", "name_it"), pick(vals))) if vals else None

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
    anomaly = None
    if normals:
        # Daily mean against its normal; the elevation is in the normal already.
        normal = normals.at("tmean", d)
        values = [None if v is None or not np.isfinite(n) else v - n for v, n in zip(column("tmean"), normal)]
        if sum(v is not None for v in values) >= rm.MIN_STATIONS:
            field, _ = fields.plain(stations, values, terrain, range_m=40000, nugget_frac=0.2)
            figures["tanom_area"] = round(float(field[terrain.inside].mean()), 1)
            drawn.append("tanom")
            if redraw or redraw_anom or not (r.out / "tanom" / f"{stamp}.webp").exists():
                r.banded(field, xy(values), "tanom", stamp, rm.TA_LEVELS, rm.TA_COLORS, lines=[0])
            p10, p90 = normals.at("tmean_p10", d), normals.at("tmean_p90", d)
            have = [(v, lo, hi) for v, lo, hi in zip(column("tmean"), p10, p90)
                    if v is not None and np.isfinite(lo) and np.isfinite(hi)]
            anomaly = {"stations": len(have), "above_p90": int(sum(v > hi for v, _, hi in have)),
                       "below_p10": int(sum(v < lo for v, lo, _ in have))}
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
        "anomaly": anomaly,
    }


def day_climate(days, history, normals):
    """Per archived day: network anomalies and what stands out against all years."""
    net = climate.network(history, normals)
    dates = history.dates
    t = (net["tmean"], net["tmin"], net["tmax"])
    out = {}
    for d in days:
        i = history.index(d)
        if i is None:
            continue
        out[d.isoformat()] = {
            "t_anom": r1(net["tmean_anom"][i]), "tmax_anom": r1(net["tmax_anom"][i]),
            "tmin_anom": r1(net["tmin_anom"][i]), "n_net": r1(net["n_sum"][i]),
            **climate.notable(t, net["n_sum"], dates, i, climate.WET_NETWORK),
        }
    return out


def station_climate(code, recent, history, normals):
    """Normals of the shown days and what stands out at one station."""
    k = history.codes.index(code)
    meta = normals.station(code)
    out = {"years": meta.get("years_t", 0), "quality": meta["quality"], "period": normals.period}
    if meta["quality"] == "none":
        return out
    for f in ("tmean", "tmin", "tmax", "n_mean"):
        out[f"norm_{f}"] = [r1(normals.at(f, d)[k]) for d in recent]
    out["norm_n_freq"] = [None if not np.isfinite(v := normals.at("n_freq", d)[k]) else round(float(v), 2)
                          for d in recent]
    dates = history.dates
    t = tuple(history.values[f][:, k] for f in ("tmean", "tmin", "tmax"))
    n = history.values["n_sum"][:, k]
    notes = {}
    for d in recent:
        i = history.index(d)
        if i is not None and (x := climate.notable(t, n, dates, i, climate.WET_STATION)):
            notes[d.isoformat()] = x
    out["notable"] = notes
    return out


def station_histories(stations, archive, days, daily, window, station_days, daily_days, history=None, normals=None):
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
        if normals and history and code in history.codes:
            out[code]["climate"] = station_climate(code, recent, history, normals)
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
    parser.add_argument("--figures", default=ROOT / "data/processed/archive-days.json", type=Path,
                        help="key figures of the days drawn, kept with the maps between runs")
    parser.add_argument("--redraw", action="store_true", help="draw all maps again")
    parser.add_argument("--workers", type=int, default=os.cpu_count())
    args = parser.parse_args()

    archived = sorted(arc.archived_days(args.archive, "daily"))
    if not archived:
        raise SystemExit(f"no archived days in {args.archive}")
    days = [d for d in archived if d > archived[-1] - timedelta(days=args.days)]
    daily = arc.read_daily(args.archive, days)
    days = [d for d in days if d in daily]

    terrain = fields.Terrain.load(ROOT / "data/geo")
    stations = json.loads((args.archive / "stations.json").read_text())
    for st in stations:
        st["exposure"], st["tpi"] = windlib.exposure(st, terrain)
    terrain.inside, terrain.centers  # computed once, before workers fork

    codes = [s["code"] for s in stations]
    normals = climate.Normals.load(args.archive / "normals", codes)
    history = climate.History.load(args.archive, codes) if normals else None
    period = normals.period if normals else None

    # The key figures of a day rarely change; with its maps they are kept beside
    # them, so a run only interpolates the days it has not seen. A fingerprint of
    # the day's values catches the archive being corrected (recheck.py):
    # such a day is computed and drawn again.
    figures_file = args.figures
    kept = json.loads(figures_file.read_text()) if figures_file.exists() else {}
    # Anomaly maps follow the reference period of the normals.
    redraw_anom = kept.get("period") != period
    cached = kept.get("days", {}) if not (args.redraw or redraw_anom) else {}
    digests = {d.isoformat(): fingerprint(daily[d]) for d in days}
    changed = {d for d in days if d.isoformat() in cached
               and kept.get("digests", {}).get(d.isoformat()) != digests[d.isoformat()]}

    def complete(d):
        stamp = d.isoformat()
        return (stamp in cached and d not in changed
                and all((args.out / v / f"{stamp}.webp").exists() for v in cached[stamp]["maps"]))

    todo = [d for d in days if not complete(d)]
    r = rm.Renderer(terrain, args.out)
    _CTX.update(stations=stations, daily=daily, terrain=terrain, renderer=r, redraw=args.redraw,
                normals=normals, redraw_anom=redraw_anom, changed=changed)
    with ProcessPoolExecutor(args.workers, mp_context=multiprocessing.get_context("fork")) as pool:
        fresh = dict(zip(todo, pool.map(render_day, todo)))
    summary = [fresh[d] if d in fresh else cached[d.isoformat()] for d in days]
    figures_file.parent.mkdir(parents=True, exist_ok=True)
    figures_file.write_text(json.dumps({"period": period, "days": {x["date"]: x for x in summary},
                                        "digests": digests}, ensure_ascii=False))
    print(f"{len(todo)} days interpolated ({len(changed)} changed in the archive), "
          f"{len(days) - len(todo)} from earlier runs")

    if normals:
        per_day = day_climate(days, history, normals)
        for day in summary:
            day["climate"] = per_day.get(day["date"])

    keep = {d.isoformat() for d in days}
    for var in MAPS:
        for f in (args.out / var).glob("*.webp"):
            if f.stem.removesuffix("-s") not in keep:
                f.unlink()

    window = json.loads(args.hourly.read_text()) if args.hourly.exists() else None
    histories = station_histories(stations, args.archive, days, daily, window, args.station_days, args.daily_days,
                                  history, normals)

    args.data.mkdir(parents=True, exist_ok=True)
    (args.data / "archive.json").write_text(json.dumps({
        "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "days": summary[::-1],
        "normals": {"period": normals.period, "window_days": normals.meta["window_days"],
                    "stations": int(normals.usable.sum())} if normals else None,
        "extent": list(terrain.extent),  # xmin, xmax, ymin, ymax of the maps, EPSG:25832
        "legend": {
            "temperature": {"levels": rm.T_LEVELS.tolist(), "colors": rm.T_COLORS},
            "nsum": {"levels": rm.P_DAY, "colors": rm.P_COLORS[: len(rm.P_DAY)]},
            "tanom": {"levels": rm.TA_LEVELS, "colors": rm.TA_COLORS},
            "wind": {"levels": windlib.LEVELS, "colors": windlib.COLORS, "names": [c[1] for c in windlib.CLASSES]},
        },
    }, ensure_ascii=False, indent=1))
    (args.data / "stations.json").write_text(json.dumps(
        [{**{k: s[k] for k in ("code", "name", "name_it", "alt", "x", "y", "exposure")},
          **({"years": normals.station(s["code"])["years_t"], "quality": normals.station(s["code"])["quality"]}
             if normals else {})}
         for s in stations if s["code"] in histories], ensure_ascii=False))
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
