"""Apply the hourly checks of meteo/qc.py to the archive.

Days archived before a check existed carry values it would have dropped:
gusts (qc.gust_outliers) such as 82.8 m/s at Prags Rossalm in a mean wind
of 1.5 m/s during Vaia, and snow depths (qc.snow_outliers) such as 123 cm for
one hour at Seiser Alm Zallinger in the same rain, or 218 cm there in July. The checks only need
the hourly values, so the archive is corrected without asking the API again:
the rows that fail are removed, and gust_max, hs_max and hs_end of their days
are recomputed from what remains. Files without such values stay as they are.
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from meteo import archive, qc  # noqa: E402


def grid(hourly, var, codes, rows, col):
    """array[hour, station] of one variable of an hourly table."""
    a = np.full((rows.max() + 1, len(codes)), np.nan)
    m = (hourly["var"].to_numpy(zero_copy_only=False) == var)
    a[rows[m], col[m]] = hourly["value"].to_numpy()[m]
    return a, m


def outlier_rows(hourly):
    """Row mask of the gusts and snow depths in an hourly table that fail the checks."""
    codes = sorted(set(hourly["station"].to_pylist()))
    secs = pc.cast(hourly["time"].cast(pa.timestamp("s", tz="UTC")), "int64").to_numpy()
    hours, rows = np.unique(secs, return_inverse=True)
    col = pc.index_in(hourly["station"], value_set=pa.array(codes)).to_numpy()
    gust, is_gust = grid(hourly, "WG.BOE", codes, rows, col)
    mean, _ = grid(hourly, "WG", codes, rows, col)
    snow, is_snow = grid(hourly, "HS", codes, rows, col)
    temp, _ = grid(hourly, "LT", codes, rows, col)
    bad_gust = qc.gust_outliers(gust, mean)
    bad_snow = np.zeros_like(bad_gust)
    for j in range(len(codes)):
        if np.isfinite(snow[:, j]).any():
            bad_snow[:, j] = qc.snow_outliers(snow[:, j], temp[:, j])
    return (is_gust & bad_gust[rows, col]) | (is_snow & bad_snow[rows, col])


def daily_values(hourly):
    """{(station, local day): {gust_max, hs_max, hs_end}} from an hourly table."""
    out = {}
    for var in ("WG.BOE", "HS"):
        part = hourly.filter(pc.equal(hourly["var"], var)).sort_by([("time", "ascending")])
        days = archive.day_of_rows(part, "hourly").to_pylist()
        for code, day, v in zip(part["station"].to_pylist(), days, part["value"].to_pylist()):
            rec = out.setdefault((code, day), {})
            if var == "WG.BOE":
                rec["gust_max"] = max(v, rec.get("gust_max", -np.inf))
            else:
                rec["hs_max"] = max(v, rec.get("hs_max", -np.inf))
                rec["hs_end"] = v  # the rows are in time order
    return out


def recheck(hourly_path, daily_path):
    """Correct one pair of hourly and daily files; the number of values removed."""
    hourly = pq.read_table(hourly_path).cast(archive.HOURLY_SCHEMA)
    kept = hourly
    # With some values gone, others may stand out: until nothing more is found.
    while (bad := outlier_rows(kept)).any():
        kept = kept.filter(pa.array(~bad))
    if kept.num_rows == hourly.num_rows:
        return 0
    values = daily_values(kept)
    rows = pq.read_table(daily_path).cast(archive.DAILY_SCHEMA).to_pylist()
    for r in rows:
        rec = values.get((r["station"], r["date"]), {})
        # Rounded as in freeze_day.py: gusts to 0.1 m/s, snow to whole cm.
        r["gust_max"] = None if "gust_max" not in rec else round(float(rec["gust_max"]), 1)
        for k in ("hs_max", "hs_end"):
            r[k] = None if k not in rec else round(float(rec[k]), 0)
    archive.write_table(kept, hourly_path)
    archive.write_table(pa.Table.from_pylist(rows, schema=archive.DAILY_SCHEMA), daily_path)
    return hourly.num_rows - kept.num_rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", default="archive", type=Path, help="checkout of the data branch")
    args = parser.parse_args()
    root = args.archive
    pairs = [(p, archive.month_path(root, "daily", m)) for m, p in archive.month_files(root, "hourly").items()]
    pairs += [(p, archive.day_path(root, "daily", d)) for d, p in archive.day_files(root, "hourly").items()]
    total = 0
    for hourly_path, daily_path in sorted(pairs):
        if not daily_path.exists():
            continue
        n = recheck(hourly_path, daily_path)
        if n:
            print(f"{hourly_path.relative_to(root)}: {n} values removed")
        total += n
    print(f"{total} values removed in {len(pairs)} files")


if __name__ == "__main__":
    main()
