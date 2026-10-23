"""Time handling shared by the pipeline.

Everything internal is aware UTC. The API speaks local time (Europe/Rome):
its dates carry a CET/CEST suffix, and its query bounds are read as local.
Local time is only for display; file names and keys use UTC so the hour
that occurs twice when daylight saving ends stays two distinct hours.
"""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

LOCAL = ZoneInfo("Europe/Rome")
OFFSETS = {"CET": 1, "CEST": 2}
HOUR = timedelta(hours=1)


def parse_api_date(s):
    """'2026-10-18T21:50:00CEST' -> aware UTC datetime."""
    local, zone = s[:19], s[19:]
    tz = timezone(timedelta(hours=OFFSETS[zone]))
    return datetime.fromisoformat(local).replace(tzinfo=tz).astimezone(timezone.utc)


def api_bound(t):
    """Query bound for the API, which reads YYYYMMDDHHMM as local time."""
    return t.astimezone(LOCAL).strftime("%Y%m%d%H%M")


def hour_ending(t):
    """The full hour an observation belongs to: (h-1, h]."""
    floor = t.replace(minute=0, second=0, microsecond=0)
    return floor if t == floor else floor + HOUR


def floor_hour(t):
    return t.replace(minute=0, second=0, microsecond=0)


def iso(t):
    """Hour key used in the JSON data files: '2026-10-25T01:00Z'."""
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)


def stamp(t):
    """File name of an hour's maps: '20261025T01Z'."""
    return t.astimezone(timezone.utc).strftime("%Y%m%dT%HZ")


def parse_local_hour(s):
    """'2026-10-20T12' as local wall time -> aware UTC.

    An hour that occurs twice resolves to its first occurrence, one that is
    skipped in spring to the hour after the gap.
    """
    naive = datetime.fromisoformat(s)
    return naive.replace(tzinfo=LOCAL).astimezone(timezone.utc)


def local_day_bounds(day: date):
    """UTC start and end of a local calendar day (23, 24 or 25 hours long)."""
    start = datetime.combine(day, datetime.min.time(), tzinfo=LOCAL)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=LOCAL)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)
