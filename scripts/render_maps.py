"""Render hourly temperature, precipitation and wind maps for the web page.

Writes, for one set of maps (the rolling window, or an event)
  <out>/<var>/<stamp>.webp       full-size map (1690 x 1090)
  <out>/<var>/<stamp>-s.webp     thumbnail for the small multiples
  <out>/base.webp                relief and places only, under the browser's wind layer
  <out>/<extra>/<stamp>.webp     change in 24 h, humidity, 24-hour precipitation, pressure tendency
  <out>/tmin|tmax/<date>.webp    lowest and highest temperature of each local day
  <out>/viewer.json              station values per hour and day for the browser's map viewer
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
from matplotlib.patches import Polygon
from PIL import Image
from pyproj import Transformer
from scipy.ndimage import gaussian_filter

import fields
from meteo import derive, wind as windlib
from meteo.timeutil import LOCAL, parse_iso, parse_local_hour, stamp as utc_stamp

VARIABLES = ("temperature", "precipitation", "wind")  # every hour, with thumbnails
EXTRA = ("t_change", "humidity", "n24", "ptend")  # every hour with enough stations, full size only
DAILY = ("tmin", "tmax")  # one map per local day
MIN_STATIONS = 20
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
# Change against 24 hours earlier, and 3-hour pressure tendency: diverging, open at
# both ends (levels are the inner bounds). Falling pressure reads warm, rising cool.
DT_LEVELS = [-8, -6, -4, -2, -1, 1, 2, 4, 6, 8]
DT_COLORS = ["#24416a", "#3d6593", "#6a8fb4", "#a3bcd2", "#c9d6e0", "#e7e5df",
             "#ebd3b4", "#ddaa7a", "#c27b4f", "#9b4f37", "#6c3028"]
PT_LEVELS = [-4, -2, -1, -0.5, 0.5, 1, 2, 4]
PT_COLORS = ["#6c3028", "#9b4f37", "#c27b4f", "#ddaa7a", "#e7e5df", "#a3bcd2", "#6a8fb4", "#3d6593", "#24416a"]
# Relative humidity: dry ochre to moist sea green, in 10 % bands.
RH_LEVELS = list(range(0, 101, 10))
RH_COLORS = ["#9c6a32", "#b48649", "#c9a468", "#dbc390", "#e6dcbd", "#d3dfcf",
             "#acc9bf", "#82b0a9", "#578f8c", "#336e6f"]
# 24-hour precipitation; below 1 mm stays clear.
P_DAY = [1, 2, 5, 10, 20, 30, 50, 80]
# Points only: snow depth (cm) and sunshine in the hour (minutes).
HS_LEVELS = [1, 10, 30, 60, 100, 150, 250]
HS_COLORS = ["#dfe6ec", "#c4d2df", "#a3bad0", "#7f9fbe", "#5d82aa", "#3f6493", "#2a4a75"]
SD_LEVELS = [0, 10, 20, 30, 40, 50, 60]
SD_COLORS = ["#ddd9cc", "#e9dcae", "#eccf86", "#e6bd5c", "#daa53a", "#c98b22"]
# Wind arrows: fixed length, strength in colour and weight (see meteo/wind.py).
W_WIDTH = [0, 1.9, 2.4, 3.0, 3.6, 4.3]
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

    def temperature(self, field, stations_xy, stamp, var="temperature", thumbs=True):
        cmap = ListedColormap(T_COLORS)
        norm = BoundaryNorm(T_LEVELS, cmap.N, extend="neither")
        img = self.base(cmap(norm(np.clip(field, T_LEVELS[0], T_LEVELS[-1] - 0.01)))[..., :3])
        smooth = np.where(self.t.inside, gaussian_filter(field, 2.5), np.nan)
        fx, fy = self.t.centers
        for small in (False, True)[: 2 if thumbs else 1]:
            fig, ax = self.figure(img, small)
            if not small:
                ax.contour(fx, fy, smooth, levels=[l for l in T_LEVELS if l % 4 == 0 and l != 0],
                           colors=INK, linewidths=0.45, alpha=0.45)
            zero = ax.contour(fx, fy, smooth, levels=[0], colors=INK,
                              linewidths=0.9 if small else 1.6, alpha=0.9)
            if zero.allsegs[0] and not small:
                ax.clabel(zero, fmt="0°", fontsize=11, inline_spacing=6)
            self.overlay(ax, stations_xy, small)
            self.save(fig, var, stamp, small)

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

    def banded(self, field, stations_xy, var, stamp, levels, colors, clear_below=None, lines=None):
        """A banded field without thumbnail. With len(levels) == len(colors) - 1 the
        levels are inner bounds and both ends are open; otherwise the bands start at
        levels[0], and with clear_below the paper shows through under it."""
        inner = len(levels) == len(colors) - 1
        bounds = [-1e9, *levels, 1e9] if inner else [*levels[: len(colors)], 1e9]
        cmap = ListedColormap(colors)
        norm = BoundaryNorm(bounds, cmap.N)
        rgb = cmap(norm(field))[..., :3]
        if clear_below is not None:
            rgb = np.where((field < clear_below)[..., None], np.array(to_rgb(PAPER)), rgb)
        img = self.base(rgb)
        fig, ax = self.figure(img)
        if lines:
            smooth = np.where(self.t.inside, gaussian_filter(field, 2.5), np.nan)
            fx, fy = self.t.centers
            if np.nanmin(smooth) < max(lines) and np.nanmax(smooth) > min(lines):
                ax.contour(fx, fy, smooth, levels=lines, colors=INK, linewidths=0.6, alpha=0.6)
        self.overlay(ax, stations_xy)
        self.save(fig, var, stamp)

    def base_map(self):
        """Relief, outline and places without data, for the browser's wind layer."""
        fig, ax = self.figure(self.base())
        self.overlay(ax)
        d = self.out
        d.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100)
        plt.close(fig)
        Image.open(buf).convert("RGB").save(d / "base.webp", quality=84, method=4)

    def wind(self, stations, wind, gusts, foehn, stamp):
        img = self.base()
        for small in (False, True):
            fig, ax = self.figure(img, small)
            self.overlay(ax, small=small)
            self.wind_arrows(ax, stations, wind, gusts, foehn, small)
            self.save(fig, "wind", stamp, small)

    def wind_arrows(self, ax, stations, wind, gusts, foehn, small):
        """Arrows of one length pointing downwind; strength is colour and weight.

        Summit arrows have an open head: they show the free flow aloft,
        filled heads the winds that valleys and slopes shape.
        """
        length = 6000 if small else 4000
        scale = 0.55 if small else 1.0
        for st, w, g, f in zip(stations, wind, gusts, foehn):
            if w is None:
                continue
            speed, direction = w
            c = windlib.strength_class(speed)
            color = windlib.COLORS[c]
            if c == 0:
                ax.scatter([st["x"]], [st["y"]], s=10 if small else 30, facecolor="none",
                           edgecolor=color, lw=0.8 if small else 1.2, zorder=7)
                continue
            rad = np.radians(direction)
            ux, uy = -np.sin(rad), -np.cos(rad)  # downwind
            px, py = -uy, ux
            tail = (st["x"] - ux * length / 2, st["y"] - uy * length / 2)
            tip = (st["x"] + ux * length / 2, st["y"] + uy * length / 2)
            head, half = length * 0.34, length * 0.17
            neck = (tip[0] - ux * head, tip[1] - uy * head)
            lw = W_WIDTH[c] * scale
            ax.plot([tail[0], neck[0]], [tail[1], neck[1]], color=color, lw=lw,
                    solid_capstyle="butt", zorder=7, path_effects=halo(lw + 2.2 * scale))
            hollow = st.get("exposure") == "summit"
            ax.add_patch(Polygon(
                [tip, (neck[0] + px * half, neck[1] + py * half), (neck[0] - px * half, neck[1] - py * half)],
                closed=True, facecolor=PAPER if hollow else color, edgecolor=color,
                lw=max(0.8, lw * 0.6), joinstyle="miter", zorder=8,
                path_effects=halo(1.6 * scale) if not hollow else None))
            if small:
                continue
            if g is not None and g >= windlib.GUST_LABEL:
                ax.text(tail[0] - ux * 700, tail[1] - uy * 700, f"{g:.0f}", fontfamily=LABEL_FONT,
                        fontweight="medium", fontsize=10.5, ha="center", va="center", zorder=9,
                        color=windlib.COLORS[windlib.strength_class(g)], path_effects=halo(2.5))
            if f:
                ax.text(st["x"] + px * 1500, st["y"] + py * 1500, "F", fontfamily=SERIF_FONT,
                        style="italic", fontsize=13, ha="center", va="center", zorder=9,
                        color=INK, path_effects=halo(2.5))


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
    for st in stations:
        st["exposure"], st["tpi"] = windlib.exposure(st, terrain)
    # Foehn is judged over the whole record, so the window's first hours see their past.
    foehn = {code: windlib.foehn(w, series.get("WG.BOE", {}).get(code), series.get("LF", {}).get(code),
                                 stations[idx[code]]["exposure"])
             for code, w in series.get("WIND", {}).items()}
    series["FOEHN"] = foehn
    derived_series(series)
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
        prev = previous.get(stamp)
        return (prev is not None and "layers" in prev and k <= end - args.fresh
                and all((Path(args.out) / v / f"{stamp}.webp").exists() for v in (*VARIABLES, *prev["layers"])))

    todo = [k for k in window if not reusable(k)]
    # Hours are independent; forked workers inherit the terrain and renderer.
    _CTX.update(stations=stations, times=times, column=column, terrain=terrain, renderer=r, only=only)
    with ProcessPoolExecutor(args.workers, mp_context=multiprocessing.get_context("fork")) as pool:
        done = dict(zip(todo, pool.map(process_hour, todo)))
    hours = [done[k] if k in done else previous[stamp_of(times[k])] for k in window]
    if not (Path(args.out) / "base.webp").exists():
        r.base_map()

    # Daily lowest and highest temperature, per local day of the window; today so far.
    days = sorted({parse_iso(times[k]).astimezone(LOCAL).date().isoformat() for k in window})
    day_of_hour = [days.index(parse_iso(times[k]).astimezone(LOCAL).date().isoformat()) for k in window]
    daily = {"TMIN": {}, "TMAX": {}}
    for code, lt in series.get("LT", {}).items():
        daily["TMIN"][code], daily["TMAX"][code] = derive.daily_extremes([lt[k] for k in window], day_of_hour, len(days))
    _CTX.update(daily=daily, days=days)
    with ProcessPoolExecutor(args.workers, mp_context=multiprocessing.get_context("fork")) as pool:
        day_maps = [m for m in pool.map(process_day, range(len(days))) if m]
    write_viewer(Path(args.out) / "viewer.json", stations, times, window, series, terrain,
                 hours, days, day_of_hour, daily, day_maps)

    if args.prune:
        keep = {h["stamp"] for h in hours} | {"total"} | set(days)
        for v in (*VARIABLES, *EXTRA, *DAILY):
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
            "wind": {"levels": windlib.LEVELS, "colors": windlib.COLORS,
                     "names": [c[1] for c in windlib.CLASSES]},
            "t_change": {"levels": DT_LEVELS, "colors": DT_COLORS},
            "humidity": {"levels": RH_LEVELS, "colors": RH_COLORS},
            "n24": {"levels": P_DAY, "colors": P_COLORS[: len(P_DAY)]},
            "ptend": {"levels": PT_LEVELS, "colors": PT_COLORS},
            "snow": {"levels": HS_LEVELS, "colors": HS_COLORS},
            "sun": {"levels": SD_LEVELS, "colors": SD_COLORS},
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
    render = _CTX["only"] is None or stamp in _CTX["only"]
    if render:
        r.temperature(tfield, xy_t, stamp)
        r.precipitation(pfield, xy_n, stamp, P_HOURLY)
        r.wind(stations, wind, column("WG.BOE", k), column("FOEHN", k), stamp)

    def enough(values):
        return sum(v is not None for v in values) >= MIN_STATIONS

    def xy(values):
        return ([s["x"] for s, v in zip(stations, values) if v is not None],
                [s["y"] for s, v in zip(stations, values) if v is not None])

    # The field 24 hours earlier, from the stations of that hour.
    layers, extra = [], {}
    if k >= 24 and enough(prev := column("LT", k - 24)):
        dt = tfield - fields.temperature(stations, prev, terrain)[0]
        extra["dt_mean"] = round(float(dt[terrain.inside].mean()), 1)
        layers.append("t_change")
        if render:
            r.banded(dt, xy_t, "t_change", stamp, DT_LEVELS, DT_COLORS, lines=[0])
    if enough(td := column("TD", k)):
        rh = derive.relative_humidity(tfield, fields.temperature(stations, td, terrain)[0])
        extra["rh_mean"] = round(float(rh[terrain.inside].mean()))
        layers.append("humidity")
        if render:
            r.banded(rh, xy(td), "humidity", stamp, RH_LEVELS, RH_COLORS)
    if enough(n24 := column("N24", k)):
        nfield, _ = fields.precipitation(stations, n24, terrain, method="krige")
        extra["n24_area"] = round(float(nfield[terrain.inside].mean()), 1)
        layers.append("n24")
        if render:
            r.banded(nfield, xy(n24), "n24", stamp, P_DAY, P_COLORS[: len(P_DAY)], clear_below=P_DAY[0])
    pt = column("PT3", k)
    if sum(v is not None for v in pt) >= 15:
        pfield3, _ = fields.plain(stations, pt, terrain)
        extra["pt_mean"] = round(float(pfield3[terrain.inside].mean()), 1)
        layers.append("ptend")
        if render:
            r.banded(pfield3, xy(pt), "ptend", stamp, PT_LEVELS, PT_COLORS, lines=[0])
    if render:
        print("rendered", stamp, tinfo, layers, flush=True)

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
        "foehn": [stations[i]["name"] for i, f in enumerate(column("FOEHN", k)) if f],
        "layers": layers,
        **extra,
    }


def process_day(d):
    """Lowest and highest temperature of local day d; returns the layers drawn."""
    stations, terrain, r, daily, days = (_CTX[x] for x in ("stations", "terrain", "renderer", "daily", "days"))
    drawn = []
    for key, var in (("TMIN", "tmin"), ("TMAX", "tmax")):
        by_code = daily[key]
        values = [by_code.get(s["code"], [None] * len(days))[d] for s in stations]
        if sum(v is not None for v in values) < MIN_STATIONS:
            continue
        field, _ = fields.temperature(stations, values, terrain)
        xy = ([s["x"] for s, v in zip(stations, values) if v is not None],
              [s["y"] for s, v in zip(stations, values) if v is not None])
        r.temperature(field, xy, days[d], var=var, thumbs=False)
        drawn.append(var)
    return {"day": days[d], "layers": drawn} if drawn else None


def derived_series(series):
    """Dew point, 24-hour changes and sums, 3-hour pressure tendency, per station."""
    lt, lf = series.get("LT", {}), series.get("LF", {})
    series["TD"] = {code: [derive.dewpoint(t, h) for t, h in zip(lt[code], lf[code])] for code in set(lt) & set(lf)}
    series["DT24"] = {code: derive.change(v, 24) for code, v in lt.items()}
    series["N24"] = {code: derive.rolling_sum(v, 24) for code, v in series.get("N", {}).items()}
    series["PT3"] = {code: derive.change(v, 3) for code, v in series.get("LD.RED", {}).items()}
    series["HS24"] = {code: derive.change(v, 24) for code, v in series.get("HS", {}).items()}


def write_viewer(path, stations, times, window, series, terrain, hours, days, day_of_hour, daily, day_maps):
    """Station values of the window for the browser's map viewer.

    Hourly series per layer and station (the layer names match the map
    directories), daily ones per local day, and which maps exist, since the
    extra layers need enough stations and the 24-hour ones a day of history.
    """
    def hourly(key):
        return {code: [v[k] for k in window] for code, v in series.get(key, {}).items()
                if any(v[k] is not None for k in window)}

    wind, gust, foehn = series.get("WIND", {}), series.get("WG.BOE", {}), series["FOEHN"]
    values = {
        "temperature": hourly("LT"),
        "t_change": hourly("DT24"),
        "precipitation": hourly("N"),
        "n24": hourly("N24"),
        "humidity": hourly("LF"),
        "dewpoint": hourly("TD"),
        "ptend": hourly("PT3"),
        "pressure": hourly("LD.RED"),
        "snow": hourly("HS"),
        "snow24": hourly("HS24"),
        "sun": hourly("SD"),
        "radiation": hourly("GS"),
        "wind": {
            code: [None if w[k] is None else [*w[k], gust.get(code, [None] * len(times))[k], int(foehn[code][k])]
                   for k in window]
            for code, w in wind.items()
        },
        "tmin": daily["TMIN"],
        "tmax": daily["TMAX"],
    }
    used = {code for by_code in values.values() for code in by_code}
    out_stations = [{k: st[k] for k in ("code", "name", "name_it", "alt", "x", "y", "exposure")}
                    for st in stations if st["code"] in used]
    xmin, xmax, ymin, ymax = terrain.extent
    h, w = terrain.shape
    path.write_text(json.dumps({
        "extent": [xmin, xmax, ymin, ymax],
        "size": [w, h],
        "times": [times[k] for k in window],
        "stamps": [stamp_of(times[k]) for k in window],
        "days": days,
        "day_of_hour": day_of_hour,
        "maps": {
            **{layer: [layer in hr.get("layers", []) for hr in hours] for layer in EXTRA},
            **{layer: [any(m["day"] == d and layer in m["layers"] for m in day_maps) for d in days] for layer in DAILY},
        },
        "classes": [{"min": lo, "name": name, "color": color} for lo, name, color in windlib.CLASSES],
        "gust_label": windlib.GUST_LABEL,
        "scales": {
            "temperature": {"levels": T_LEVELS.tolist(), "colors": T_COLORS},
            "precipitation": {"levels": P_HOURLY, "colors": P_COLORS[: len(P_HOURLY)]},
            "snow": {"levels": HS_LEVELS, "colors": HS_COLORS},
            "sun": {"levels": SD_LEVELS, "colors": SD_COLORS},
        },
        "stations": out_stations,
        "values": values,
    }, ensure_ascii=False, separators=(",", ":")))


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
