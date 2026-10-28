"""Elevation trend of the hourly temperature, shared by the maps and the checks."""

import numpy as np

# The summit hinge: searched between these heights, with at least TOP_NEED
# stations above it. Only a dozen stations stand above 2800 m, so in a fit over
# all of them the valleys set the slope and a warm layer around 2000 m (warm
# air aloft, an inversion's top) carries on up to the summits, 2-3 K too warm.
TOP_LO, TOP_HI, TOP_NEED = 2000, 2800, 8
TOP_GAIN = 0.8        # kept only if it cuts the summits' squared error by 20 %
TOP_STEEP = -0.01     # nor if the line above it falls faster than dry air, 1 K per 100 m


def _lstsq(cols, t):
    design = np.column_stack(cols)
    coef, *_ = np.linalg.lstsq(design, t, rcond=None)
    return coef, design @ coef


def _line(coef, hinges):
    def f(zz):
        zz = np.asarray(zz, dtype=float)
        v = coef[0] + coef[1] * zz
        for c, h in zip(coef[2:], hinges):
            v = v + c * np.maximum(zz - h, 0)
        return v
    return f


def hinge_fit(z, t):
    """Fit t = a + b*z + c*max(z - h, 0) [+ d*max(z - top, 0)]; fall back to a straight line.

    The hinge is searched between 600 and 2000 m and only kept if it explains
    the data clearly better, so normal well-mixed hours stay linear. The
    summit hinge `top` is judged on the stations above it alone, as they are
    too few to move the overall error (see TOP_LO).

    Returns (trend, hinge, top); hinge and top are None where not used.
    """
    one = np.ones_like(z)
    line, pred = _lstsq([one, z], t)
    line_sse = float(np.sum((pred - t) ** 2))
    coef, hinges, best_sse = line, [], line_sse
    for h in range(600, 2001, 100):
        if (z > h).sum() < 8 or (z <= h).sum() < 8:
            continue
        c, pred = _lstsq([one, z, np.maximum(z - h, 0)], t)
        sse = float(np.sum((pred - t) ** 2))
        if sse < 0.8 * best_sse and sse < 0.85 * line_sse:
            coef, hinges, best_sse = c, [h], sse
    base = _line(coef, hinges)

    cols = [one, z] + [np.maximum(z - h, 0) for h in hinges]
    best_top = None
    for top in range(TOP_LO, TOP_HI + 1, 100):
        above = z > top
        if above.sum() < TOP_NEED or (hinges and top < hinges[0] + 300):
            continue
        c, pred = _lstsq(cols + [np.maximum(z - top, 0)], t)
        sse = float(np.sum((pred[above] - t[above]) ** 2))
        if np.sum(c[1:]) < TOP_STEEP or sse > TOP_GAIN * float(np.sum((base(z[above]) - t[above]) ** 2)):
            continue
        if best_top is None or sse < best_top[0]:
            best_top = (sse, c, top)
    if best_top is None:
        return base, (hinges or [None])[0], None
    _, c, top = best_top
    return _line(c, hinges + [top]), (hinges or [None])[0], top
