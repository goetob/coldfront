"""Normal values per station and calendar day, from the archived daily values.

For every station and day of the year the days within ±15 days of it in all
complete years of the archive (the reference period, now 2015–2025) give

  tmean, tmin, tmax           mean of the daily mean, lowest and highest temperature
  *_p10, *_p90                their 10th and 90th percentile
  n_freq                      share of days with at least 1 mm
  n_mean                      mean daily precipitation (mm, dry days included)
  n_wet_p90                   90th percentile of the wet days' amounts

A calendar day needs MIN_YEARS years' worth of days in its window, else it
stays empty; stations with fewer than SHORT_YEARS years are marked as short.
The current year never counts, so the file only changes when a year is
complete or the backfill adds data. Writes, on the data branch,

  normals/normals.parquet     station, doy (1–366, a leap year's calendar), month, day, the values above
  normals/stations.json       reference period and per station years of data and quality
"""

import argparse
import json
import warnings
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from meteo import archive
from meteo.timeutil import LOCAL

HALF_WINDOW = 15
WET = 1.0           # mm, a wet day
MIN_YEARS = 5       # years of data a normal needs
SHORT_YEARS = 8     # fewer years: marked as uncertain
YEAR_SHARE = 0.8    # share of a year's days for it to count as a year of data
T_FIELDS = ("tmean", "tmin", "tmax")


def slot(d):
    """Day of the year in a leap year's calendar, 0–365: 29 February keeps its own slot."""
    return date(2000, d.month, d.day).timetuple().tm_yday - 1


SLOT_DATES = [date.fromordinal(date(2000, 1, 1).toordinal() + i) for i in range(366)]
# Window of every slot; 29 February is missing in three years of four, so the
# window counts calendar slots, which is within a day of counting days.
WINDOW = (np.arange(366)[:, None] + np.arange(-HALF_WINDOW, HALF_WINDOW + 1)[None, :]) % 366


def grids(table, years):
    """{station: {field: array[year, slot]}} of the reference years."""
    y0 = years[0]
    out = {}
    cols = {k: table[k].to_numpy(zero_copy_only=False) for k in ("tmean", "tmin", "tmax", "n_sum")}
    dates = table["date"].to_pylist()
    year_idx = np.array([d.year - y0 for d in dates])
    slots = np.array([slot(d) for d in dates])
    stations = np.array(table["station"].to_pylist())
    keep = (year_idx >= 0) & (year_idx < len(years))
    for code in np.unique(stations[keep]):
        sel = keep & (stations == code)
        g = {}
        for k, v in cols.items():
            a = np.full((len(years), 366), np.nan)
            a[year_idx[sel], slots[sel]] = np.asarray(v[sel], dtype=float)
            g[k] = a
        out[str(code)] = g
    return out


def years_of_data(a):
    """Years in which at least YEAR_SHARE of the days have a value."""
    return int((np.isfinite(a).sum(axis=1) >= YEAR_SHARE * 365).sum())


def window_stats(a):
    """Windowed samples per slot: array[slot, sample]."""
    return a[:, WINDOW].transpose(1, 0, 2).reshape(366, -1)


def station_normals(g):
    """Columns of one station's normals, NaN where a slot has too little data."""
    need = MIN_YEARS * (2 * HALF_WINDOW + 1) * YEAR_SHARE
    out = {}
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # empty windows
        for k in T_FIELDS:
            w = window_stats(g[k])
            n = np.isfinite(w).sum(axis=1)
            ok = n >= need
            out[k] = np.where(ok, np.nanmean(w, axis=1), np.nan)
            p10, p90 = np.nanpercentile(w, [10, 90], axis=1)
            out[f"{k}_p10"] = np.where(ok, p10, np.nan)
            out[f"{k}_p90"] = np.where(ok, p90, np.nan)
            if k == "tmean":
                out["t_n"] = n
        w = window_stats(g["n_sum"])
        n = np.isfinite(w).sum(axis=1)
        ok = n >= need
        out["n_n"] = n
        out["n_freq"] = np.where(ok, np.nansum(w >= WET, axis=1) / np.maximum(n, 1), np.nan)
        out["n_mean"] = np.where(ok, np.nanmean(w, axis=1), np.nan)
        wet = np.where(w >= WET, w, np.nan)
        enough_wet = np.isfinite(wet).sum(axis=1) >= 20
        out["n_wet_p90"] = np.where(ok & enough_wet, np.nanpercentile(wet, 90, axis=1), np.nan)
    return out


COLUMNS = ("tmean", "tmin", "tmax", "tmean_p10", "tmean_p90", "tmin_p10", "tmin_p90", "tmax_p10", "tmax_p90",
           "n_freq", "n_mean", "n_wet_p90")


def build(table, years):
    """(normals table, per-station metadata) from a daily table."""
    rows = {k: [] for k in ("station", "doy", "month", "day", *COLUMNS)}
    meta = {}
    for code, g in sorted(grids(table, years).items()):
        yt, yn = years_of_data(g["tmean"]), years_of_data(g["n_sum"])
        quality = "none" if max(yt, yn) < MIN_YEARS else "short" if max(yt, yn) < SHORT_YEARS else "ok"
        meta[code] = {"years_t": yt, "years_n": yn, "quality": quality}
        if quality == "none":
            continue
        nm = station_normals(g)
        has = np.isfinite(nm["tmean"]) | np.isfinite(nm["n_mean"])
        for i in np.flatnonzero(has):
            rows["station"].append(code)
            rows["doy"].append(int(i) + 1)
            rows["month"].append(SLOT_DATES[i].month)
            rows["day"].append(SLOT_DATES[i].day)
            for k in COLUMNS:
                v = nm[k][i]
                rows[k].append(None if not np.isfinite(v) else round(float(v), 3 if k == "n_freq" else 2))
    schema = pa.schema([("station", pa.string()), ("doy", pa.int16()), ("month", pa.int8()), ("day", pa.int8()),
                        *((k, pa.float32()) for k in COLUMNS)])
    return pa.Table.from_pydict(rows, schema=schema), meta


def reference_years(days, today):
    """Complete years of the archive: from the first archived year to the last before today's."""
    if not days:
        return []
    first = min(days).year
    return list(range(first, today.year))


def load(path):
    """Normals as {station: {doy: row}} and the metadata, or (None, None) without a file."""
    p = Path(path)
    if not (p / "normals.parquet").exists():
        return None, None
    out = {}
    for r in pq.read_table(p / "normals.parquet").to_pylist():
        out.setdefault(r["station"], {})[r["doy"]] = r
    return out, json.loads((p / "stations.json").read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", default="archive", type=Path, help="checkout of the data branch")
    args = parser.parse_args()

    today = datetime.now(LOCAL).date()
    days = archive.archived_days(args.archive, "daily")
    years = reference_years(days, today)
    table = archive.read(args.archive, "daily", [d for d in days if d.year in set(years)],
                         columns=["station", "date", "tmean", "tmin", "tmax", "n_sum"])
    if not years or not table.num_rows:
        print("no complete year archived, no normals")
        return
    normals, meta = build(table, years)
    out = args.archive / "normals"
    out.mkdir(exist_ok=True)
    pq.write_table(normals, out / "normals.parquet", compression="zstd")
    (out / "stations.json").write_text(json.dumps({
        "period": [years[0], years[-1]],
        "window_days": HALF_WINDOW,
        "min_years": MIN_YEARS,
        "short_years": SHORT_YEARS,
        "stations": meta,
    }, indent=1, sort_keys=True) + "\n")
    q = [m["quality"] for m in meta.values()]
    print(f"normals {years[0]}–{years[-1]}: {q.count('ok')} stations ok, {q.count('short')} short, "
          f"{q.count('none')} too short; {normals.num_rows} rows")


if __name__ == "__main__":
    main()
