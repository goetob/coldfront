"""Plausibility checks on the raw 10-minute values.

During a single event bad values get noticed on the maps; in continuous
operation nobody looks, so obvious faults are caught here. The checks are
conservative: a flagged value is left out of the hourly aggregate and
counted, never corrected.

  range   outside physical limits for the sensor (snow depth sensors read
          slightly below zero on bare ground; that is handled in hourly.py)
  spike   a single value that jumps away and straight back (LT, LD.RED)
  stuck   the same value for many hours where real air never holds still
          (LT; LF other than saturation; a non-zero wind speed)

Gusts are checked again on the hourly values (gust_outliers), against the
station's own mean wind and against the network: the archive back to 2015
holds 68 hours with gusts of 60 to 95 m/s, all faults, such as 95 m/s in a
mean wind of 0.4 m/s or a sensor reporting 80 to 95 m/s for weeks while no
other station passed 26. The strongest real gusts in it reach 53 m/s.
Snow depths are checked on the hourly values too (snow_outliers), and on
bare ground they count as 0 cm (snow_bare).

Temperatures are checked again on the hourly values (temp_outliers), against
the elevation trend of the hour: Prettau Lengspitze at 3105 m read 20 to
37 °C for weeks in the summer of 2022, Signalgipfel at 3399 m -40 °C in June
2020, Sarnthein drifted 10 K above its neighbours in the autumn of 2015.
"""

import warnings

import numpy as np

from .trend import hinge_fit

RANGE = {
    "LT": (-45, 45), "LF": (0, 105), "LD.RED": (900, 1080),
    "WG": (0, 75), "WG.BOE": (0, 75), "WR": (0, 360),
    "N": (0, 50), "HS": (-30, 600), "SD": (0, 600), "GS": (0, 1500),
}
SPIKE = {"LT": 8.0, "LD.RED": 8.0}
# A gust is out of place above GUST_FACTOR times its hour's mean wind plus
# GUST_OFFSET (in a storm, 50 m/s gusts come with 20 to 30 m/s of mean wind),
# or, from GUST_LONELY on, at more than twice the third strongest gust of the
# hour in the network.
# Snow depth: a stretch of at most SNOW_RUN hours that lies SNOW_SPIKE cm or more
# above (or below) the hours before and after it is a fault: new snow stays and
# settles slowly, it does not come and go within hours. Seiser Alm Zallinger
# read 0, 0, 0, 123, 0, 0, 0 cm in the rain of Vaia; sensors also drop to 0 for an
# hour. A lasting change of more than SNOW_STEP cm within an hour is faster than
# any snowfall, but which side of it is right cannot be told (a sensor coming
# back from a drop-out reads its true depth again); it is kept, and the event
# detection does not count the 24 hours around it. Unless it is warm: snow
# cannot build up by SNOW_STEP cm within an hour at SNOW_WARM °C or more, so
# such a rise is a fault until the readings come back to the level before
# (Seiser Alm Zallinger: 218 cm in July at 12 °C; Graun Schöneben: 715 cm).
SNOW_SPIKE, SNOW_RUN, SNOW_STEP, SNOW_WARM = 20.0, 3, 30.0, 3.0
# Bare ground (snow_bare): some snow depth sensors on alpine meadows read the
# grass in summer, 10 to 60 cm, rising while it is warm (Schnals Finail: 37 cm
# in the median of July and August). Once the snow cover has melted, a station
# is bare: through BARE_HOURS every reading at most BARE_DEPTH cm, or at most
# BARE_DEPTH_WARM cm with the hours at BARE_WARM °C or more on average (some
# sensors keep 6 to 10 cm on bare ground, a stone or a slipped zero). While
# bare, a reading counts as 0 cm. Snow counts again when SNOW_NEW_HOURS readings
# in a row lie SNOW_NEW cm or more above the highest reading of the BASE_HOURS
# before, the first of them at SNOW_COLD °C or less: grass does not grow by 5 cm
# within hours, and not in the cold. Of the cold readings before them, those
# that already lie above that highest one count as well: the snowfall began
# there. The BASE_HOURS end BASE_LAG hours back, so that snow falling at 1 cm
# an hour still stands out, and at the last hour above SNOW_COLD, as grass
# only grows when it is warm. In the archive back to 2015 winter snow falls at
# SNOW_COLD °C or less in 99.5 % of the hours it builds up.
BARE_HOURS, BARE_DEPTH, BARE_DEPTH_WARM, BARE_WARM = 48, 5.0, 10.0, 3.0
SNOW_NEW, SNOW_NEW_HOURS, SNOW_COLD, BASE_HOURS, BASE_LAG = 5.0, 3, 2.0, 24, 6
# Snow that falls on tall grass presses it down and may never rise above it:
# at Proveis Samerberg in September 2024 the readings fell from 20 to 40 cm of
# grass to a smooth 14 cm of snow. Snow also counts again when SETTLED_HOURS
# readings, all at SNOW_COLD °C or less and at least BARE_DEPTH cm, change by
# SETTLED cm an hour or less in the median, after the readings of the
# BASE_HOURS before jumped by GRASS cm or more: a snow surface lies still
# (0.2 to 0.4 cm an hour in winter), grass does not (1 to 4 cm). Snow that
# fell on bare ground has melted again when the readings of BASE_HOURS jump
# by GRASS cm in the median while it is GRASS_WARM °C on average, and do not
# sink below those of the BASE_HOURS before, as melting snow would.
SETTLED_HOURS, SETTLED, GRASS, GRASS_WARM = 6, 0.5, 2.0, 7.0
GUST_FACTOR, GUST_OFFSET = 3.0, 25.0
GUST_LONELY, GUST_LONELY_RATIO, GUST_NETWORK = 35.0, 2.0, 10
STUCK_HOURS = {"LT": 12, "LF": 12, "WG": 6}
# Hourly temperature against the elevation trend of the hour (trend.hinge_fit),
# in K. Real departures stay within these: valley cold pools reach 12 K below
# the trend for 12 hours (Pfitsch St. Jakob, January 2017: -18 °C, Vals -5 °C),
# a foehn breaking through warms a valley by 12 K within the hour (Schlanders).
#   one hour        more than TEMP_HOUR off the trend
#   warm for long   more than TEMP_WARM[k][0] above it for TEMP_WARM[k][1] hours;
#                   such a stretch is dropped as long as it stays TEMP_WARM_EDGE
#                   above, so a sensor drifting off goes with its start
#   cold for long   more than TEMP_COLD below it for TEMP_COLD_HOURS hours
#   frozen          TEMP_FLAT_HOURS hours within TEMP_FLAT K (the raw check only
#                   finds identical values; Graun Elferspitze read -13.3 and -13.4
#                   for three weeks), except near 0 °C, where melting snow holds it
TEMP_HOUR = 15.0
TEMP_WARM, TEMP_WARM_EDGE = ((8.0, 24), (6.0, 72)), 5.0
TEMP_COLD, TEMP_COLD_HOURS = 12.0, 24
TEMP_FLAT, TEMP_FLAT_HOURS, TEMP_FLAT_ZERO = 0.2, 24, 1.0
TEMP_NETWORK = 20   # stations an hour needs for a trend


def check(sensor, rows):
    """Split [(t, v), ...] sorted by time into (good rows, mask of flagged rows)."""
    if not rows:
        return [], np.zeros(0, bool)
    t = np.array([r[0].timestamp() for r in rows])
    v = np.array([r[1] for r in rows], dtype=float)
    bad = np.zeros(len(v), bool)

    lo, hi = RANGE.get(sensor, (-np.inf, np.inf))
    bad |= (v < lo) | (v > hi)

    if sensor in SPIKE and len(v) >= 3:
        jump = SPIKE[sensor]
        up = v[1:-1] - v[:-2]
        down = v[1:-1] - v[2:]
        spike = (np.abs(up) > jump) & (np.abs(down) > jump) & (np.sign(up) == np.sign(down))
        bad[1:-1] |= spike

    if sensor in STUCK_HOURS:
        bad |= stuck(t, v, STUCK_HOURS[sensor] * 3600, sensor)

    good = [r for r, b in zip(rows, bad) if not b]
    return good, bad


def stuck(t, v, span, sensor):
    """Runs of identical values lasting at least `span` seconds."""
    out = np.zeros(len(v), bool)
    start = 0
    for i in range(1, len(v) + 1):
        if i == len(v) or v[i] != v[start]:
            value = v[start]
            exempt = (sensor == "LF" and value >= 99) or (sensor == "WG" and value == 0)
            if not exempt and t[i - 1] - t[start] >= span:
                out[start:i] = True
            start = i
    return out


def gust_outliers(gust, mean):
    """Mask of implausible hourly gusts; arrays [hour, station], NaN where missing."""
    with np.errstate(invalid="ignore"):
        windless = np.isfinite(mean) & (gust > GUST_FACTOR * mean + GUST_OFFSET)
        filled = np.where(np.isfinite(gust), gust, -np.inf)
        # The third strongest gust of the hour: two faulty sensors at once
        # must not vouch for each other.
        third = np.sort(filled, axis=1)[:, -3:-2] if gust.shape[1] >= 3 else np.full((len(gust), 1), np.inf)
        enough = np.isfinite(gust).sum(axis=1, keepdims=True) >= GUST_NETWORK
        lonely = enough & (gust >= GUST_LONELY) & (gust > GUST_LONELY_RATIO * third)
        return windless | lonely | (gust > RANGE["WG.BOE"][1])


def snow_spikes(values):
    """Mask of the hours in a station's hourly snow depths (NaN where missing) that are spikes.

    Repeated until nothing more is found: with one spike gone, the one next to
    it may stand out.
    """
    v = np.array(values, dtype=float)
    bad = np.zeros(len(v), bool)
    while True:
        new = _spikes_once(np.where(bad, np.nan, v))
        if not new.any():
            return bad
        bad |= new


def _spikes_once(v):
    bad = np.zeros(len(v), bool)
    ok = np.flatnonzero(np.isfinite(v))
    x = v[ok]
    for n in range(1, SNOW_RUN + 1):
        if len(x) < n + 2:
            break
        # Every run of n consecutive readings with one before and one after it.
        runs = np.lib.stride_tricks.sliding_window_view(x[1:-1], n)
        before, after = x[:len(runs)], x[n + 1:n + 1 + len(runs)]
        high = runs.min(axis=1) > np.maximum(before, after) + SNOW_SPIKE
        low = runs.max(axis=1) < np.minimum(before, after) - SNOW_SPIKE
        for k in np.flatnonzero(high | low):
            bad[ok[k + 1:k + 1 + n]] = True
    return bad


def snow_outliers(values, temps):
    """Mask of faulty hourly snow depths of one station: spikes, and rises no snowfall makes.

    `temps` is the station's air temperature in the same hours (NaN where missing).
    """
    v = np.array(values, dtype=float)
    t = np.array(temps, dtype=float)
    with np.errstate(invalid="ignore"):
        bad = v > RANGE["HS"][1]  # under 4 m at 3000 m is the most in the archive; 6.6 m at 2040 m was a fault
    bad |= snow_spikes(np.where(bad, np.nan, v))
    ok = np.flatnonzero(np.isfinite(v) & ~bad)
    k = 1
    while k < len(ok):
        level = v[ok[k - 1]]
        if v[ok[k]] - level > SNOW_STEP and t[ok[k]] >= SNOW_WARM:
            end = k
            while end < len(ok) and v[ok[end]] > level + SNOW_SPIKE:
                end += 1
            bad[ok[k:end]] = True
            k = end
        else:
            k += 1
    return bad


def snow_bare(values, temps, hours, bare=False):
    """Mask of the hourly snow depths of one station that count as 0 cm: bare ground.

    `values` and `temps` are the station's snow depths and air temperatures
    (NaN where missing) at `hours`, whole hours in ascending order; `bare` is
    the state before the first of them. See BARE_HOURS for the rules.
    """
    v = np.array(values, dtype=float)
    lt = np.array(temps, dtype=float)
    h = np.array(hours, dtype=np.int64)
    out = np.zeros(len(v), bool)
    ok = np.flatnonzero(np.isfinite(v))
    th, x = h[ok], v[ok]
    since = th[0] if bare and len(ok) else np.iinfo(np.int64).min // 2  # hour of the last change
    fell = False  # snow that fell on bare ground
    last_warm = None
    for k, i in enumerate(ok):
        t = th[k]
        if bare:
            end = t - BASE_LAG if last_warm is None else min(t - BASE_LAG, last_warm)
            a, b = np.searchsorted(th, [end - BASE_HOURS, end], side="right")
            # After a gap the last reading before it; at the start nothing.
            base = x[a:b].max() if b > a else (x[b - 1] if b else np.nan)
            n = k + SNOW_NEW_HOURS
            # Cold in the first hour, so that the snow never starts with a warm step
            # (snow_outliers); without its temperature, in the hours around it.
            cold = lt[i] if np.isfinite(lt[i]) else np.nanmin(lt[max(i - 1, 0):ok[min(n, len(ok)) - 1] + 1],
                                                              initial=np.inf)
            new = (n <= len(ok) and th[n - 1] - t <= SNOW_NEW_HOURS
                   and (x[k:n] >= base + SNOW_NEW).all() and cold <= SNOW_COLD)
            if new:
                # The snowfall began where the readings left the bare level in the cold.
                j = k
                while j > 0 and out[ok[j - 1]] and x[j - 1] > base and lt[ok[j - 1]] <= SNOW_COLD:
                    j -= 1
                out[ok[j:k]] = False
                bare, since, fell = False, th[j], True
            elif _settled(x, th, lt, ok, k):
                j = k - SETTLED_HOURS + 1
                out[ok[j:k]] = False
                bare, since, fell = False, th[j], True
            else:
                out[i] = True
        if not lt[i] <= SNOW_COLD:
            last_warm = t
        if bare or t - since < BARE_HOURS:
            continue
        a = np.searchsorted(th, t - BARE_HOURS, side="right")
        if k + 1 - a < BARE_HOURS // 2:
            continue
        deepest = x[a:k + 1].max()
        span = lt[ok[a]:i + 1]
        warm = np.isfinite(span).any() and np.nanmean(span) >= BARE_WARM
        if deepest <= BARE_DEPTH or (deepest <= BARE_DEPTH_WARM and warm) or (fell and _grass(x, th, lt, ok, k)):
            bare, since, fell = True, t, False
    return out


def _grass(x, th, lt, ok, k):
    """Whether the readings of the BASE_HOURS up to the k-th jump like grass in the warmth (GRASS)."""
    a = np.searchsorted(th, th[k] - BASE_HOURS, side="right")
    if k + 1 - a < BASE_HOURS * 3 // 4:
        return False
    span = lt[ok[a]:ok[k] + 1]
    # Snow melts in the warmth; grass keeps its height or grows.
    b = np.searchsorted(th, th[k] - 2 * BASE_HOURS, side="right")
    return (np.median(np.abs(np.diff(x[a:k + 1]))) >= GRASS
            and np.isfinite(span).any() and np.nanmean(span) >= GRASS_WARM
            and a - b >= BASE_HOURS // 2 and np.median(x[a:k + 1]) >= np.median(x[b:a]))


def _settled(x, th, lt, ok, k):
    """Whether the readings up to the k-th lie still like snow after jumping like grass (SETTLED_HOURS)."""
    j = k - SETTLED_HOURS + 1
    if j < 1 or th[k] - th[j] > SETTLED_HOURS:
        return False
    w = x[j:k + 1]
    if w.min() < BARE_DEPTH or np.median(np.abs(np.diff(w))) > SETTLED:
        return False
    if not (lt[ok[j]:ok[k] + 1] <= SNOW_COLD).all():
        return False
    before = x[np.searchsorted(th, th[j] - BASE_HOURS, side="left"):j + 1]
    # Readings of exactly 0 cm before are bare ground already counted as such
    # (an archive checked before), and snow on it as well.
    return len(before) >= BASE_HOURS * 3 // 4 and (np.median(np.abs(np.diff(before))) >= GRASS
                                                   or not before[:-1].any())


def _runs(mask):
    """(start, end) of the runs of True in a 1-D mask."""
    edges = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))


def _long_runs(mask, hours):
    out = np.zeros_like(mask)
    for s, e in _runs(mask):
        if e - s >= hours:
            out[s:e] = True
    return out


def trend_residuals(temps, alt, skip=None):
    """array[hour, station]: temperature minus the elevation trend of its hour (NaN without one).

    Stations in `skip` (a mask like temps) are left out of the fits.
    """
    t = np.asarray(temps, dtype=float)
    z = np.asarray(alt, dtype=float)
    res = np.full_like(t, np.nan)
    use = np.isfinite(t) & np.isfinite(z)[None, :]
    if skip is not None:
        use &= ~skip
    for k in np.flatnonzero(use.sum(axis=1) >= TEMP_NETWORK):
        ok = use[k]
        trend, _ = hinge_fit(z[ok], t[k, ok])
        have = np.isfinite(t[k]) & np.isfinite(z)
        res[k, have] = t[k, have] - trend(z[have])
    return res


def _temp_once(t, res):
    bad = np.zeros(t.shape, bool)
    with np.errstate(invalid="ignore"):
        bad |= np.abs(res) > TEMP_HOUR
        for j in range(t.shape[1]):
            r = res[:, j]
            warm = np.zeros(len(r), bool)
            for limit, hours in TEMP_WARM:
                warm |= _long_runs(r > limit, hours)
            for s, e in _runs(r > TEMP_WARM_EDGE):
                if warm[s:e].any():
                    bad[s:e, j] = True
            bad[:, j] |= _long_runs(r < -TEMP_COLD, TEMP_COLD_HOURS)
    return bad


def _flat(t):
    """Hours in a stretch of TEMP_FLAT_HOURS that stays within TEMP_FLAT K, away from 0 °C."""
    n = TEMP_FLAT_HOURS
    bad = np.zeros(t.shape, bool)
    if len(t) < n:
        return bad
    win = np.lib.stride_tricks.sliding_window_view(t, n, axis=0)
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        span = np.nanmax(win, axis=-1) - np.nanmin(win, axis=-1)
    flat = (span <= TEMP_FLAT) & (np.isfinite(win).sum(axis=-1) >= n - 2)
    for i in range(n):
        bad[i:i + len(flat)] |= flat
    with np.errstate(invalid="ignore"):
        return bad & (np.abs(t) > TEMP_FLAT_ZERO)


def temp_outliers(temps, alt):
    """Mask of implausible hourly temperatures; temps array[hour, station] (NaN where missing),
    alt the stations' elevations (NaN where unknown: not checked against the trend).

    The hours must be consecutive. Repeated until nothing more is found: a
    faulty station pulls the trend towards it.
    """
    t = np.asarray(temps, dtype=float)
    bad = _flat(t)
    while True:
        new = _temp_once(t, trend_residuals(t, alt, skip=bad)) & ~bad
        if not new.any():
            return bad
        bad |= new
