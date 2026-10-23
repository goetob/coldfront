from datetime import date, datetime, timezone

from meteo.timeutil import (api_bound, hour_ending, iso, local_day_bounds, parse_api_date,
                            parse_iso, parse_local_hour, stamp)


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


def test_parse_api_date_both_offsets():
    assert parse_api_date("2026-10-18T21:50:00CEST") == utc(2026, 10, 18, 19, 50)
    assert parse_api_date("2026-12-01T12:00:00CET") == utc(2026, 12, 1, 11, 0)


def test_repeated_hour_at_end_of_dst_stays_two_hours():
    # 25 October 2026: local 02:00-03:00 happens twice, first CEST then CET.
    first = parse_api_date("2026-10-25T02:30:00CEST")
    second = parse_api_date("2026-10-25T02:30:00CET")
    assert second - first == utc(2026, 1, 1, 1) - utc(2026, 1, 1, 0)
    assert stamp(hour_ending(first)) != stamp(hour_ending(second))


def test_hour_ending():
    assert hour_ending(utc(2026, 10, 23, 13, 0)) == utc(2026, 10, 23, 13, 0)
    assert hour_ending(utc(2026, 10, 23, 13, 10)) == utc(2026, 10, 23, 14, 0)


def test_stamp_and_iso_roundtrip():
    t = utc(2026, 10, 25, 1)
    assert stamp(t) == "20261025T01Z"
    assert parse_iso(iso(t)) == t


def test_api_bound_is_local_time():
    assert api_bound(utc(2026, 10, 23, 14, 5)) == "202610231605"
    assert api_bound(utc(2026, 12, 23, 14, 5)) == "202612231505"


def test_parse_local_hour():
    assert parse_local_hour("2026-10-20T12") == utc(2026, 10, 20, 10)


def test_local_day_lengths():
    for day, hours in [(date(2026, 3, 29), 23), (date(2026, 10, 25), 25), (date(2026, 10, 22), 24)]:
        start, end = local_day_bounds(day)
        assert (end - start).total_seconds() == hours * 3600
