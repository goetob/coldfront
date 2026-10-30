"""Fetch the satellite pictures for the cloud maps into data/raw/clouds.

Per hour of the window (UTC, stamped like the maps, the hour ending at the
stamp) the Cloud Type of MTG over South Tyrol that was scanned in the last
10 minutes of the hour:

  <stamp>.nc       Cloud Type, cut out by EUMETSAT's Data Tailor (about 70 kB
                   instead of 17 MB for the full disk); needs the Data Store
                   credentials EUMETSAT_CONSUMER_KEY and EUMETSAT_CONSUMER_SECRET
  <stamp>-vis.png  by day the visible channel (0.6 micron) from the EUMETView
                   WMS, already in the map's grid (EPSG:25832, 100 m), only
                   once the scan is an hour old (VIS_DELAY)

The licences differ (EUMETSAT Data Policy): Cloud Type is free without
conditions as soon as it is out, but Level 1 FCI data are free (CC BY 4.0)
only with a latency of an hour or more; younger they need a licence. So the
newest hour's map first shows its clouds flat, and the next runs, which draw
the newest hours again, give them the texture of the visible picture.

A run fetches at most --max hours, newest first, so a lost cache refills
over a few runs instead of holding up one. Without credentials it does
nothing; the maps then have no cloud layer. It never fails the build.
"""

import argparse
import logging
import os
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import rasterio

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fields  # noqa: E402
from meteo import clouds  # noqa: E402
from meteo.timeutil import floor_hour, stamp  # noqa: E402

COLLECTION = "EO:EUM:DAT:0680"  # Cloud Type - MTG - 0 degree
ROI = [47.25, 46.05, 10.2, 12.65]  # N, S, W, E: the map's extent with a margin
BANDS = ["cloud_type", "quality_illumination"]
WMS = "https://view.eumetsat.int/geoserver/mtg_fd/wms"
SCAN = timedelta(minutes=10)
VIS_DELAY = timedelta(hours=1)  # Level 1 FCI data are free (CC BY 4.0) from this latency on
PARALLEL = 3  # the Data Tailor runs at most 3 customisations per user


def visible(when, terrain, path):
    """The visible picture of the scan starting at `when`, in the map's grid."""
    xmin, xmax, ymin, ymax = terrain.extent
    h, w = terrain.shape
    query = urllib.parse.urlencode({
        "service": "WMS", "version": "1.3.0", "request": "GetMap", "layers": "mtg_fd:vis06_hrfi",
        "styles": "", "crs": "EPSG:25832", "bbox": f"{xmin},{ymin},{xmax},{ymax}",
        "width": w, "height": h, "format": "image/png", "time": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    with urllib.request.urlopen(f"{WMS}?{query}", timeout=60) as resp:
        if not resp.headers.get("Content-Type", "").startswith("image/png"):
            raise RuntimeError(f"WMS: {resp.read(300)!r}")
        path.write_bytes(resp.read())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/raw/clouds", type=Path)
    parser.add_argument("--hours", type=int, default=72, help="hours of the window to fill")
    parser.add_argument("--keep", type=int, default=120, help="hours of pictures to keep")
    parser.add_argument("--max", type=int, default=12, help="most hours to fetch in one run")
    parser.add_argument("--budget", type=int, default=300, help="seconds after which no new hour is started")
    args = parser.parse_args()

    key, secret = os.environ.get("EUMETSAT_CONSUMER_KEY"), os.environ.get("EUMETSAT_CONSUMER_SECRET")
    if not key or not secret:
        print("No EUMETSAT credentials, no cloud maps")
        return
    args.out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    newest = floor_hour(now)
    for f in args.out.iterdir():  # old pictures and leftovers of broken downloads
        t = datetime.strptime(f.name[:12], "%Y%m%dT%HZ").replace(tzinfo=timezone.utc) if f.name[:1] == "2" else None
        if f.suffix == ".part" or t is None or t < newest - timedelta(hours=args.keep):
            f.unlink()
    hours = [newest - timedelta(hours=i) for i in range(args.hours)]
    todo = [h for h in hours if not (args.out / f"{stamp(h)}.nc").exists()][: args.max]
    terrain = fields.Terrain.load(Path(__file__).resolve().parent.parent / "data/geo")
    if todo:
        fetch_cloud_type(todo, args.out, key, secret, args.budget)
    else:
        print("Cloud Type up to date")

    # By day also the visible picture, once the scan (ending at the hour) is VIS_DELAY old.
    for h in hours:
        ct, vis = args.out / f"{stamp(h)}.nc", args.out / f"{stamp(h)}-vis.png"
        if not ct.exists() or vis.exists() or now - h < VIS_DELAY:
            continue
        with rasterio.open(f'NETCDF:"{ct}":quality_illumination') as src:
            if not clouds.is_day(np.nan_to_num(src.read(1))):
                continue
        try:
            visible(h - SCAN, terrain, vis)
            print(stamp(h), "visible picture")
        except (OSError, RuntimeError) as exc:  # the clouds work without it
            print(stamp(h), "no visible picture:", exc)


def fetch_cloud_type(todo, out_dir, key, secret, budget):
    """Cloud Type for the hours in `todo`, cut out by the Data Tailor, a few at a time."""
    import eumdac
    from eumdac.tailor_models import Chain
    # Its retry warnings carry the request URL, access token included, into the public build log.
    logging.getLogger("eumdac").setLevel(logging.CRITICAL + 1)

    token = eumdac.AccessToken((key, secret))
    collection = eumdac.DataStore(token).get_collection(COLLECTION)
    tailor = eumdac.DataTailor(token)
    chain = Chain(product="FCIL2CT", format="netcdf4", filter={"bands": BANDS}, roi={"NSWE": ROI})
    t0 = time.time()

    def one(hour):
        if time.time() - t0 > budget:
            return "later"
        start = hour - SCAN
        target = out_dir / f"{stamp(hour)}.nc"
        found = [p for p in collection.search(dtstart=start, dtend=start + timedelta(minutes=1))
                 if p.sensing_start.replace(tzinfo=timezone.utc, second=0, microsecond=0) == start]
        if not found:
            return "not yet"
        job = tailor.new_customisation(found[0], chain)
        try:
            for _ in range(60):
                if job.status in ("DONE", "FAILED", "KILLED", "INACTIVE"):
                    break
                time.sleep(5)
            if job.status != "DONE":
                return job.status.lower()
            out = next(o for o in job.outputs if o.endswith(".nc"))
            part = target.with_suffix(".part")
            with job.stream_output(out) as stream, open(part, "wb") as f:
                shutil.copyfileobj(stream, f)
        finally:
            job.delete()
        part.rename(target)
        return "fetched"

    def safe(hour):
        try:
            return stamp(hour), one(hour)
        except Exception as exc:  # one hour failing must not stop the others or the build
            text = re.sub(r"access_token=[^&\s'\"]+", "access_token=***", str(exc))
            return stamp(hour), f"error {type(exc).__name__}: {text[:200]}"

    with ThreadPoolExecutor(PARALLEL) as pool:
        results = list(pool.map(safe, todo))
    for s, r in results:
        print(s, r)
    got = sum(r == "fetched" for _, r in results)
    print(f"{got} of {len(todo)} hours fetched in {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
