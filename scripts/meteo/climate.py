"""Normal values, anomalies and rankings for the pages.

Normals come from build_normals.py (normals/ on the data branch), the daily
values of all archived years from meteo/archive.py. Everything is held as
arrays over (day, station) so ranking a day against every year is cheap.

Network values average over the stations that have normals. The absolute
network temperature is the stations' mean normal plus their mean anomaly,
so a year with fewer stations does not read as warmer or colder.
"""

import json
import warnings
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq

from . import archive

FIELDS = ("tmean", "tmin", "tmax", "n_sum")
NORMAL_FIELDS = ("tmean", "tmin", "tmax", "tmean_p10", "tmean_p90", "tmin_p10", "tmax_p90", "n_freq", "n_mean")
MIN_NETWORK = 20     # stations a network value needs
MIN_YEARS_RANK = 5   # years a calendar day needs before it is ranked
SINCE_GAP = 45       # days: "the wettest day since ..." only when that is longer ago
# Temperature runs through the year: in autumn nearly every cold snap is the coldest
# day since spring. "The warmest/coldest day since ..." is only said beyond a year.
SINCE_GAP_T = 365
WET_NETWORK = 3.0    # mm in the network mean for "the wettest day since ..."
WET_STATION = 10.0   # mm at a station


def slot(d):
    """Day of the year in a leap year's calendar, 0–365 (as in build_normals.py)."""
    return date(2000, d.month, d.day).timetuple().tm_yday - 1


def _nanmean(a, axis, least=1):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        out = np.nanmean(a, axis=axis)
    out[np.isfinite(a).sum(axis=axis) < least] = np.nan
    return out


@dataclass
class Normals:
    codes: list            # station order of the arrays
    values: dict           # field -> array[366, station]
    meta: dict             # normals/stations.json

    @classmethod
    def load(cls, path, codes):
        """None when the archive has no normals yet."""
        p = Path(path)
        if not (p / "normals.parquet").exists():
            return None
        t = pq.read_table(p / "normals.parquet").to_pydict()
        col = {c: i for i, c in enumerate(codes)}
        values = {f: np.full((366, len(codes)), np.nan) for f in NORMAL_FIELDS}
        for k, (code, doy) in enumerate(zip(t["station"], t["doy"])):
            if code in col:
                for f in NORMAL_FIELDS:
                    v = t[f][k]
                    if v is not None:
                        values[f][doy - 1, col[code]] = v
        return cls(list(codes), values, json.loads((p / "stations.json").read_text()))

    def at(self, field, d):
        return self.values[field][slot(d)]

    @property
    def period(self):
        return self.meta["period"]

    def station(self, code):
        return self.meta["stations"].get(code, {"years_t": 0, "years_n": 0, "quality": "none"})

    @property
    def usable(self):
        """Stations with normals, as a mask over the codes."""
        return np.array([self.station(c)["quality"] != "none" for c in self.codes])


@dataclass
class History:
    first: date
    codes: list
    values: dict           # field -> array[day, station], one row per calendar day from `first`

    @classmethod
    def load(cls, root, codes):
        t = archive.read(root, "daily", columns=["station", *FIELDS])
        if not t.num_rows:
            return None
        dates = t["date"].to_pylist()
        first, last = min(dates), max(dates)
        n = (last - first).days + 1
        col = {c: i for i, c in enumerate(codes)}
        rows = np.array([(d - first).days for d in dates])
        cols = np.array([col.get(c, -1) for c in t["station"].to_pylist()])
        keep = cols >= 0
        values = {}
        for f in FIELDS:
            a = np.full((n, len(codes)), np.nan)
            v = t[f].to_numpy(zero_copy_only=False).astype(float)
            a[rows[keep], cols[keep]] = v[keep]
            values[f] = a
        return cls(first, list(codes), values)

    @property
    def dates(self):
        return [self.first + timedelta(days=i) for i in range(len(self.values["tmean"]))]

    def index(self, d):
        i = (d - self.first).days
        return i if 0 <= i < len(self.values["tmean"]) else None


def anomalies(history, normals, field="tmean"):
    """array[day, station]: the daily value minus its normal."""
    slots = np.array([slot(d) for d in history.dates])
    base = field if field in ("tmean", "tmin", "tmax") else "tmean"
    return history.values[field] - normals.values[base][slots]


def network(history, normals):
    """Network series per day: anomalies, absolute temperatures and mean precipitation."""
    use = normals.usable
    slots = np.array([slot(d) for d in history.dates])
    out = {}
    for f in ("tmean", "tmin", "tmax"):
        anom = anomalies(history, normals, f)[:, use]
        mean_anom = _nanmean(anom, 1, MIN_NETWORK)
        out[f"{f}_anom"] = mean_anom
        out[f] = _nanmean(normals.values[f][:, use], 1)[slots] + mean_anom
    out["n_sum"] = _nanmean(history.values["n_sum"][:, use], 1, MIN_NETWORK)
    return out


_CALENDAR = {}


def _calendar(dates):
    """Month*100+day of every date, computed once per date axis."""
    key = (dates[0], len(dates))
    if key not in _CALENDAR:
        _CALENDAR[key] = np.array([d.month * 100 + d.day for d in dates])
    return _CALENDAR[key]


def same_date(series, dates, i):
    """Rank of day i among the same calendar day of every year: warmest first and coldest first."""
    if not np.isfinite(series[i]):
        return None
    cal = _calendar(dates)
    idx = np.flatnonzero(cal == cal[i])
    vals = series[idx]
    ok = np.isfinite(vals)
    if ok.sum() < MIN_YEARS_RANK:
        return None
    vals, v = vals[ok], series[i]
    return {"warm": 1 + int((vals > v).sum()), "cold": 1 + int((vals < v).sum()),
            "years": int(ok.sum()), "first": dates[int(idx[ok][0])].year}


def since(series, dates, i, higher=True, gap=SINCE_GAP):
    """The last earlier day at least as high (or low) as day i, if longer than `gap` days ago.

    {"since": "2019-06-12"}, or {"record": True, "first": "2015-01-01"} when no earlier
    day reached it, or None when one did within `gap` days.
    """
    v = series[i]
    if not np.isfinite(v):
        return None
    past = series[:i]
    with np.errstate(invalid="ignore"):
        hit = np.flatnonzero(past >= v if higher else past <= v)
    if not hit.size:
        # A record only says something with a few years behind it.
        first = next((k for k in range(i) if np.isfinite(series[k])), None)
        if first is None or (dates[i] - dates[first]).days < 365 * MIN_YEARS_RANK:
            return None
        return {"record": True, "first": dates[first].isoformat()}
    j = int(hit[-1])
    if (dates[i] - dates[j]).days <= gap:
        return None
    return {"since": dates[j].isoformat()}


def notable(series_t, series_n, dates, i, wet):
    """What stands out about day i: ranks of its calendar day and "since" records."""
    tmean, tmin, tmax = series_t
    out = {}
    if r := same_date(tmean, dates, i):
        out["same_date"] = r
    if (s := since(tmax, dates, i, gap=SINCE_GAP_T)) is not None:
        out["warm_since"] = s
    if (s := since(tmin, dates, i, higher=False, gap=SINCE_GAP_T)) is not None:
        out["cold_since"] = s
    if np.isfinite(series_n[i]) and series_n[i] >= wet and (s := since(series_n, dates, i)) is not None:
        out["wet_since"] = s
    return out


# Climatological day counts (DWD, MeteoSwiss) from the daily values of the local
# day: tropical night (lowest value at least 20 °C), hot day (highest at least
# 30 °C), frost day (lowest below 0 °C), ice day (highest below 0 °C).
# A tropical night counts on the day its lowest value falls in.
KENNTAGE = {"tn": ("tmin", 20.0, True), "hd": ("tmax", 30.0, True),
            "fd": ("tmin", 0.0, False), "ed": ("tmax", 0.0, False)}
YEAR_SHARE = 0.8     # share of a span's days with values for its count to stand


def kenntag(values, kind):
    """(hit, valid) masks of one kind over an array of daily values."""
    _, limit, at_least = KENNTAGE[kind]
    valid = np.isfinite(values)
    with np.errstate(invalid="ignore"):
        hit = (values >= limit) if at_least else (values < limit)
    return hit & valid, valid


def kenntage_years(history, last):
    """Counts per year and station, up to `last`: {"years": [...], kind: array[year, station]}.

    The year of `last` counts up to it, and so does "to_date": every year up to
    the same calendar day, for comparing the running year. A count needs
    YEAR_SHARE of its days with values, else it is NaN.
    """
    dates = history.dates
    n = (last - history.first).days + 1
    year = np.array([d.year for d in dates[:n]])
    early = np.array([(d.month, d.day) <= (last.month, last.day) for d in dates[:n]])
    years = list(range(dates[0].year, last.year + 1))
    out = {"years": years, "to_date": {}}
    for kind, (field, _, _) in KENNTAGE.items():
        hit, valid = kenntag(history.values[field][:n], kind)
        full = np.full((len(years), hit.shape[1]), np.nan)
        part = np.full_like(full, np.nan)
        for k, y in enumerate(years):
            if history.first > date(y, 1, 1):
                continue  # a year the archive starts in is not counted whole
            for target, sel in ((full, year == y), (part, (year == y) & early)):
                # The axis of history has every calendar day: sel counts the span's days.
                ok = valid[sel].sum(axis=0) >= YEAR_SHARE * sel.sum()
                target[k] = np.where(ok, hit[sel].sum(axis=0), np.nan)
        out[kind] = full
        out["to_date"][kind] = part
    return out


def kenntage_mean(counts, years, period):
    """Mean count per station over the years of `period` that have one (at least MIN_YEARS_RANK)."""
    sel = [k for k, y in enumerate(years) if period[0] <= y <= period[1]]
    a = counts[sel]
    have = np.isfinite(a).sum(axis=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        m = np.nanmean(a, axis=0)
    return np.where(have >= MIN_YEARS_RANK, m, np.nan)
