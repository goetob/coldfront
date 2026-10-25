"""Weather events found automatically in the archive and the rolling window.

Every hour is tested against a few rules on the station network as a whole;
flagged hours less than a day apart form one event, if at least two hours
are flagged. The thresholds were chosen on the first 90 archived days
(July to October 2026) and checked on the whole archive back to 2015:

  cold   the mean temperature of the last 24 hours, median of the stations,
         at least 4 K below that of the 24 hours before
  rain   24-hour precipitation, mean of the stations >= 20 mm
  storm  gusts above the station's own 99.5th percentile (and at least
         17.2 m/s) at >= 5 stations
  snow   snow depth up >= 20 cm in 24 hours at >= 3 stations
  heat   32 °C or more at >= 15 stations

Over ten years the first rules of a fixed gust (Beaufort 9 at three
stations) and a fixed hourly drop (6 K) fired about 20 and 16 times a year:
the summits above 2800 m have such gusts on most winter days, and a
thunderstorm cools single afternoons as much. Each station's own gust
percentile and the 24-hour mean keep the clearly remarkable days, about
five storms and twelve cold spells a year. A station's percentile needs a
year of gusts, otherwise the fixed GUST applies; the whole-archive run
stores the percentiles with its events, so the rolling run, which reads
only the last months, uses the same ones. Writes web/src/data/detected.json; the words are added by
the site in either language.
"""

import argparse
import json
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

from meteo import archive as arc
from meteo.timeutil import iso, parse_iso

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ZoneInfo("Europe/Rome")
VARS = ("LT", "N", "WG.BOE", "HS")

COLD_DROP = -4.0   # change of the 24-hour mean (K), median of the stations
RAIN_AREA = 20.0
GUST_PCT, GUST_FLOOR, GUST_STATIONS = 99.5, 17.2, 5
GUST = 20.8        # Beaufort 9, for stations with less than a year of gusts
GUST_RECORD = 365 * 24
SNOW, SNOW_STATIONS = 20.0, 3
HEAT, HEAT_STATIONS = 32.0, 15
GAP_HOURS = 24
MIN_HOURS = 2       # flagged hours an event needs; single hours are mostly one gusty peak
ONGOING_HOURS = 3   # an event flagged this close to the newest hour may go on
TYPES = ("cold", "rain", "snow", "storm", "heat")  # order when two set in the same hour


def load(archive, window, codes, days=None):
    """{var: array[hour, station]} on one hourly UTC axis, and the axis.

    `days` limits the archive to those local days (all if None).
    """
    t = (arc.read(archive, "hourly", days, columns=["station", "time", "var", "value", "flagged"])
         if archive else None)
    if t is not None:
        t = t.filter(pc.and_(pc.equal(t["flagged"], 0), pc.is_in(t["var"], value_set=pa.array(VARS))))
    starts, ends = [], []
    if t is not None and t.num_rows:
        starts.append(pc.min(t["time"]).as_py())
        ends.append(pc.max(t["time"]).as_py())
    if window:
        starts.append(parse_iso(window["times"][0]))
        ends.append(parse_iso(window["times"][-1]))
    if not starts:
        raise SystemExit("no hourly data")
    t0 = min(starts).astimezone(timezone.utc)
    hours = int((max(ends) - t0).total_seconds() // 3600) + 1
    col = {c: i for i, c in enumerate(codes)}
    data = {v: np.full((hours, len(codes)), np.nan) for v in VARS}

    if t is not None and t.num_rows:
        secs = pc.cast(t["time"], "int64").to_numpy()
        row = (secs - int(t0.timestamp())) // 3600
        station = pc.fill_null(pc.index_in(t["station"], value_set=pa.array(codes)), -1).to_numpy()
        var = pc.index_in(t["var"], value_set=pa.array(VARS)).to_numpy()
        value = t["value"].to_numpy(zero_copy_only=False)
        keep = station >= 0
        for i, v in enumerate(VARS):
            m = keep & (var == i)
            data[v][row[m], station[m]] = value[m]

    # The rolling window carries the hours since the last archived day.
    if window:
        rows = [int((parse_iso(s) - t0).total_seconds() // 3600) for s in window["times"]]
        for v in VARS:
            for code, values in window["series"].get(v, {}).items():
                if code not in col:
                    continue
                for r, x in zip(rows, values):
                    if x is not None:
                        data[v][r, col[code]] = x
    times = [t0 + timedelta(hours=h) for h in range(hours)]
    return data, times


def _lag(a, hours):
    out = np.full_like(a, np.nan)
    out[hours:] = a[:-hours]
    return out


def _nan_stat(fn, a, least):
    """fn over the stations of every hour; NaN where fewer than `least` report."""
    n = np.isfinite(a).sum(axis=1)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN hours
        out = fn(a, axis=1)
    out[n < least] = np.nan
    return out


def rolling_sum(a, hours, least):
    """Sum over the last `hours` hours per station, NaN with fewer than `least` values."""
    ok = np.isfinite(a)
    c = np.vstack([np.zeros((1, a.shape[1])), np.cumsum(np.where(ok, a, 0.0), axis=0)])
    k = np.vstack([np.zeros((1, a.shape[1])), np.cumsum(ok, axis=0)])
    s = np.full_like(a, np.nan)
    s[hours - 1:] = c[hours:] - c[:-hours]
    n = np.zeros_like(a)
    n[hours - 1:] = k[hours:] - k[:-hours]
    s[n < least] = np.nan
    return s


def gust_thresholds(gust):
    """Per station the gust that counts: its own GUST_PCT percentile, at least GUST_FLOOR."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        pct = np.nanpercentile(gust, GUST_PCT, axis=0)
    long_enough = np.isfinite(gust).sum(axis=0) >= GUST_RECORD
    return np.where(long_enough, np.maximum(pct, GUST_FLOOR), GUST)


def mean_24(a, least=20):
    """Mean of the last 24 hours per station, NaN with fewer than `least` values."""
    total = rolling_sum(a, 24, least)
    count = rolling_sum(np.isfinite(a).astype(float), 24, 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return total / count


def indicators(data, gust_thr=None):
    """Per hour the network values the rules test, and per station what lies behind them."""
    lt, n, gust, hs = (data[v] for v in VARS)
    if gust_thr is None:
        gust_thr = gust_thresholds(gust)
    mean24 = mean_24(lt)
    d_t = mean24 - _lag(mean24, 24)
    p24 = rolling_sum(n, 24, 20)
    d_hs = hs - _lag(hs, 24)
    with np.errstate(invalid="ignore"):
        return {
            "station": {"d_t": d_t, "p24": p24, "gust": gust, "d_hs": d_hs, "t": lt},
            "d_t": _nan_stat(np.nanmedian, d_t, 20),
            "p24": _nan_stat(np.nanmean, p24, 10),
            "gust_n": (gust >= gust_thr).sum(axis=1),
            "snow_n": (d_hs >= SNOW).sum(axis=1),
            "heat_n": (lt >= HEAT).sum(axis=1),
        }


def flags(ind):
    with np.errstate(invalid="ignore"):
        return {
            "cold": ind["d_t"] <= COLD_DROP,
            "rain": ind["p24"] >= RAIN_AREA,
            "storm": ind["gust_n"] >= GUST_STATIONS,
            "snow": ind["snow_n"] >= SNOW_STATIONS,
            "heat": ind["heat_n"] >= HEAT_STATIONS,
        }


def clusters(any_flag, gap=GAP_HOURS):
    """(first, last) hour index of runs of flagged hours less than `gap` hours apart."""
    hit = np.flatnonzero(any_flag)
    if not hit.size:
        return []
    breaks = np.flatnonzero(np.diff(hit) > gap)
    starts = np.concatenate([[hit[0]], hit[breaks + 1]])
    ends = np.concatenate([hit[breaks], [hit[-1]]])
    return list(zip(starts.tolist(), ends.tolist()))


def describe(a, b, ind, fl, times, stations):
    """Key figures of the event spanning hours a..b."""
    sl = slice(a, b + 1)
    st = ind["station"]

    def at_station(key, hour, pick):
        row = st[key][hour]
        if not np.isfinite(row).any():
            return None
        i = int(pick(np.where(np.isfinite(row), row, np.inf if pick is np.argmin else -np.inf)))
        s = stations[i]
        return {"value": round(float(row[i]), 1), "name": s["name"], "name_it": s.get("name_it") or s["name"]}

    def peak_hour(series, pick):
        part = np.where(np.isfinite(series[sl]), series[sl], np.inf if pick is np.argmin else -np.inf)
        return a + int(pick(part))

    found = {}
    if fl["cold"][sl].any():
        h = peak_hour(ind["d_t"], np.argmin)
        found["cold"] = {"peak": iso(times[h]), "median": round(float(ind["d_t"][h]), 1),
                         "station": at_station("d_t", h, np.argmin)}
    if fl["rain"][sl].any():
        h = peak_hour(ind["p24"], np.argmax)
        found["rain"] = {"peak": iso(times[h]), "mean": round(float(ind["p24"][h]), 1),
                         "station": at_station("p24", h, np.argmax)}
    if fl["storm"][sl].any():
        h = peak_hour(ind["gust_n"].astype(float), np.argmax)
        g = st["gust"][sl]
        hh = a + int(np.unravel_index(np.nanargmax(np.where(np.isfinite(g), g, -np.inf)), g.shape)[0])
        found["storm"] = {"peak": iso(times[h]), "stations": int(ind["gust_n"][h]),
                          "station": at_station("gust", hh, np.argmax)}
    if fl["snow"][sl].any():
        h = peak_hour(ind["snow_n"].astype(float), np.argmax)
        found["snow"] = {"peak": iso(times[h]), "stations": int(ind["snow_n"][h]),
                         "station": at_station("d_hs", h, np.argmax)}
    if fl["heat"][sl].any():
        h = peak_hour(ind["heat_n"].astype(float), np.argmax)
        t = st["t"][sl]
        hh = a + int(np.unravel_index(np.nanargmax(np.where(np.isfinite(t), t, -np.inf)), t.shape)[0])
        found["heat"] = {"peak": iso(times[h]), "stations": int(ind["heat_n"][h]),
                         "station": at_station("t", hh, np.argmax)}

    # In the order they set in: a heat wave ended by a cold front reads "heat, cold".
    types = sorted(found, key=lambda k: (int(np.argmax(fl[k][sl])), TYPES.index(k)))
    start, end = times[a], times[b]
    days = []
    d = start.astimezone(LOCAL).date()
    while d <= end.astimezone(LOCAL).date():
        days.append(d.isoformat())
        d += timedelta(days=1)
    return {
        "id": f"{days[0]}-{types[0]}",
        "types": types,
        "start": iso(start),
        "end": iso(end),
        "hours": b - a + 1,
        "days": days,
        "peak": found[types[0]]["peak"],
        **found,
    }


def detect(data, times, stations, gust_thr=None):
    ind = indicators(data, gust_thr)
    fl = flags(ind)
    any_flag = np.any(np.vstack(list(fl.values())), axis=0)
    events = [describe(a, b, ind, fl, times, stations) for a, b in clusters(any_flag)
              if any_flag[a:b + 1].sum() >= MIN_HOURS]
    for e in events:
        e["ongoing"] = parse_iso(e["end"]) >= times[-1] - timedelta(hours=ONGOING_HOURS)
    return events[::-1]


def merge(recent, past, cutoff):
    """Events found in the recent days, and before `cutoff` those of the whole archive."""
    old = [e for e in past if parse_iso(e["start"]) < cutoff]
    new = [e for e in recent if parse_iso(e["start"]) >= cutoff]
    return sorted(new + old, key=lambda e: e["start"], reverse=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", default=ROOT / "archive", type=Path, help="checkout of the data branch")
    parser.add_argument("--hourly", default=ROOT / "data/processed/hourly.json", type=Path)
    parser.add_argument("--out", default=ROOT / "web/src/data/detected.json", type=Path)
    parser.add_argument("--history", type=Path,
                        help="search the whole archive (no rolling window) and write the events here")
    parser.add_argument("--days", type=int, default=120,
                        help="archived days searched when the archive has a history of events")
    args = parser.parse_args()

    archive = args.archive if (args.archive / "stations.json").exists() else None
    window = None if args.history else (json.loads(args.hourly.read_text()) if args.hourly.exists() else None)
    if archive:
        stations = json.loads((archive / "stations.json").read_text())
    elif window:
        stations = window["stations"]
    else:
        raise SystemExit("neither archive nor rolling window")
    codes = [s["code"] for s in stations]

    if args.history:
        data, times = load(archive, None, codes)
        thr = gust_thresholds(data["WG.BOE"])
        events = detect(data, times, stations, thr)
        for e in events:
            e.pop("ongoing")
        args.history.write_text(json.dumps({
            "from": iso(times[0]), "to": iso(times[-1]),
            "gust_thresholds": {c: round(float(v), 1) for c, v in zip(codes, thr)},
            "events": events,
        }, ensure_ascii=False, indent=1) + "\n")
        print(f"{len(events)} events in the archive, {times[0]:%Y-%m-%d} to {times[-1]:%Y-%m-%d}")
        return

    # The whole archive is searched once a day (freeze.yml); here only its last days.
    past = archive / "events.json" if archive else None
    days = None
    if past and past.exists():
        archived = sorted(arc.archived_days(archive))
        days = [d for d in archived if d > archived[-1] - timedelta(days=args.days)]
    history = json.loads(past.read_text()) if days is not None else None
    data, times = load(archive, window, codes, days)
    thr = None
    if history and "gust_thresholds" in history:
        thr = np.array([history["gust_thresholds"].get(c, GUST) for c in codes])
    events = detect(data, times, stations, thr)
    first = times[0]
    if history:
        # Events that began in the first two days searched may have begun earlier.
        events = merge(events, history["events"], times[0] + timedelta(days=2))
        first = parse_iso(history["from"])

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({
        "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "from": iso(first),
        "to": iso(times[-1]),
        "rules": {"cold": COLD_DROP, "rain": RAIN_AREA, "gust": GUST, "gust_pct": GUST_PCT,
                  "gust_floor": GUST_FLOOR, "gust_stations": GUST_STATIONS,
                  "snow": SNOW, "snow_stations": SNOW_STATIONS, "heat": HEAT, "heat_stations": HEAT_STATIONS},
        "events": events,
    }, ensure_ascii=False, indent=1))
    print(f"{len(events)} events between {first:%Y-%m-%d} and {times[-1]:%Y-%m-%d %H:%M}Z")


if __name__ == "__main__":
    main()
