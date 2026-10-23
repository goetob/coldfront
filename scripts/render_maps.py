"""Render hourly temperature, precipitation and wind maps for the web page.

Writes, for one set of maps (the rolling window, or an event)
  <out>/<var>/<stamp>.webp       full-size map (1690 x 1090)
  <out>/<var>/<stamp>-s.webp     thumbnail for the small multiples
  <meta>                         times, per-hour stats, key hours, legends

Stamps are UTC hours ('20261025T01Z'), so the hour that occurs twice when
daylight saving ends gets two maps.
"""

import argparse
import io
import json
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager, patheffects
from matplotlib.colors import BoundaryNorm, LightSource, ListedColormap, to_rgb
from PIL import Image
from pyproj import Transformer
from scipy.ndimage import gaussian_filter

import fields
from meteo.timeutil import LOCAL, parse_iso, parse_local_hour, stamp as utc_stamp

VARIABLES = ("temperature", "precipitation", "wind")
ROOT = Path(__file__).resolve().parent.parent

for f in (ROOT / "assets/fonts").glob("*.ttf"):
    font_manager.fontManager.addfont(str(f))
LABEL_FONT = "IBM Plex Sans Condensed"
SERIF_FONT = "Newsreader"

PAPER = "#ecebe3"
INK = "#25282b"

# Temperature: diverging around the freezing point, cool slate <-> warm brick.
T_LEVELS = np.arange(-14, 27, 2)
T_COLORS = [
    "#1d3354", "#24416a", "#2e5180", "#3d6593", "#5179a4", "#6a8fb4", "#86a6c4", "#a3bcd2",
    "#c1d1df", "#dde3e6",  # -> 0 °C
    "#ece3d3", "#ebd3b4", "#e6c096", "#ddaa7a", "#d19362", "#c27b4f", "#b06441", "#9b4f37",
    "#843e2f", "#6c3028",
]
# Precipitation: one hue, light -> dark; below the first level stays clear.
P_HOURLY = [0.2, 0.5, 1, 2, 4, 8, 16]
P_TOTAL = [5, 10, 20, 30, 45, 60, 80, 110]
P_COLORS = ["#d9e4e1", "#b9d0cf", "#93b7bb", "#6e9daa", "#4c8299", "#336886", "#22506f", "#173a56"]
W_COLORS = ["#8a8679", "#6b675c", "#4e4a41", "#2b2a26"]
W_LEVELS = [0, 3, 6, 10]
SMALL_WIDTH = 640  # thumbnail width in px

PLACES = [  # name, lon, lat, label offset (m)
    ("Bozen", 11.354, 46.498, (2500, -2500)),
    ("Meran", 11.159, 46.671, (-2500, 2000)),
    ("Brixen", 11.657, 46.716, (2500, 1500)),
    ("Bruneck", 11.936, 46.796, (2500, 1500)),
    ("Sterzing", 11.430, 46.893, (2500, 1500)),
    ("Schlanders", 10.773, 46.628, (0, 2600)),
    ("Innichen", 12.281, 46.733, (0, 2600)),
]
PEAKS = [("Ortler", 10.545, 46.509, 3905)]


def halo(lw=3):
    return [patheffects.withStroke(linewidth=lw, foreground=PAPER)]


class Renderer:
    def __init__(self, terrain, out_dir):
        self.t = terrain
        self.out = Path(out_dir)
        dem = np.nan_to_num(terrain.dem, nan=np.nanmin(terrain.dem))
        ls = LightSource(azdeg=315, altdeg=38)
        self.shade = ls.hillshade(dem, vert_exag=1.6, dx=100, dy=100)
        to_utm = Transformer.from_crs("EPSG:4326", "EPSG:25832", always_xy=True)
        self.places = [(n, *to_utm.transform(lon, lat), off) for n, lon, lat, off in PLACES]
        self.peaks = [(n, *to_utm.transform(lon, lat), h) for n, lon, lat, h in PEAKS]

    def base(self, rgb_inside=None):
        """Paper and relief; rgb_inside tints the province, multiplied with the shade."""
        paper = np.array(to_rgb(PAPER))
        hs = self.shade[..., None]
        outside = paper * (0.80 + 0.20 * hs)
        img = outside.copy()
        inside = self.t.inside
        tint = paper if rgb_inside is None else rgb_inside
        lit = np.broadcast_to(tint, img.shape) * (0.62 + 0.45 * hs)
        img[inside] = np.clip(lit[inside], 0, 1)
        return img

    def figure(self, img, small=False):
        """Full maps are drawn 1:1 on the 100 m grid; thumbnails are drawn
        natively at 640 px so lines and arrows keep a legible pixel width."""
        h, w = self.t.shape
        scale = SMALL_WIDTH / w if small else 1
        fig = plt.figure(figsize=(w * scale / 100, h * scale / 100), dpi=100)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.imshow(img, extent=self.t.extent, interpolation="antialiased" if small else "nearest")
        ax.set_xlim(self.t.extent[:2])
        ax.set_ylim(self.t.extent[2:])
        ax.set_axis_off()
        return fig, ax

    def overlay(self, ax, stations=None, small=False):
        geoms = getattr(self.t.outline, "geoms", [self.t.outline])
        for g in geoms:
            x, y = g.exterior.xy
            ax.plot(x, y, color=INK, lw=0.8 if small else 1.4, alpha=0.85, solid_joinstyle="round")
        if small:
            return
        if stations is not None:
            ax.scatter(*stations, s=6, color=INK, alpha=0.55, lw=0, zorder=4)
        for name, x, y, (dx, dy) in self.places:
            ax.scatter([x], [y], s=16, color=INK, lw=0, zorder=5)
            ax.text(x + dx, y + dy, name.upper(), fontfamily=LABEL_FONT, fontweight="medium",
                    fontsize=12.5, color=INK, ha="left" if dx > 0 else ("right" if dx < 0 else "center"),
                    va="center", zorder=6, path_effects=halo())
        for name, x, y, h in self.peaks:
            ax.scatter([x], [y], marker="^", s=30, color=INK, lw=0, zorder=5)
            ax.text(x, y - 2600, f"{name} {h}", fontfamily=SERIF_FONT, style="italic",
                    fontsize=14, color=INK, ha="center", va="top", zorder=6, path_effects=halo())

    def save(self, fig, var, stamp, small=False):
        d = self.out / var
        d.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        plt.close(fig)
        img = Image.open(buf).convert("RGB")
        img.save(d / f"{stamp}{'-s' if small else ''}.webp", quality=80 if small else 84, method=4)

    # --- variables -------------------------------------------------------

    def temperature(self, field, stations_xy, stamp):
        cmap = ListedColormap(T_COLORS)
        norm = BoundaryNorm(T_LEVELS, cmap.N, extend="neither")
        img = self.base(cmap(norm(np.clip(field, T_LEVELS[0], T_LEVELS[-1] - 0.01)))[..., :3])
        smooth = np.where(self.t.inside, gaussian_filter(field, 2.5), np.nan)
        fx, fy = self.t.centers
        for small in (False, True):
            fig, ax = self.figure(img, small)
            if not small:
                ax.contour(fx, fy, smooth, levels=[l for l in T_LEVELS if l % 4 == 0 and l != 0],
                           colors=INK, linewidths=0.45, alpha=0.45)
            zero = ax.contour(fx, fy, smooth, levels=[0], colors=INK,
                              linewidths=0.9 if small else 1.6, alpha=0.9)
            if zero.allsegs[0] and not small:
                ax.clabel(zero, fmt="0°", fontsize=11, inline_spacing=6)
            self.overlay(ax, stations_xy, small)
            self.save(fig, "temperature", stamp, small)

    def precipitation(self, field, stations_xy, stamp, levels, var="precipitation"):
        cmap = ListedColormap(P_COLORS[: len(levels)])
        norm = BoundaryNorm(levels + [1e6], cmap.N)
        rgba = cmap(norm(field))
        dry = field < levels[0]
        paper = np.array(to_rgb(PAPER))
        img = self.base(np.where(dry[..., None], paper, rgba[..., :3]))
        smooth = np.where(self.t.inside, gaussian_filter(field, 2), np.nan)
        fx, fy = self.t.centers
        for small in (False, True):
            fig, ax = self.figure(img, small)
            if np.nanmax(smooth) > levels[0] and not small:
                ax.contour(fx, fy, smooth, levels=levels, colors=INK, linewidths=0.4, alpha=0.35)
            self.overlay(ax, stations_xy, small)
            self.save(fig, var, stamp, small)

    def wind(self, stations, wind, stamp):
        img = self.base()
        for small in (False, True):
            fig, ax = self.figure(img, small)
            self.overlay(ax, small=small)
            self.wind_arrows(ax, stations, wind, small)
            self.save(fig, "wind", stamp, small)

    def wind_arrows(self, ax, stations, wind, small):
        x, y, u, v, s = [], [], [], [], []
        for st, w in zip(stations, wind):
            if w is None:
                continue
            speed, direction = w
            if speed < 0.5:
                ax.scatter([st["x"]], [st["y"]], s=8 if small else 26, facecolor="none",
                           edgecolor=INK, lw=0.6 if small else 0.9, alpha=0.7, zorder=7)
                continue
            # Arrow points downwind; length grows with sqrt(speed) so calm
            # valleys stay visible next to exposed ridges.
            rad = np.radians(direction)
            length = (3400 if small else 2600) * np.sqrt(speed)
            x.append(st["x"]); y.append(st["y"]); s.append(speed)
            u.append(-np.sin(rad) * length); v.append(-np.cos(rad) * length)
        if x:
            colors = INK if small else [W_COLORS[np.searchsorted(W_LEVELS, sp, side="right") - 1] for sp in s]
            ax.quiver(x, y, u, v, color=colors, scale=1, scale_units="xy", angles="xy",
                      width=6.5e-3 if small else 2.6e-3, headwidth=3.4, headlength=3.6,
                      headaxislength=3.2, pivot="middle", zorder=7,
                      path_effects=halo(1.2 if small else 2.2))


def stamp_of(time_utc):
    return utc_stamp(parse_iso(time_utc))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hourly", default=ROOT / "data/processed/hourly.json")
    parser.add_argument("--out", default=ROOT / "web/public/maps/now")
    parser.add_argument("--meta", default=ROOT / "web/src/data/now.json")
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--start", help="first hour to show, local time YYYY-MM-DDTHH; "
                                        "the window then runs --hours from there, or up to the newest data")
    parser.add_argument("--only", help="comma-separated stamps to render (debugging)")
    parser.add_argument("--workers", type=int, default=os.cpu_count())
    parser.add_argument("--fresh", type=int, default=None,
                        help="reuse maps and stats of earlier runs except for the newest N hours, "
                             "which late station reports can still change")
    parser.add_argument("--prune", action="store_true",
                        help="delete maps of hours that have left the window")
    args = parser.parse_args()

    data = json.loads(Path(args.hourly).read_text())
    times, stations, series = data["times"], data["stations"], data["series"]
    idx = {s["code"]: i for i, s in enumerate(stations)}

    if args.start:
        begin = times.index(parse_local_hour(args.start).strftime("%Y-%m-%dT%H:%MZ"))
        end = min(len(times) - 1, begin + args.hours - 1)
    else:
        end = len(times) - 1
        begin = max(0, end - args.hours + 1)
    window = list(range(begin, end + 1))

    def column(sensor, k):
        vals = [None] * len(stations)
        for code, s in series.get(sensor, {}).items():
            vals[idx[code]] = s[k]
        return vals

    terrain = fields.Terrain.load(ROOT / "data/geo")
    r = Renderer(terrain, args.out)
    terrain.inside, terrain.centers  # computed once, before workers fork
    only = set(args.only.split(",")) if args.only else None

    previous = {}
    if args.fresh is not None and Path(args.meta).exists():
        previous = {h["stamp"]: h for h in json.loads(Path(args.meta).read_text())["hours"]}

    totals = np.zeros(len(stations))
    total_ok = np.zeros(len(stations), dtype=int)
    for k in window:
        for i, v in enumerate(column("N", k)):
            if v is not None:
                totals[i] += v
                total_ok[i] += 1

    def reusable(k):
        stamp = stamp_of(times[k])
        return (stamp in previous and k <= end - args.fresh
                and all((Path(args.out) / v / f"{stamp}.webp").exists() for v in VARIABLES))

    todo = [k for k in window if not reusable(k)]
    # Hours are independent; forked workers inherit the terrain and renderer.
    _CTX.update(stations=stations, times=times, column=column, terrain=terrain, renderer=r, only=only)
    with ProcessPoolExecutor(args.workers, mp_context=multiprocessing.get_context("fork")) as pool:
        done = dict(zip(todo, pool.map(process_hour, todo)))
    hours = [done[k] if k in done else previous[stamp_of(times[k])] for k in window]

    if args.prune:
        keep = {h["stamp"] for h in hours} | {"total"}
        for v in VARIABLES:
            for f in (Path(args.out) / v).glob("*.webp"):
                if f.stem.removesuffix("-s") not in keep:
                    f.unlink()

    # Event total precipitation, only from stations that reported (almost) every hour.
    full = total_ok >= 0.9 * len(window)
    tot = [float(t * len(window) / c) if f else None for t, c, f in zip(totals, total_ok, full)]
    tfield, _ = fields.precipitation(stations, tot, terrain, method="krige")
    xy = ([s["x"] for s, v in zip(stations, tot) if v is not None],
          [s["y"] for s, v in zip(stations, tot) if v is not None])
    r.precipitation(tfield, xy, "total", P_TOTAL, var="precipitation")
    wettest = sorted(((v, stations[i]["name"]) for i, v in enumerate(tot) if v is not None), reverse=True)

    meta = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "newest": times[end],
        "hours": hours,
        "key": key_hours(hours),
        "total": {"area_mean": round(float(tfield[terrain.inside].mean()), 1),
                  "wettest": [{"name": n, "mm": round(v, 1)} for v, n in wettest[:5]]},
        "stations": {"temperature": len(series.get("LT", {})), "precipitation": len(series.get("N", {})),
                     "wind": len(series.get("WIND", {}))},
        "qc": data.get("qc", {}),
        "legend": {
            "temperature": {"levels": T_LEVELS.tolist(), "colors": T_COLORS},
            "precipitation": {"levels": P_HOURLY, "colors": P_COLORS[: len(P_HOURLY)]},
            "total": {"levels": P_TOTAL, "colors": P_COLORS[: len(P_TOTAL)]},
            "wind": {"levels": W_LEVELS, "colors": W_COLORS},
        },
    }
    Path(args.meta).parent.mkdir(parents=True, exist_ok=True)
    Path(args.meta).write_text(json.dumps(meta, ensure_ascii=False, indent=1))


_CTX = {}


def process_hour(k):
    stations, times, column, terrain, r = (_CTX[x] for x in ("stations", "times", "column", "terrain", "renderer"))
    stamp = stamp_of(times[k])
    lt, n, wind = column("LT", k), column("N", k), column("WIND", k)
    xy_t = ([s["x"] for s, v in zip(stations, lt) if v is not None],
            [s["y"] for s, v in zip(stations, lt) if v is not None])
    xy_n = ([s["x"] for s, v in zip(stations, n) if v is not None],
            [s["y"] for s, v in zip(stations, n) if v is not None])

    tfield, tinfo = fields.temperature(stations, lt, terrain)
    pfield, _ = fields.precipitation(stations, n, terrain, method="krige")
    if _CTX["only"] is None or stamp in _CTX["only"]:
        r.temperature(tfield, xy_t, stamp)
        r.precipitation(pfield, xy_n, stamp, P_HOURLY)
        r.wind(stations, wind, stamp)
        print("rendered", stamp, tinfo, flush=True)

    pressure = [v for v in column("LD.RED", k) if v is not None]
    gusts = [(v, stations[i]["name"]) for i, v in enumerate(column("WG.BOE", k)) if v is not None]
    gmax = max(gusts) if gusts else (None, None)
    wet = [v for v in n if v is not None]
    inside = terrain.inside
    return {
        "stamp": stamp,
        "utc": times[k],
        "local": parse_iso(times[k]).astimezone(LOCAL).isoformat(),
        "t_mean": round(float(tfield[inside].mean()), 1),
        "t_min": round(float(tfield[inside].min()), 1),
        "t_max": round(float(tfield[inside].max()), 1),
        "freezing_level": freezing_level(stations, lt),
        "p_mean": round(float(np.mean(pressure)), 1) if pressure else None,
        "n_area": round(float(pfield[inside].mean()), 2),
        "n_max": max(wet) if wet else None,
        "wet_share": round(sum(v >= 0.2 for v in wet) / len(wet), 2) if wet else None,
        "gust_max": gmax[0], "gust_station": gmax[1],
        "hinge_m": tinfo["hinge_m"],
    }


def freezing_level(stations, lt):
    """Altitude where the station temperatures cross 0 °C, from a linear fit."""
    pts = [(s["alt"], v) for s, v in zip(stations, lt) if v is not None and s["alt"] > 800]
    if len(pts) < 10:
        return None
    z, t = np.array(pts).T
    b, a = np.polyfit(z, t, 1)
    return round(float(-a / b), -1) if b < 0 else None


def key_hours(hours):
    """Before, during and after the front.

    The pressure minimum marks the passage. "Before" is the last mostly dry
    hour ahead of it (the warm sector), "after" the coldest hour behind it.
    """
    p = [h["p_mean"] if h["p_mean"] is not None else np.inf for h in hours]
    k = int(np.argmin(p))
    pick = lambda i: hours[max(0, min(len(hours) - 1, i))]["stamp"]
    dry = [i for i in range(k) if (hours[i]["wet_share"] or 0) < 0.15]
    before = dry[-1] if dry else k - 12
    behind = range(k + 1, len(hours))
    after = min(behind, key=lambda i: hours[i]["t_mean"]) if behind else k
    return {"before": pick(before), "front": pick(k), "after": pick(after)}


if __name__ == "__main__":
    main()
