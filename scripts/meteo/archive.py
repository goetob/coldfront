"""Layout of the archive on the data branch, for writing and reading.

  hourly/YYYY/MM/YYYY-MM-DD.parquet   one local day: station, time (UTC, hour ending), var, value, flagged
  daily/YYYY/YYYY-MM-DD.parquet       one local day, one row per station
  hourly/YYYY-MM.parquet              a whole local month, same columns
  daily/YYYY-MM.parquet
  stations.json

Days are written as day files; a month that is over is folded into one
month file, and the backfill writes month files right away. A month file
holds the month's days that have no day file of their own, so where both
exist the day file wins. Reading always goes through this module.
"""

import calendar
import os
import re
from datetime import date

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

HOURLY_SCHEMA = pa.schema([
    ("station", pa.string()), ("time", pa.timestamp("s", tz="UTC")),
    ("var", pa.string()), ("value", pa.float64()), ("flagged", pa.int16()),
])
DAILY_FIELDS = ("tmin", "tmax", "tmean", "n_sum", "gust_max", "wind_mean", "lf_mean", "lf_min",
                "p_mean", "sun_h", "gs_mean", "hs_max", "hs_end")
DAILY_SCHEMA = pa.schema([("station", pa.string()), ("date", pa.date32()),
                          *((f, pa.float64()) for f in DAILY_FIELDS)])
MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def day_path(root, kind, d):
    return root / (f"hourly/{d:%Y/%m}/{d}.parquet" if kind == "hourly" else f"daily/{d:%Y}/{d}.parquet")


def month_path(root, kind, month):
    return root / f"{kind}/{month[0]:04d}-{month[1]:02d}.parquet"


def month_days(month):
    y, m = month
    return [date(y, m, k) for k in range(1, calendar.monthrange(y, m)[1] + 1)]


def months(first, last):
    """(year, month) from the month of `first` to that of `last`."""
    y, m = first.year, first.month
    out = []
    while (y, m) <= (last.year, last.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def day_files(root, kind="hourly"):
    pattern = "hourly/*/*/*.parquet" if kind == "hourly" else "daily/*/*.parquet"
    return {date.fromisoformat(p.stem): p for p in root.glob(pattern)}


def month_files(root, kind="hourly"):
    out = {}
    for p in root.glob(f"{kind}/*.parquet"):
        if m := MONTH_RE.match(p.stem):
            out[(int(m[1]), int(m[2]))] = p
    return out


def archived_days(root, kind="hourly"):
    """Every archived local day: those with a day file and all days of months with a month file."""
    days = set(day_files(root, kind))
    for month in month_files(root, kind):
        days.update(month_days(month))
    return days


def day_of_rows(table, kind):
    """Local day of each row: the date column, or the local day an hour ends in."""
    if kind == "daily":
        return table["date"]
    # An hour ending at local midnight belongs to the day before.
    before = pc.subtract(table["time"], pa.scalar(1, pa.duration("s")))
    return pc.local_timestamp(before.cast(pa.timestamp("s", tz="Europe/Rome"))).cast(pa.date32())


def read(root, kind, days=None, columns=None, variables=None):
    """One table of the archived rows of `days` (all days if None); day files win over month files.

    `variables` limits hourly rows to these values of var.
    """
    days = set(days) if days is not None else None
    filters = [("var", "in", sorted(variables))] if variables else None
    schema = HOURLY_SCHEMA if kind == "hourly" else DAILY_SCHEMA
    # The day of a row is needed to pick it from a month file.
    cols = columns and set(columns) | {"time" if kind == "hourly" else "date"}
    dfiles = day_files(root, kind)
    tables = []
    for month, path in sorted(month_files(root, kind).items()):
        wanted = set(month_days(month)) if days is None else days & set(month_days(month))
        if not wanted:
            continue
        t = pq.read_table(path, columns=sorted(cols) if cols else None, filters=filters)
        skip = wanted & set(dfiles)
        if skip or wanted != set(month_days(month)):
            keep = wanted - skip
            t = t.filter(pc.is_in(day_of_rows(t, kind), value_set=pa.array(sorted(keep), pa.date32())))
        tables.append(t)
    for d, path in sorted(dfiles.items()):
        if days is None or d in days:
            tables.append(pq.read_table(path, columns=sorted(cols) if cols else None, filters=filters))
    if cols:
        schema = pa.schema([schema.field(c) for c in schema.names if c in cols])
    if not tables:
        return schema.empty_table()
    return pa.concat_tables([t.select(schema.names).cast(schema) for t in tables])


def read_daily(root, days=None):
    """{date: {station: row}} for the archived days."""
    out = {}
    for row in read(root, "daily", days).to_pylist():
        out.setdefault(row["date"], {})[row["station"]] = row
    return out


def write_table(table, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, path)


def write_day(root, d, hourly, daily):
    write_table(hourly.cast(HOURLY_SCHEMA), day_path(root, "hourly", d))
    write_table(daily.cast(DAILY_SCHEMA), day_path(root, "daily", d))


def write_month(root, month, hourly=None, daily=None, fold_days=False):
    """Add the given rows to the month file; with fold_days also the month's day files, which are then removed.

    The month's day files win over rows already in the month file, the new
    rows over both.
    """
    days = month_days(month)
    parts = {"hourly": [], "daily": []}
    replaced = set()
    new = {"hourly": hourly, "daily": daily}
    for kind in ("hourly", "daily"):
        if new[kind] is not None and new[kind].num_rows:
            replaced |= set(day_of_rows(new[kind], kind).to_pylist())
    folded = []
    if fold_days:
        dfiles = {d: p for d, p in day_files(root, "hourly").items() if d in set(days)}
        for d in sorted(set(dfiles) - replaced):
            for kind in ("hourly", "daily"):
                p = day_path(root, kind, d)
                if p.exists():
                    parts[kind].append(pq.read_table(p))
                    folded.append(p)
            replaced.add(d)
    for kind in ("hourly", "daily"):
        old = month_path(root, kind, month)
        if old.exists():
            t = pq.read_table(old)
            if replaced:
                t = t.filter(pc.invert(pc.is_in(day_of_rows(t, kind), value_set=pa.array(sorted(replaced), pa.date32()))))
            parts[kind].insert(0, t)
        if new[kind] is not None:
            parts[kind].append(new[kind])
    for kind, schema, order in (("hourly", HOURLY_SCHEMA, ["station", "var", "time"]),
                                ("daily", DAILY_SCHEMA, ["date", "station"])):
        tables = [t.select(schema.names).cast(schema) for t in parts[kind] if t.num_rows]
        if tables:
            t = pa.concat_tables(tables).sort_by([(c, "ascending") for c in order])
            write_table(t, month_path(root, kind, month))
    for p in folded:
        p.unlink()
        if not any(p.parent.iterdir()):
            p.parent.rmdir()


def missing_days(root, month, last=None):
    """Days of `month` up to `last` that are not archived."""
    have = archived_days(root)
    return [d for d in month_days(month) if d not in have and (last is None or d <= last)]

