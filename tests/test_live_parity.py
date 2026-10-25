"""The browser's live field (web/src/lib/livefield.ts) against fields.py."""

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

import fields
import render_maps as rm

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def run_ts(fixture, tmp_path):
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(fixture))
    out = subprocess.run(["node", str(ROOT / "tests/live_parity.ts"), str(path)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def terrain():
    return fields.Terrain.load(ROOT / "data/geo")


def stations_with_inversion(terrain, n=60, seed=3):
    """Stations on real terrain cells, cold in the valleys below 900 m."""
    rng = np.random.default_rng(seed)
    rows, cols = np.nonzero(terrain.inside & np.isfinite(terrain.dem))
    pick = rng.choice(len(rows), n, replace=False)
    fx, fy = terrain.centers
    out = []
    for i in pick:
        z = float(terrain.dem[rows[i], cols[i]])
        t = 2 + 0.004 * min(z, 900) - 0.0065 * max(z - 900, 0) + rng.normal(0, 0.6)
        out.append({"x": float(fx[cols[i]]), "y": float(fy[rows[i]]), "alt": round(z), "v": round(t, 1)})
    return out


def test_field_matches_python(terrain, tmp_path):
    points = stations_with_inversion(terrain)
    field, info = fields.temperature(points, [p["v"] for p in points], terrain)
    rng = np.random.default_rng(7)
    rows, cols = np.nonzero(terrain.inside)
    pick = rng.choice(len(rows), 400, replace=False)
    fx, fy = terrain.centers
    cells = [[float(fx[cols[i]]), float(fy[rows[i]]), float(terrain.dem[rows[i], cols[i]])] for i in pick]
    fixture = {"extent": list(terrain.extent), "points": points, "cells": cells,
               "crop": {"w": 1, "h": 1, "rgba": [0, 1, 0, 255], "range": [0, 1]}}
    js = run_ts(fixture, tmp_path)
    assert js["hinge"] == info["hinge_m"]
    expected = field[rows[pick], cols[pick]]
    assert np.max(np.abs(np.array(js["values"]) - expected)) < 0.01


def test_precipitation_matches_python(terrain, tmp_path):
    points = stations_with_inversion(terrain)
    rng = np.random.default_rng(5)
    for p in points:  # a shower over the west, dry elsewhere
        p["v"] = round(max(0.0, 6 - (p["x"] - terrain.extent[0]) / 15000 + rng.normal(0, 0.5)), 1)
    field, _ = fields.precipitation(points, [p["v"] for p in points], terrain, method="krige")
    rows, cols = np.nonzero(terrain.inside)
    pick = rng.choice(len(rows), 400, replace=False)
    fx, fy = terrain.centers
    cells = [[float(fx[cols[i]]), float(fy[rows[i]]), 0.0] for i in pick]
    fixture = {"kind": "precipitation", "extent": list(terrain.extent), "points": points, "cells": cells,
               "crop": {"w": 1, "h": 1, "rgba": [0, 1, 0, 255], "range": [0, 1]}}
    js = run_ts(fixture, tmp_path)
    assert np.max(np.abs(np.array(js["values"]) - field[rows[pick], cols[pick]])) < 0.01


def test_shade_matches_python(terrain, tmp_path):
    r = rm.Renderer(terrain, tmp_path)
    dem = np.nan_to_num(terrain.dem, nan=np.nanmin(terrain.dem))
    r0, c0, size = 600, 900, 40  # around the Sarntal Alps
    crop = np.clip(np.round(dem[r0:r0 + size, c0:c0 + size]), 1, 65535).astype(int)
    rgba = np.dstack([crop >> 8, crop & 255, np.full_like(crop, 255), np.full_like(crop, 255)])
    fixture = {"extent": list(terrain.extent), "points": [{"x": 0, "y": 0, "alt": 0, "v": 0}], "cells": [],
               "crop": {"w": size, "h": size, "rgba": rgba.ravel().tolist(), "range": rm.shade_range(dem, 100)}}
    js = np.array(run_ts(fixture, tmp_path)["shade"]).reshape(size, size)
    inner = (slice(1, -1), slice(1, -1))  # the crop's own edge is one-sided
    assert np.max(np.abs(js[inner] - r.shade[r0:r0 + size, c0:c0 + size][inner])) < 0.02
