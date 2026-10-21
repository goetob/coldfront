"""Build the static geodata the maps need: terrain grids and the province outline.

Inputs (downloaded separately, not committed):
  - Copernicus DEM GLO-30 tiles covering South Tyrol
    https://copernicus-dem-30m.s3.amazonaws.com/
  - ISTAT province boundaries via openpolis/geojson-italy
    https://raw.githubusercontent.com/openpolis/geojson-italy/master/geojson/limits_IT_provinces.geojson

Outputs (committed, small):
  data/geo/terrain.npz      elevation on a 100 m and a 250 m grid, EPSG:25832
  data/geo/suedtirol.geojson province outline, EPSG:25832
"""

import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.merge import merge
from rasterio.warp import Resampling, reproject
from rasterio.transform import from_origin
from pyproj import Transformer
import shapely
from shapely.geometry import mapping, shape

CRS = "EPSG:25832"
MARGIN = 4000  # metres of context around the province


def province_outline(path):
    features = json.loads(Path(path).read_text())["features"]
    bz = next(f for f in features if f["properties"].get("prov_acr") == "BZ")
    to_utm = Transformer.from_crs("EPSG:4326", CRS, always_xy=True)
    return shapely.transform(shape(bz["geometry"]),
                             lambda xy: np.column_stack(to_utm.transform(xy[:, 0], xy[:, 1])))


def resample(src_arr, src_transform, src_crs, bounds, res):
    xmin, ymin, xmax, ymax = bounds
    width = int(np.ceil((xmax - xmin) / res))
    height = int(np.ceil((ymax - ymin) / res))
    transform = from_origin(xmin, ymax, res, res)
    out = np.full((height, width), np.nan, dtype=np.float32)
    reproject(
        src_arr, out,
        src_transform=src_transform, src_crs=src_crs,
        dst_transform=transform, dst_crs=CRS,
        resampling=Resampling.average,
        src_nodata=np.nan, dst_nodata=np.nan,
    )
    return out, transform


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dem-dir", required=True)
    parser.add_argument("--provinces", required=True)
    parser.add_argument("--out", default="data/geo")
    args = parser.parse_args()

    outline = province_outline(args.provinces)
    xmin, ymin, xmax, ymax = outline.bounds
    # Snap to whole kilometres so both grids share an origin.
    bounds = (
        np.floor((xmin - MARGIN) / 1000) * 1000,
        np.floor((ymin - MARGIN) / 1000) * 1000,
        np.ceil((xmax + MARGIN) / 1000) * 1000,
        np.ceil((ymax + MARGIN) / 1000) * 1000,
    )

    tiles = [rasterio.open(p) for p in sorted(Path(args.dem_dir).glob("*.tif"))]
    mosaic, mosaic_transform = merge(tiles, nodata=np.nan, dtype="float32")
    src_crs = tiles[0].crs

    grids = {}
    for res in (100, 250):
        dem, transform = resample(mosaic[0], mosaic_transform, src_crs, bounds, res)
        grids[f"dem{res}"] = dem
        grids[f"transform{res}"] = np.array(transform)[:6]
        print(f"{res} m grid: {dem.shape}, {np.nanmin(dem):.0f}-{np.nanmax(dem):.0f} m")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out / "terrain.npz", bounds=np.array(bounds), **grids)
    (out / "suedtirol.geojson").write_text(json.dumps({
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": CRS}},
        "features": [{"type": "Feature", "properties": {"name": "Südtirol"},
                      "geometry": mapping(outline.simplify(50))}],
    }))


if __name__ == "__main__":
    main()
