// Map viewer: one rendered map per hour and variable (webp), with the stations
// drawn as SVG on top. Temperature and precipitation show the interpolated
// field with the measuring stations; wind shows arrows over the bare relief.
// Hours come from viewer.json (written by render_maps.py); in live mode the
// newest 10-minute values come straight from the open API of the Landeswetterdienst.

type Variable = "temperature" | "precipitation" | "wind";
type Exposure = "summit" | "slope" | "valley";
type Station = { code: string; name: string; name_it: string; alt: number; x: number; y: number; exposure: Exposure };
type Wind = [speed: number, direction: number, gust: number | null, foehn: number] | null;
type Scale = { levels: number[]; colors: string[] };
type ViewerData = {
  extent: [number, number, number, number];
  size: [number, number];
  times: string[];
  stamps: string[];
  classes: { min: number; name: string; color: string }[];
  gust_label: number;
  temperature: Scale;
  precipitation: Scale;
  stations: Station[];
  values: {
    temperature: Record<string, (number | null)[]>;
    precipitation: Record<string, (number | null)[]>;
    wind: Record<string, Wind[]>;
  };
};
type Frame = {
  k: number; // hour shown by the raster maps
  live: Date | null; // time of the live station values, if any
  temperature: Map<string, number | null>;
  precipitation: Map<string, number | null>;
  wind: Map<string, Wind>;
};

const VARIABLES: Variable[] = ["temperature", "precipitation", "wind"];
const API = "https://geoservices.buergernetz.bz.it/services/meteo/v1/sensors";
const LIVE_EVERY = 5 * 60e3;
const PLAY_EVERY = 500;
const SVG = "http://www.w3.org/2000/svg";
const ARROW = 40; // arrow length in map pixels (the map is 1690 px wide)
const WIDTH = [0, 2.2, 2.8, 3.4, 4.1, 4.8];
const EXPOSURE_ONE: Record<Exposure, string> = { summit: "Gipfel oder Kamm", slope: "Hang", valley: "Tal" };
const DIRS = ["Nord", "Nordnordost", "Nordost", "Ostnordost", "Ost", "Ostsüdost", "Südost", "Südsüdost",
  "Süd", "Südsüdwest", "Südwest", "Westsüdwest", "West", "Westnordwest", "Nordwest", "Nordnordwest"];

const fmt = (v: number, digits = 1) =>
  v.toLocaleString("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits });
const tz = "Europe/Rome";
const dayHour = new Intl.DateTimeFormat("de-DE", { weekday: "short", day: "numeric", month: "numeric", hour: "numeric", timeZone: tz });
const clock = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit", timeZone: tz });
const dayName = new Intl.DateTimeFormat("de-DE", { weekday: "short", day: "numeric", month: "numeric", timeZone: tz });
const hourNum = new Intl.DateTimeFormat("de-DE", { hour: "numeric", hourCycle: "h23", timeZone: tz });
const parseUtc = (s: string) => new Date(s.replace("Z", ":00Z"));
// API dates carry their own offset name: 2026-10-23T15:30:00CEST
const parseApi = (s: string) => new Date(s.slice(0, 19) + (s.endsWith("CEST") ? "+02:00" : "+01:00"));
const direction = (deg: number) => DIRS[Math.round(deg / 22.5) % 16];
const esc = (s: string) =>
  s.replace(/[&<>"]/g, (c) => `&${({ "&": "amp", "<": "lt", ">": "gt", '"': "quot" } as Record<string, string>)[c]};`);

function el<K extends keyof SVGElementTagNameMap>(name: K, attrs: Record<string, string | number> = {}) {
  const node = document.createElementNS(SVG, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
}

function band(scale: Scale, v: number, open: boolean) {
  // Banded colour as on the maps; precipitation below the first level stays clear.
  if (open && v < scale.levels[0]) return null;
  let i = 0;
  scale.levels.forEach((l, j) => v >= l && (i = j));
  return scale.colors[Math.min(i, scale.colors.length - 1)];
}

class Viewer {
  root: HTMLElement;
  stage: HTMLElement;
  imgs: HTMLImageElement[];
  svg: SVGSVGElement;
  tip: HTMLElement;
  slider: HTMLInputElement;
  when: HTMLOutputElement;
  play: HTMLButtonElement;
  liveButton: HTMLButtonElement | null;
  ticks: HTMLElement;
  data!: ViewerData;
  frame!: Frame;
  variable: Variable;
  timer: number | undefined;
  liveTimer: number | undefined;
  liveCache: { at: number; frame: Frame } | null = null;
  filter = "all";
  labels = false;
  selected: string | null = null;
  front = 0;
  quiet = true; // no URL changes until the reader does something

  constructor(root: HTMLElement) {
    this.root = root;
    this.stage = root.querySelector(".viewer-stage")!;
    this.imgs = [...root.querySelectorAll<HTMLImageElement>(".viewer-stage img")];
    this.svg = root.querySelector("svg.viewer-layer")!;
    this.tip = root.querySelector(".viewer-tip")!;
    this.slider = root.querySelector("input[type=range]")!;
    this.when = root.querySelector("output")!;
    this.play = root.querySelector("button.play")!;
    this.liveButton = root.querySelector("button.live");
    this.ticks = root.querySelector(".viewer-ticks")!;
    this.variable = (root.dataset.variable as Variable) ?? "temperature";
  }

  get last() {
    return this.data.times.length - 1;
  }

  async init() {
    this.data = await (await fetch(this.root.dataset.src!)).json();
    this.slider.max = String(this.last);
    this.drawTicks();

    this.slider.addEventListener("input", () => this.showHour(Number(this.slider.value)));
    this.play.addEventListener("click", () => this.togglePlay());
    this.root.querySelector("button.prev")!.addEventListener("click", () => this.step(-1));
    this.root.querySelector("button.next")!.addEventListener("click", () => this.step(1));
    this.liveButton?.addEventListener("click", () => (this.frame.live ? this.showHour(this.last) : this.goLive()));
    this.root.querySelectorAll<HTMLButtonElement>("[role=tab]").forEach((tab) =>
      tab.addEventListener("click", () => this.setVariable(tab.dataset.var as Variable)));
    this.root.querySelectorAll<HTMLInputElement>("input[name^=exposure]").forEach((input) =>
      input.addEventListener("change", () => {
        this.filter = input.value;
        this.draw();
      }));
    this.root.querySelector<HTMLInputElement>("input.labels")?.addEventListener("change", (e) => {
      this.labels = (e.target as HTMLInputElement).checked;
      this.draw();
    });
    this.root.addEventListener("keydown", (e) => {
      const t = e.target as HTMLElement;
      if (t.matches("input[type=range]") || e.altKey || e.ctrlKey || e.metaKey) return;
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
        e.preventDefault();
        this.step(e.key === "ArrowLeft" ? -1 : 1);
      }
    });
    this.svg.addEventListener("pointerleave", (e) => {
      if ((e as PointerEvent).pointerType === "mouse") this.select(null);
    });
    this.stage.addEventListener("click", (e) => {
      if (!(e.target as Element).closest("[data-code]")) this.select(null);
    });
    // Symbols keep a legible size when the map is drawn small (phones).
    let width = 0;
    new ResizeObserver(() => {
      const w = this.svg.clientWidth;
      if (this.frame && Math.abs(w - width) > 40) this.draw();
      width = w;
    }).observe(this.svg);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && this.frame?.live) this.goLive();
    });
    // Links elsewhere on the page (the hourly thumbnails) open an hour here.
    document.addEventListener("click", (e) => {
      const a = (e.target as Element).closest<HTMLAnchorElement>("a[data-viewer]");
      if (!a || a.dataset.viewer !== this.root.id) return;
      e.preventDefault();
      this.setVariable(a.dataset.var as Variable, false);
      this.showHour(Math.max(0, this.data.stamps.indexOf(a.dataset.stamp!)));
      this.root.scrollIntoView({ behavior: "smooth", block: "start" });
    });

    this.root.classList.add("ready");
    const params = new URLSearchParams(location.search);
    const v = params.get("v") as Variable;
    if (VARIABLES.includes(v)) this.setVariable(v, false);
    const t = params.get("t");
    const k = t ? this.data.stamps.indexOf(t) : -1;
    if (k >= 0) this.showHour(k);
    else if (this.liveButton) await this.goLive();
    else this.showHour(this.last);
    this.quiet = false;
  }

  drawTicks() {
    const n = this.data.times.length;
    this.ticks.replaceChildren();
    this.data.times.forEach((t, k) => {
      const d = parseUtc(t);
      if (Number(hourNum.formatToParts(d).find((x) => x.type === "hour")!.value) !== 0) return;
      const span = document.createElement("span");
      span.style.left = `${(k / Math.max(1, n - 1)) * 100}%`;
      span.textContent = dayName.format(d);
      this.ticks.append(span);
    });
  }

  updateUrl() {
    if (this.quiet || !this.root.hasAttribute("data-url")) return;
    const params = new URLSearchParams(location.search);
    params.set("v", this.variable);
    if (this.frame.live) params.delete("t");
    else params.set("t", this.data.stamps[this.frame.k]);
    history.replaceState(null, "", `${location.pathname}?${params}${location.hash}`);
  }

  setVariable(v: Variable, redraw = true) {
    this.variable = v;
    this.root.dataset.variable = v;
    this.root.querySelectorAll<HTMLButtonElement>("[role=tab]").forEach((tab) =>
      tab.setAttribute("aria-selected", String(tab.dataset.var === v)));
    this.root.querySelectorAll<HTMLElement>("[data-legend]").forEach((lg) => (lg.hidden = lg.dataset.legend !== v));
    if (redraw && this.frame) {
      this.showImage();
      this.draw();
      this.updateUrl();
    }
  }

  hourFrame(k: number): Frame {
    const f: Frame = { k, live: null, temperature: new Map(), precipitation: new Map(), wind: new Map() };
    for (const v of VARIABLES) {
      const values = this.data.values[v] as Record<string, unknown[]>;
      for (const [code, series] of Object.entries(values)) (f[v] as Map<string, unknown>).set(code, series[k]);
    }
    return f;
  }

  step(d: number) {
    const from = this.frame.live ? this.last : this.frame.k;
    this.showHour(Math.min(this.last, Math.max(0, from + d)));
  }

  showHour(k: number) {
    this.stopLive();
    this.slider.value = String(k);
    this.frame = this.hourFrame(k);
    this.showImage();
    this.draw();
    this.updateUrl();
    // Have the next hour ready for stepping or playback.
    if (k < this.last && this.variable !== "wind") new Image().src = this.imageUrl(k + 1);
  }

  imageUrl(k: number) {
    const base = this.root.dataset.maps!;
    return this.variable === "wind" ? `${base}/base.webp` : `${base}/${this.variable}/${this.data.stamps[k]}.webp`;
  }

  showImage() {
    const url = this.imageUrl(this.frame.k);
    const current = this.imgs[this.front];
    if (current.getAttribute("src") === url) return;
    const back = this.imgs[1 - this.front];
    back.onload = () => {
      back.classList.add("shown");
      current.classList.remove("shown");
      this.front = 1 - this.front;
    };
    back.alt = "";
    back.src = url;
  }

  togglePlay() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = undefined;
      this.play.textContent = "Abspielen";
      this.play.setAttribute("aria-pressed", "false");
      return;
    }
    if (this.frame.live || this.frame.k >= this.last) this.showHour(0);
    this.play.textContent = "Anhalten";
    this.play.setAttribute("aria-pressed", "true");
    this.timer = window.setInterval(() => {
      if (this.frame.k >= this.last) return this.togglePlay();
      this.showHour(this.frame.k + 1);
    }, PLAY_EVERY);
  }

  stopLive() {
    clearTimeout(this.liveTimer);
    this.liveTimer = undefined;
    this.liveButton?.setAttribute("aria-pressed", "false");
  }

  async goLive() {
    if (this.timer) this.togglePlay();
    this.stopLive();
    try {
      // Within the refresh interval, reuse the last answer instead of asking again.
      if (!this.liveCache || Date.now() - this.liveCache.at > LIVE_EVERY - 5e3) {
        this.liveCache = { at: Date.now(), frame: await this.fetchLive() };
      }
      this.frame = this.liveCache.frame;
      this.slider.value = String(this.last);
      this.liveButton?.setAttribute("aria-pressed", "true");
      this.showImage();
      this.draw();
      this.updateUrl();
      this.liveTimer = window.setTimeout(() => !document.hidden && this.goLive(), LIVE_EVERY);
    } catch {
      this.showHour(this.last);
      this.when.value += " (Live-Werte gerade nicht erreichbar)";
    }
  }

  async fetchLive(): Promise<Frame> {
    const res = await fetch(API, { headers: { Accept: "application/json" } });
    const rows: { SCODE: string; TYPE: string; DATE: string; VALUE: number | null }[] = await res.json();
    const by = new Map<string, Record<string, { v: number; t: Date }>>();
    for (const r of rows) {
      if (r.VALUE == null || !["LT", "WG", "WR", "WG.BOE"].includes(r.TYPE)) continue;
      const rec = by.get(r.SCODE) ?? {};
      rec[r.TYPE] = { v: r.VALUE, t: parseApi(r.DATE) };
      by.set(r.SCODE, rec);
    }
    // The raster maps and precipitation stay at the newest full hour.
    const frame = this.hourFrame(this.last);
    let newest = 0;
    const fresh = (x?: { t: Date }) => x && Date.now() - x.t.getTime() < 3600e3;
    for (const s of this.data.stations) {
      const rec = by.get(s.code) ?? {};
      if (frame.temperature.has(s.code)) frame.temperature.set(s.code, fresh(rec.LT) ? rec.LT!.v : null);
      if (frame.wind.has(s.code)) {
        frame.wind.set(s.code, fresh(rec.WG) && rec.WR ? [rec.WG!.v, rec.WR.v, rec["WG.BOE"]?.v ?? null, 0] : null);
      }
      for (const x of [rec.LT, rec.WG]) if (fresh(x)) newest = Math.max(newest, x!.t.getTime());
    }
    if (!newest) throw new Error("no current values");
    frame.live = new Date(newest);
    return frame;
  }

  label() {
    const f = this.frame;
    const hour = dayHour.format(parseUtc(this.data.times[f.k])); // "Fr., 23.10., 17 Uhr"
    if (!f.live) return this.variable === "wind" ? `${hour}, Stundenmittel` : hour;
    const live = `Live, Stand ${clock.format(f.live)} Uhr`;
    if (this.variable === "wind") return `${live}, 10-Minuten-Mittel`;
    if (this.variable === "temperature") return `${live} an den Stationen, Fläche von ${hour}`;
    return `Niederschlag der letzten vollen Stunde, ${hour}`;
  }

  project(s: Station): [number, number] {
    const [xmin, xmax, ymin, ymax] = this.data.extent;
    const [w, h] = this.data.size;
    return [((s.x - xmin) / (xmax - xmin)) * w, ((ymax - s.y) / (ymax - ymin)) * h];
  }

  strength(speed: number) {
    let c = 0;
    this.data.classes.forEach((cl, i) => speed >= cl.min && (c = i));
    return c;
  }

  visible(s: Station) {
    if (this.filter === "summit") return s.exposure === "summit";
    if (this.filter === "low") return s.exposure !== "summit";
    return true;
  }

  value(code: string) {
    return this.frame[this.variable].get(code);
  }

  draw() {
    const { svg, data } = this;
    svg.replaceChildren();
    const k = Math.min(2.6, Math.max(1, 1000 / (svg.clientWidth || 1000)));
    svg.style.setProperty("--k", String(k));
    const summary: string[] = [];
    let shown = 0;
    for (const s of data.stations) {
      const v = this.value(s.code);
      if (v == null || !this.visible(s)) continue;
      shown++;
      const [px, py] = this.project(s);
      const g = el("g", { "data-code": s.code, class: `st ${s.exposure}` });
      g.append(el("circle", { cx: px, cy: py, r: 16 * k, class: "hit" }));
      if (this.variable === "wind") this.drawWind(g, v as NonNullable<Wind>, px, py, k);
      else this.drawPoint(g, v as number, px, py, k);
      g.addEventListener("pointerenter", () => this.select(s.code));
      g.addEventListener("click", (e) => {
        e.stopPropagation();
        this.select(s.code);
      });
      svg.append(g);
    }
    this.when.value = this.label();
    summary.push(`${shown} Stationen`, this.label());
    if (this.variable === "wind") {
      const foehn = data.stations.filter((s) => (this.value(s.code) as Wind)?.[3]).map((s) => s.name);
      if (foehn.length) summary.push(`Föhnverdacht: ${foehn.join(", ")}`);
    }
    svg.setAttribute("aria-label", summary.join(". "));
    this.select(this.selected);
  }

  drawPoint(g: SVGGElement, v: number, px: number, py: number, k: number) {
    const live = this.frame.live && this.variable === "temperature";
    const scale = this.data[this.variable as "temperature" | "precipitation"];
    const fill = band(scale, v, this.variable === "precipitation") ?? "var(--paper)";
    // Live values differ from the hour's field, so they get their own colour; hourly
    // stations sit on a field of the same colour and only need to be findable.
    g.append(el("circle", {
      cx: px, cy: py, r: (live || this.labels ? 5.5 : 3.6) * k,
      fill: live || this.variable === "precipitation" ? fill : "var(--ink)",
      stroke: live || this.variable === "precipitation" ? "var(--ink)" : "var(--paper)",
      "stroke-width": 1.1 * k, class: "dot",
    }));
    if (this.labels || live) {
      const t = el("text", { x: px, y: py - 13 * k, class: "value" });
      t.textContent = this.variable === "temperature" ? fmt(v) : v >= 0.05 ? fmt(v) : "";
      g.append(t);
    }
  }

  drawWind(g: SVGGElement, v: NonNullable<Wind>, px: number, py: number, k: number) {
    const { data } = this;
    const [speed, dir, gust, f] = v;
    const len = ARROW * k;
    const c = this.strength(speed);
    const color = data.classes[c].color;
    if (c === 0) {
      g.append(el("circle", { cx: px, cy: py, r: 4.5 * k, fill: "none", stroke: color, "stroke-width": 1.6 * k }));
    } else {
      const summit = g.classList.contains("summit");
      const arrow = el("g", { transform: `translate(${px} ${py}) rotate(${dir})` });
      const head = len * 0.34, half = len * 0.17, w = WIDTH[c] * k;
      const shaft = el("line", { x1: 0, y1: -len / 2, x2: 0, y2: len / 2 - head, stroke: color, "stroke-width": w });
      const tip = el("path", {
        d: `M0,${len / 2} L${half},${len / 2 - head} L${-half},${len / 2 - head} Z`,
        fill: summit ? "var(--paper)" : color, stroke: color,
        "stroke-width": Math.max(1.2, w * 0.6), "stroke-linejoin": "miter",
      });
      for (const part of [shaft, tip]) {
        const halo = part.cloneNode() as SVGElement;
        halo.setAttribute("class", "halo");
        arrow.append(halo);
      }
      arrow.append(shaft, tip);
      g.append(arrow);
      if (gust != null && gust >= data.gust_label) {
        const r = (dir * Math.PI) / 180;
        // Behind the arrow's tail, upwind of the station.
        const t = el("text", {
          x: px + Math.sin(r) * (len / 2 + 10 * k), y: py - Math.cos(r) * (len / 2 + 10 * k),
          class: "gust", fill: data.classes[this.strength(gust)].color,
        });
        t.textContent = fmt(gust, 0);
        g.append(t);
      }
    }
    if (f) {
      const t = el("text", { x: px + 14 * k, y: py - 12 * k, class: "foehn" });
      t.textContent = "F";
      g.append(t);
    }
  }

  select(code: string | null) {
    this.selected = code;
    this.svg.querySelectorAll(".st.selected").forEach((n) => n.classList.remove("selected"));
    const s = this.data.stations.find((x) => x.code === code);
    const v = code ? this.value(code) : null;
    if (!s || v == null || !this.visible(s)) {
      this.tip.hidden = true;
      return;
    }
    this.svg.querySelector(`[data-code="${code}"]`)?.classList.add("selected");
    const lines = [`<b>${esc(s.name)}</b> <span>${s.alt.toLocaleString("de-DE")} m · ${EXPOSURE_ONE[s.exposure]}</span>`];
    const { k: hour, live } = this.frame;
    const series = (this.data.values[this.variable] as Record<string, unknown[]>)[s.code];
    if (this.variable === "temperature") {
      lines.push(`${fmt(v as number)} °C${live ? `, ${clock.format(live)} Uhr` : ""}`);
      const before = hour >= 24 ? (series[hour - 24] as number | null) : null;
      if (before != null) lines.push(`Vor 24 Stunden: ${fmt(before)} °C`);
    } else if (this.variable === "precipitation") {
      lines.push(`${fmt(v as number)} mm in der Stunde`);
      const day = (series.slice(Math.max(0, hour - 23), hour + 1) as (number | null)[]).filter((x) => x != null) as number[];
      if (day.length) lines.push(`Letzte ${day.length} Stunden: ${fmt(day.reduce((a, b) => a + b, 0))} mm`);
    } else {
      const [speed, dir, gust, f] = v as NonNullable<Wind>;
      const c = this.data.classes[this.strength(speed)];
      lines.push(speed < this.data.classes[1].min
        ? `Windstill (${fmt(speed)} m/s)`
        : `${fmt(speed)} m/s aus ${direction(dir)} <i style="color:${c.color}">${c.name}</i>`);
      if (gust != null) lines.push(`Böen bis ${fmt(gust)} m/s`);
      if (f) lines.push("Föhnverdacht: Nordwind, trockene Luft");
      if (!live) {
        const past = [3, 2, 1].map((d) => hour - d).filter((x) => x >= 0)
          .map((x) => series[x] as Wind).filter((x): x is NonNullable<Wind> => x != null);
        if (past.length) lines.push(`Davor: ${past.map(([sp, d]) => `${fmt(sp)} aus ${direction(d)}`).join(" · ")}`);
      }
    }
    this.tip.innerHTML = lines.map((l) => `<p>${l}</p>`).join("");
    const [px, py] = this.project(s);
    const [w, h] = this.data.size;
    this.tip.style.left = `${(px / w) * 100}%`;
    this.tip.style.top = `${(py / h) * 100}%`;
    this.tip.classList.toggle("below", py / h < 0.35);
    this.tip.classList.toggle("left", px / w > 0.65);
    this.tip.hidden = false;
  }
}

export function initViewers() {
  document.querySelectorAll<HTMLElement>(".viewer").forEach((root) => {
    new Viewer(root).init().catch(() => root.classList.add("failed"));
  });
}
