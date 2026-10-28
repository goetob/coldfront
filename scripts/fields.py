"""Interpolate hourly station values onto the terrain grid.

Temperature  elevation trend + ordinary kriging of the residuals.
             The trend is a hinge regression on station altitude, so a valley
             cold pool or inversion below the hinge gets its own slope instead
             of being forced onto one lapse rate; a second hinge above
             2000 m lets the summits leave a warm layer below them.
Precipitation  ordinary kriging with a large nugget, which keeps single dry or
             wet stations from punching rings into the field (IDW is kept
             as an option).

Kriging and IDW run on a 1 km grid and are upsampled to the 100 m terrain
grid; only the temperature trend uses the full-resolution elevation.
"""

from dataclasses import dataclass
from functools import cached_property
import json
from pathlib import Path

import numpy as np
from pykrige.ok import OrdinaryKriging
from rasterio.features import geometry_mask
from rasterio.transform import Affine
from scipy.interpolate import RegularGridInterpolator
from shapely.geometry import shape

from meteo.trend import hinge_fit

COARSE = 1000  # metres


@dataclass
class Terrain:
    dem: np.ndarray        # 100 m elevation, row 0 = north
    transform: Affine
    outline: object        # shapely geometry, EPSG:25832

    @classmethod
    def load(cls, geo_dir="data/geo"):
        geo = Path(geo_dir)
        t = np.load(geo / "terrain.npz")
        gj = json.loads((geo / "suedtirol.geojson").read_text())
        return cls(t["dem100"], Affine(*t["transform100"]), shape(gj["features"][0]["geometry"]))

    @property
    def shape(self):
        return self.dem.shape

    @cached_property
    def extent(self):
        """(xmin, xmax, ymin, ymax) for imshow."""
        a = self.transform
        h, w = self.shape
        return a.c, a.c + a.a * w, a.f + a.e * h, a.f

    @cached_property
    def inside(self):
        return geometry_mask([self.outline], self.shape, self.transform, invert=True)

    @cached_property
    def centers(self):
        """1-D x (west->east) and y (north->south) cell centres of the fine grid."""
        a = self.transform
        h, w = self.shape
        return a.c + a.a * (np.arange(w) + 0.5), a.f + a.e * (np.arange(h) + 0.5)

    @cached_property
    def coarse(self):
        """1-D x and y (south->north) coordinates of the 1 km working grid."""
        xmin, xmax, ymin, ymax = self.extent
        return (np.arange(xmin + COARSE / 2, xmax, COARSE),
                np.arange(ymin + COARSE / 2, ymax, COARSE))

    def upsample(self, coarse_field):
        gx, gy = self.coarse
        interp = RegularGridInterpolator((gy, gx), coarse_field, bounds_error=False, fill_value=None)
        fx, fy = self.centers
        yy, xx = np.meshgrid(fy, fx, indexing="ij")
        return interp((yy, xx)).astype(np.float32)


def krige(x, y, values, terrain, range_m=30000, nugget_frac=0.1):
    """Ordinary kriging with a fixed exponential variogram.

    Fitting a variogram per hour from ~90 stations is noisy; a fixed range with
    the sill taken from the hour's own variance is steadier frame to frame.
    """
    var = float(np.var(values))
    if var < 1e-6:
        return np.full(terrain.shape, float(np.mean(values)), dtype=np.float32)
    ok = OrdinaryKriging(
        x, y, values, variogram_model="exponential",
        variogram_parameters={"sill": var, "range": range_m, "nugget": nugget_frac * var},
        coordinates_type="euclidean",
    )
    gx, gy = terrain.coarse
    z, _ = ok.execute("grid", gx, gy)
    return terrain.upsample(np.asarray(z))


def idw(x, y, values, terrain, power=2.0, neighbours=8):
    gx, gy = terrain.coarse
    xx, yy = np.meshgrid(gx, gy)
    d = np.hypot(xx[..., None] - x, yy[..., None] - y)
    k = min(neighbours, len(x))
    idx = np.argpartition(d, k - 1, axis=-1)[..., :k]
    dk = np.take_along_axis(d, idx, axis=-1)
    w = 1.0 / np.maximum(dk, 1.0) ** power
    z = (w * values[idx]).sum(-1) / w.sum(-1)
    return terrain.upsample(z)


def temperature(stations, values, terrain):
    ok = [i for i, v in enumerate(values) if v is not None]
    x = np.array([stations[i]["x"] for i in ok])
    y = np.array([stations[i]["y"] for i in ok])
    z = np.array([stations[i]["alt"] for i in ok], dtype=float)
    t = np.array([values[i] for i in ok], dtype=float)
    trend, hinge, top = hinge_fit(z, t)
    resid = t - trend(z)
    field = trend(np.nan_to_num(terrain.dem, nan=np.nanmean(terrain.dem))) + krige(x, y, resid, terrain)
    rmse = float(np.sqrt(np.mean(resid ** 2)))
    return field.astype(np.float32), {"hinge_m": hinge, "top_m": top, "trend_rmse": round(rmse, 2), "n": len(ok)}


def precipitation(stations, values, terrain, method="idw"):
    ok = [i for i, v in enumerate(values) if v is not None]
    x = np.array([stations[i]["x"] for i in ok])
    y = np.array([stations[i]["y"] for i in ok])
    p = np.array([values[i] for i in ok], dtype=float)
    if method == "krige":
        field = krige(x, y, p, terrain, range_m=25000, nugget_frac=0.25)
    else:
        field = idw(x, y, p, terrain)
    return np.clip(field, 0, None), {"n": len(ok)}


def plain(stations, values, terrain, range_m=40000, nugget_frac=0.2):
    """Ordinary kriging without elevation trend or clipping (pressure tendency)."""
    ok = [i for i, v in enumerate(values) if v is not None]
    x = np.array([stations[i]["x"] for i in ok])
    y = np.array([stations[i]["y"] for i in ok])
    v = np.array([values[i] for i in ok], dtype=float)
    return krige(x, y, v, terrain, range_m=range_m, nugget_frac=nugget_frac), {"n": len(ok)}
