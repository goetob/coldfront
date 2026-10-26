"""Apply the hourly checks of meteo/qc.py to the archive.

Days archived before a check existed carry values it would have dropped:
gusts (qc.gust_outliers) such as 82.8 m/s at Prags Rossalm in a mean wind
of 1.5 m/s during Vaia, and snow depths (qc.snow_outliers) such as 123 cm for
one hour at Seiser Alm Zallinger in the same rain, or 218 cm there in July, and
temperatures (qc.temp_outliers) such as 20 to 37 °C at Prettau Lengspitze, 3105 m,
in the summer of 2022. The checks only need the hourly values, so the archive is
corrected without asking the API again: the rows that fail are removed, and
gust_max, hs_max, hs_end, and tmin, tmax, tmean where temperatures went, are
recomputed from what remains. Files without such values stay as they are.

The day files (the current month) are checked together, so the temperature
checks see more than one day; --day-files checks only them, for the daily run.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from meteo import archive, qc  # noqa: E402
from meteo.timeutil import local_day_bounds  # noqa: E402

DAILY_COMPLETE = 0.9  # as in freeze_day.py


def grid(hourly, var, codes, rows, col):
    """array[hour, station] of one variable of an hourly table."""
    a = np.full((rows.max() + 1, len(codes)), np.nan)
    m = (hourly["var"].to_numpy(zero_copy_only=False) == var)
    a[rows[m], col[m]] = hourly["value"].to_numpy()[m]
    return a, m


def outlier_rows(hourly, alt=None):
    """Row mask of the gusts, snow depths and temperatures in an hourly table that fail the checks.

    alt: {station: elevation}; without it temperatures are not checked.
    """
    codes = sorted(set(hourly["station"].to_pylist()))
    secs = pc.cast(hourly["time"].cast(pa.timestamp("s", tz="UTC")), "int64").to_numpy()
    hours, rows = np.unique(secs, return_inverse=True)
    col = pc.index_in(hourly["station"], value_set=pa.array(codes)).to_numpy()
    gust, is_gust = grid(hourly, "WG.BOE", codes, rows, col)
    mean, _ = grid(hourly, "WG", codes, rows, col)
    snow, is_snow = grid(hourly, "HS", codes, rows, col)
    temp, _ = grid(hourly, "LT", codes, rows, col)
    bad_gust = qc.gust_outliers(gust, mean)
    bad_temp = np.zeros_like(bad_gust)
    if alt and temp.size:
        # The rule on lasting departures needs consecutive hours.
        full = np.arange(hours[0], hours[-1] + 1, 3600)
        t = np.full((len(full), len(codes)), np.nan)
        t[(hours - hours[0]) // 3600] = temp
        bad_temp = qc.temp_outliers(t, [alt.get(c, np.nan) for c in codes])[(hours - hours[0]) // 3600]
        temp = np.where(bad_temp, np.nan, temp)
    bad_snow = np.zeros_like(bad_gust)
    for j in range(len(codes)):
        if np.isfinite(snow[:, j]).any():
            bad_snow[:, j] = qc.snow_outliers(snow[:, j], temp[:, j])
    is_temp = hourly["var"].to_numpy(zero_copy_only=False) == "LT"
    return (is_gust & bad_gust[rows, col]) | (is_snow & bad_snow[rows, col]) | (is_temp & bad_temp[rows, col])


def daily_values(hourly):
    """{(station, local day): {gust_max, hs_max, hs_end, tmin, tmax, tmean}} from an hourly table.

    The temperatures as in freeze_day.py: from at least DAILY_COMPLETE of the day's hours.
    """
    out = {}
    temps = {}
    for var in ("WG.BOE", "HS", "LT"):
        part = hourly.filter(pc.equal(hourly["var"], var)).sort_by([("time", "ascending")])
        days = archive.day_of_rows(part, "hourly").to_pylist()
        for code, day, v in zip(part["station"].to_pylist(), days, part["value"].to_pylist()):
            rec = out.setdefault((code, day), {})
            if var == "LT":
                temps.setdefault((code, day), []).append(v)
            elif var == "WG.BOE":
                rec["gust_max"] = max(v, rec.get("gust_max", -np.inf))
            else:
                rec["hs_max"] = max(v, rec.get("hs_max", -np.inf))
                rec["hs_end"] = v  # the rows are in time order
    for (code, day), vals in temps.items():
        start, end = local_day_bounds(day)
        if len(vals) >= DAILY_COMPLETE * (end - start).total_seconds() / 3600:
            out[(code, day)].update(tmin=min(vals), tmax=max(vals), tmean=sum(vals) / len(vals))
    return out


def recheck(hourly_path, daily_path, alt=None):
    """Correct one pair of hourly and daily files; the number of values removed."""
    return recheck_group([(hourly_path, daily_path)], alt)


def recheck_group(pairs, alt=None):
    """Correct pairs of hourly and daily files, checked as one table; the number of values removed."""
    tables = [pq.read_table(h).cast(archive.HOURLY_SCHEMA) for h, _ in pairs]
    hourly = pa.concat_tables(tables)
    part = np.repeat(np.arange(len(pairs)), [t.num_rows for t in tables])
    keep = np.ones(hourly.num_rows, bool)
    # With some values gone, others may stand out: until nothing more is found.
    while (bad := outlier_rows(hourly.filter(pa.array(keep)), alt)).any():
        keep[np.flatnonzero(keep)[bad]] = False
    for k, (hourly_path, daily_path) in enumerate(pairs):
        mine = part == k
        if keep[mine].all():
            continue
        kept = hourly.filter(pa.array(keep & mine))
        dropped = hourly.filter(pa.array(~keep & mine))
        hot = dropped.filter(pc.equal(dropped["var"], "LT"))
        t_days = set(zip(hot["station"].to_pylist(), archive.day_of_rows(hot, "hourly").to_pylist()))
        values = daily_values(kept)
        rows = pq.read_table(daily_path).cast(archive.DAILY_SCHEMA).to_pylist()
        for r in rows:
            rec = values.get((r["station"], r["date"]), {})
            # Rounded as in freeze_day.py: gusts and temperatures to 0.1, snow to whole cm.
            r["gust_max"] = None if "gust_max" not in rec else round(float(rec["gust_max"]), 1)
            for f in ("hs_max", "hs_end"):
                r[f] = None if f not in rec else round(float(rec[f]), 0)
            if (r["station"], r["date"]) in t_days:
                for f in ("tmin", "tmax", "tmean"):
                    r[f] = None if f not in rec else round(float(rec[f]), 1)
        archive.write_table(kept, hourly_path)
        archive.write_table(pa.Table.from_pylist(rows, schema=archive.DAILY_SCHEMA), daily_path)
    return int((~keep).sum())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", default="archive", type=Path, help="checkout of the data branch")
    parser.add_argument("--day-files", action="store_true", help="only the day files (the daily run)")
    args = parser.parse_args()
    root = args.archive
    stations = json.loads((root / "stations.json").read_text()) if (root / "stations.json").exists() else []
    alt = {s["code"]: s["alt"] for s in stations}
    groups = [] if args.day_files else [
        [(p, archive.month_path(root, "daily", m))] for m, p in sorted(archive.month_files(root, "hourly").items())]
    days = [(p, archive.day_path(root, "daily", d)) for d, p in sorted(archive.day_files(root, "hourly").items())]
    if days:
        groups.append(days)
    total = 0
    for group in groups:
        group = [(h, d) for h, d in group if d.exists()]
        if not group:
            continue
        n = recheck_group(group, alt)
        if n:
            print(f"{group[0][0].relative_to(root)}{' …' if len(group) > 1 else ''}: {n} values removed")
        total += n
    print(f"{total} values removed in {sum(len(g) for g in groups)} files")


if __name__ == "__main__":
    main()
