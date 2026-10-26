"""Elevation trend of the hourly temperature, shared by the maps and the checks."""

import numpy as np


def hinge_fit(z, t):
    """Fit t = a + b*z + c*max(z - h, 0); fall back to a straight line.

    The hinge is searched between 600 and 2000 m and only kept if it explains
    the data clearly better, so normal well-mixed hours stay linear.
    """
    def lstsq(design):
        coef, *_ = np.linalg.lstsq(design, t, rcond=None)
        return coef, float(np.sum((design @ coef - t) ** 2))

    line, line_sse = lstsq(np.column_stack([np.ones_like(z), z]))
    best = (lambda zz: line[0] + line[1] * zz, line_sse, None)
    for h in range(600, 2001, 100):
        design = np.column_stack([np.ones_like(z), z, np.maximum(z - h, 0)])
        if (z > h).sum() < 8 or (z <= h).sum() < 8:
            continue
        coef, sse = lstsq(design)
        if sse < 0.8 * best[1] and sse < 0.85 * line_sse:
            best = (lambda zz, c=coef, h=h: c[0] + c[1] * zz + c[2] * np.maximum(zz - h, 0), sse, h)
    return best[0], best[2]
