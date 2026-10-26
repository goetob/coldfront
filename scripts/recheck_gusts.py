"""Apply the hourly gust check (meteo/qc.gust_outliers) to the archive.

Days archived before the check carry gusts that it would have dropped, such
as 82.8 m/s at Prags Rossalm in a mean wind of 1.5 m/s during Vaia. The check
only needs the hourly values, so the archive is corrected without asking the
API again: the gust rows that fail are removed and gust_max of their days is
recomputed from the gusts that remain. Files without such gusts stay as
they are.
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


def outlier_rows(hourly):
    """Row mask of the gusts in an hourly table that fail the check."""
    codes = sorted(set(hourly["station"].to_pylist()))
    secs = pc.cast(hourly["time"].cast(pa.timestamp("s", tz="UTC")), "int64").to_numpy()
    hours, row = np.unique(secs, return_inverse=True)
    col = pc.index_in(hourly["station"], value_set=pa.array(codes)).to_numpy()
    var = hourly["var"].to_numpy(zero_copy_only=False)
    value = hourly["value"].to_numpy()
    g = np.full((len(hours), len(codes)), np.nan)
    m = np.full_like(g, np.nan)
    is_gust, is_mean = var == "WG.BOE", var == "WG"
    g[row[is_gust], col[is_gust]] = value[is_gust]
    m[row[is_mean], col[is_mean]] = value[is_mean]
    bad = qc.gust_outliers(g, m)
    return is_gust & bad[row, col]


def daily_gusts(hourly):
    """{(station, local day): highest gust} of an hourly table."""
    gust = hourly.filter(pc.equal(hourly["var"], "WG.BOE"))
    days = archive.day_of_rows(gust, "hourly").to_pylist()
    out = {}
    for code, day, v in zip(gust["station"].to_pylist(), days, gust["value"].to_pylist()):
        out[(code, day)] = max(v, out.get((code, day), -np.inf))
    return out


def recheck(hourly_path, daily_path):
    """Correct one pair of hourly and daily files; the number of gusts removed."""
    hourly = pq.read_table(hourly_path).cast(archive.HOURLY_SCHEMA)
    bad = outlier_rows(hourly)
    if not bad.any():
        return 0
    kept = hourly.filter(pa.array(~bad))
    gusts = daily_gusts(kept)
    daily = pq.read_table(daily_path).cast(archive.DAILY_SCHEMA)
    rows = daily.to_pylist()
    for r in rows:
        g = gusts.get((r["station"], r["date"]))
        r["gust_max"] = None if g is None else round(float(g), 1)
    archive.write_table(kept, hourly_path)
    archive.write_table(pa.Table.from_pylist(rows, schema=archive.DAILY_SCHEMA), daily_path)
    return int(bad.sum())


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
            print(f"{hourly_path.relative_to(root)}: {n} gusts removed")
        total += n
    print(f"{total} gusts removed in {len(pairs)} files")


if __name__ == "__main__":
    main()
