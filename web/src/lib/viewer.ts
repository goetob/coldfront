// Map viewer: rendered maps per hour or day (webp) with the stations drawn as
// SVG on top. Field layers show an interpolated map with the measuring
// stations; point layers (snow, sun) and wind show the stations over the bare
// relief. Values come from viewer.json (written by render_maps.py); in live
// mode the newest 10-minute values come straight from the open API of the
// Landeswetterdienst.

type Exposure = "summit" | "slope" | "valley";
type Station = { code: string; name: string; name_it: string; alt: number; x: number; y: number; exposure: Exposure };
type Wind = [speed: number, direction: number, gust: number | null, foehn: number] | null;
type Series = Record<string, (number | null)[]>;
type Scale = { levels: number[]; colors: string[] };
type ViewerData = {
  extent: [number, number, number, number];
  size: [number, number];
  times: string[];
  stamps: string[];
  days: string[];
  day_of_hour: number[];
  maps: Record<string, boolean[]>;
  classes: { min: number; name: string; color: string }[];
  gust_label: number;
  scales: Record<"temperature" | "precipitation" | "snow" | "sun", Scale>;
  stations: Station[];
  values: Record<string, Series> & { wind: Record<string, Wind[]> };
};
// hourly: a map every hour; extra: a map in hours with enough data; daily: one
// map per local day; total: one map for the whole window; points/wind: no field.
type Kind = "hourly" | "extra" | "daily" | "total" | "points" | "wind";
type Layer = { id: string; tab: string; kind: Kind; unit: string; digits: number; signed?: boolean };
type Frame = {
  k: number; // hour shown
  live: Date | null; // time of the live station values, if any
  temperature?: Map<string, number | null>; // live overrides
  wind?: Map<string, Wind>;
};

const LAYERS: Layer[] = [
  { id: "temperature", tab: "temperature", kind: "hourly", unit: "°C", digits: 1 },
  { id: "t_change", tab: "temperature", kind: "extra", unit: "K", digits: 1, signed: true },
  { id: "tmin", tab: "temperature", kind: "daily", unit: "°C", digits: 1 },
  { id: "tmax", tab: "temperature", kind: "daily", unit: "°C", digits: 1 },
  { id: "precipitation", tab: "precipitation", kind: "hourly", unit: "mm", digits: 1 },
  { id: "n24", tab: "precipitation", kind: "extra", unit: "mm", digits: 1 },
  { id: "n72", tab: "precipitation", kind: "total", unit: "mm", digits: 0 },
  { id: "humidity", tab: "humidity", kind: "extra", unit: "%", digits: 0 },
  { id: "ptend", tab: "pressure", kind: "extra", unit: "hPa", digits: 1, signed: true },
  { id: "wind", tab: "wind", kind: "wind", unit: "m/s", digits: 1 },
  { id: "snow", tab: "snow", kind: "points", unit: "cm", digits: 0 },
  { id: "sun", tab: "sun", kind: "points", unit: "min", digits: 0 },
];
const byId = Object.fromEntries(LAYERS.map((l) => [l.id, l]));
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
const signed = (v: number, digits = 1) => (v > 0 ? "+" : v < 0 ? "−" : "±") + fmt(Math.abs(v), digits);
const tz = "Europe/Rome";
const clock = new Intl.DateTimeFormat("de-DE", { hour: "2-digit", minute: "2-digit", timeZone: tz });
const dayName = new Intl.DateTimeFormat("de-DE", { weekday: "short", day: "numeric", month: "numeric", timeZone: tz });
const hourNum = new Intl.DateTimeFormat("de-DE", { hour: "numeric", hourCycle: "h23", timeZone: tz });
const hourOf = (d: Date) => Number(hourNum.formatToParts(d).find((x) => x.type === "hour")!.value);
const dayHourFmt = (d: Date) => `${dayName.format(d)}, ${hourOf(d)} Uhr`; // "Fr., 23.10., 3 Uhr"
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

function band(scale: Scale, v: number) {
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
  layer: Layer = byId.temperature;
  lastOfTab: Record<string, string> = {};
  windowSum: Map<string, number | null> = new Map();
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
  }

  get last() {
    return this.data.times.length - 1;
  }

  async init() {
    this.data = await (await fetch(this.root.dataset.src!)).json();
    this.slider.max = String(this.last);
    this.drawTicks();
    // Precipitation over the whole window, from stations that reported (almost) every hour.
    for (const [code, s] of Object.entries(this.data.values.precipitation)) {
      const ok = s.filter((v) => v != null) as number[];
      this.windowSum.set(code, ok.length >= 0.9 * s.length ? (ok.reduce((a, b) => a + b, 0) * s.length) / ok.length : null);
    }

    this.slider.addEventListener("input", () => this.showHour(Number(this.slider.value)));
    this.play.addEventListener("click", () => this.togglePlay());
    this.root.querySelector("button.prev")!.addEventListener("click", () => this.step(-1));
    this.root.querySelector("button.next")!.addEventListener("click", () => this.step(1));
    this.liveButton?.addEventListener("click", () => (this.frame.live ? this.showHour(this.last) : this.goLive()));
    this.root.querySelectorAll<HTMLButtonElement>("[role=tab]").forEach((tab) =>
      tab.addEventListener("click", () => {
        const t = tab.dataset.tab!;
        this.setLayer(this.lastOfTab[t] ?? LAYERS.find((l) => l.tab === t)!.id);
      }));
    this.root.querySelectorAll<HTMLButtonElement>(".viewer-sub button").forEach((b) =>
      b.addEventListener("click", () => this.setLayer(b.dataset.layer!)));
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
      this.setLayer(a.dataset.var!, false);
      this.showHour(Math.max(0, this.data.stamps.indexOf(a.dataset.stamp!)));
      this.root.scrollIntoView({ behavior: "smooth", block: "start" });
    });

    this.root.classList.add("ready");
    const params = new URLSearchParams(location.search);
    const v = params.get("v");
    if (v && byId[v]) this.setLayer(v, false);
    else this.setLayer("temperature", false);
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
      // Midnights, except one too close to the right end to have room for its label.
      if (hourOf(d) !== 0 || k / Math.max(1, n - 1) > 0.92) return;
      const span = document.createElement("span");
      span.style.left = `${(k / Math.max(1, n - 1)) * 100}%`;
      span.textContent = dayName.format(d);
      this.ticks.append(span);
    });
  }

  updateUrl() {
    if (this.quiet || !this.root.hasAttribute("data-url")) return;
    const params = new URLSearchParams(location.search);
    params.set("v", this.layer.id);
    if (this.frame.live) params.delete("t");
    else params.set("t", this.data.stamps[this.frame.k]);
    history.replaceState(null, "", `${location.pathname}?${params}${location.hash}`);
  }

  setLayer(id: string, redraw = true) {
    const layer = byId[id] ?? byId.temperature;
    this.layer = layer;
    this.lastOfTab[layer.tab] = layer.id;
    this.root.dataset.variable = layer.kind === "wind" ? "wind" : layer.kind === "points" ? "points" : "field";
    this.root.querySelectorAll<HTMLButtonElement>("[role=tab]").forEach((tab) =>
      tab.setAttribute("aria-selected", String(tab.dataset.tab === layer.tab)));
    this.root.querySelectorAll<HTMLElement>(".viewer-sub").forEach((sub) => (sub.hidden = sub.dataset.tab !== layer.tab));
    this.root.querySelectorAll<HTMLButtonElement>(".viewer-sub button").forEach((b) =>
      b.setAttribute("aria-pressed", String(b.dataset.layer === layer.id)));
    this.root.querySelectorAll<HTMLElement>("[data-legend]").forEach((lg) => (lg.hidden = lg.dataset.legend !== layer.id));
    if (redraw && this.frame) {
      this.showImage();
      this.draw();
      this.updateUrl();
    }
  }

  step(d: number) {
    const from = this.frame.live ? this.last : this.frame.k;
    this.showHour(Math.min(this.last, Math.max(0, from + d)));
  }

  showHour(k: number) {
    this.stopLive();
    this.slider.value = String(k);
    this.frame = { k, live: null };
    this.showImage();
    this.draw();
    this.updateUrl();
    // Have the next hour ready for stepping or playback.
    if (k < this.last) {
      const next = this.imageUrl(k + 1);
      if (next !== this.imageUrl(k)) new Image().src = next;
    }
  }

  hasMap(k: number) {
    const { kind, id } = this.layer;
    if (kind === "extra") return this.data.maps[id]?.[k] ?? false;
    if (kind === "daily") return this.data.maps[id]?.[this.data.day_of_hour[k]] ?? false;
    return kind === "hourly" || kind === "total";
  }

  imageUrl(k: number) {
    const base = this.root.dataset.maps!;
    const { kind, id } = this.layer;
    if (!this.hasMap(k)) return `${base}/base.webp`;
    if (kind === "total") return `${base}/precipitation/total.webp`;
    if (kind === "daily") return `${base}/${id}/${this.data.days[this.data.day_of_hour[k]]}.webp`;
    return `${base}/${id}/${this.data.stamps[k]}.webp`;
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
    // Everything else stays at the newest full hour.
    const frame: Frame = { k: this.last, live: null, temperature: new Map(), wind: new Map() };
    let newest = 0;
    const fresh = (x?: { t: Date }) => x && Date.now() - x.t.getTime() < 3600e3;
    for (const s of this.data.stations) {
      const rec = by.get(s.code) ?? {};
      if (s.code in this.data.values.temperature) frame.temperature!.set(s.code, fresh(rec.LT) ? rec.LT!.v : null);
      if (s.code in this.data.values.wind) {
        frame.wind!.set(s.code, fresh(rec.WG) && rec.WR ? [rec.WG!.v, rec.WR.v, rec["WG.BOE"]?.v ?? null, 0] : null);
      }
      for (const x of [rec.LT, rec.WG]) if (fresh(x)) newest = Math.max(newest, x!.t.getTime());
    }
    if (!newest) throw new Error("no current values");
    frame.live = new Date(newest);
    return frame;
  }

  liveLayer() {
    return this.frame.live != null && (this.layer.id === "temperature" || this.layer.id === "wind");
  }

  series(id: string, code: string) {
    return (this.data.values[id] as Series | undefined)?.[code];
  }

  value(code: string, id = this.layer.id): number | Wind | null | undefined {
    const { k } = this.frame;
    if (this.liveLayer() && id === this.layer.id) return this.frame[id as "temperature" | "wind"]!.get(code);
    if (id === "wind") return this.data.values.wind[code]?.[k];
    if (id === "n72") return this.windowSum.get(code);
    if (byId[id]?.kind === "daily") return this.series(id, code)?.[this.data.day_of_hour[k]];
    return this.series(id, code)?.[k];
  }

  label() {
    const { k, live } = this.frame;
    const { id, kind } = this.layer;
    const t = parseUtc(this.data.times[k]);
    const hour = dayHourFmt(t);
    if (this.liveLayer()) {
      const now = `Live, Stand ${clock.format(live!)} Uhr`;
      return id === "wind" ? `${now}, 10-Minuten-Mittel` : `${now} an den Stationen, Fläche von ${hour}`;
    }
    let text = hour;
    if (id === "wind") text = `${hour}, Stundenmittel`;
    else if (id === "t_change") text = `${hour}, gegenüber 24 Stunden vorher`;
    else if (id === "n24") text = `24 Stunden bis ${hour}`;
    else if (id === "ptend") text = `${hour}, Änderung in 3 Stunden`;
    else if (id === "sun") text = `${hour}, Sonnenschein in der Stunde`;
    else if (kind === "total") {
      text = `Summe vom ${dayHourFmt(parseUtc(this.data.times[0]))} bis ${dayHourFmt(parseUtc(this.data.times[this.last]))}`;
    } else if (kind === "daily") {
      const d = this.data.day_of_hour[k];
      const today = d === this.data.day_of_hour[this.last];
      text = `${dayName.format(t)}${today ? `, bis ${hourOf(parseUtc(this.data.times[this.last]))} Uhr` : ""}`;
    }
    if (live) text += " (letzte volle Stunde)";
    if (kind !== "points" && kind !== "wind" && !this.hasMap(k)) text += ": für diesen Zeitpunkt keine Karte";
    if (kind === "points") {
      const vals = this.data.stations.map((s) => this.value(s.code) as number | null | undefined).filter((v) => v != null);
      if (id === "snow" && vals.every((v) => v! < 1)) text += ": an keiner Station liegt Schnee";
      if (id === "sun" && vals.every((v) => v === 0)) text += ": keine Sonne an den Stationen";
    }
    return text;
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

  draw() {
    const { svg, data } = this;
    svg.replaceChildren();
    const k = Math.min(2.6, Math.max(1, 1000 / (svg.clientWidth || 1000)));
    svg.style.setProperty("--k", String(k));
    let shown = 0;
    for (const s of data.stations) {
      const v = this.value(s.code);
      if (v == null || !this.visible(s)) continue;
      shown++;
      const [px, py] = this.project(s);
      const g = el("g", { "data-code": s.code, class: `st ${s.exposure}` });
      g.append(el("circle", { cx: px, cy: py, r: 16 * k, class: "hit" }));
      if (this.layer.kind === "wind") this.drawWind(g, v as NonNullable<Wind>, px, py, k);
      else if (this.layer.kind === "points") this.drawSymbol(g, v as number, px, py, k);
      else this.drawPoint(g, v as number, px, py, k);
      g.addEventListener("pointerenter", () => this.select(s.code));
      g.addEventListener("click", (e) => {
        e.stopPropagation();
        this.select(s.code);
      });
      svg.append(g);
    }
    this.when.value = this.label();
    const summary = [`${shown} Stationen`, this.label()];
    if (this.layer.kind === "wind") {
      const foehn = data.stations.filter((s) => (this.value(s.code) as Wind)?.[3]).map((s) => s.name);
      if (foehn.length) summary.push(`Föhnverdacht: ${foehn.join(", ")}`);
    }
    svg.setAttribute("aria-label", summary.join(". "));
    this.select(this.selected);
  }

  text(v: number) {
    const { digits, signed: sign } = this.layer;
    return sign ? signed(v, digits) : fmt(v, digits);
  }

  drawPoint(g: SVGGElement, v: number, px: number, py: number, k: number) {
    // Live temperatures differ from the hour's field, so they get their own colour;
    // on a field of the same colour the stations only need to be findable.
    const live = this.liveLayer();
    g.append(el("circle", {
      cx: px, cy: py, r: (live || this.labels ? 5.5 : 3.6) * k,
      fill: live ? band(this.data.scales.temperature, v) : "var(--ink)",
      stroke: live ? "var(--ink)" : "var(--paper)", "stroke-width": 1.1 * k, class: "dot",
    }));
    // Live values are written out unless the map is drawn too small for them (phones).
    if (this.labels || (live && k < 1.5)) {
      const t = el("text", { x: px, y: py - 13 * k, class: "value" });
      t.textContent = this.text(v);
      g.append(t);
    }
  }

  drawSymbol(g: SVGGElement, v: number, px: number, py: number, k: number) {
    // Snow: area grows with depth; sun: one size, colour by minutes of sunshine.
    const snow = this.layer.id === "snow";
    const scale = this.data.scales[snow ? "snow" : "sun"];
    const empty = snow ? v < 1 : false;
    const r = snow ? (empty ? 4 : 6 + Math.sqrt(v) * 1.6) : 8;
    g.append(el("circle", {
      cx: px, cy: py, r: r * k,
      fill: empty ? "none" : band(scale, v), stroke: empty ? "var(--ink-3)" : "var(--ink)",
      "stroke-width": (empty ? 1.2 : 0.9) * k, class: "dot",
    }));
    if (this.labels || (snow && !empty)) {
      const t = el("text", { x: px, y: py - (r + 9) * k, class: "value" });
      t.textContent = this.text(v);
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

  tooltip(s: Station, v: number | NonNullable<Wind>): string[] {
    const { id, unit } = this.layer;
    const { k, live } = this.frame;
    const at = (key: string, hours = 0) => this.series(key, s.code)?.[k - hours] ?? null;
    const lines: string[] = [];
    if (id === "wind") {
      const [speed, dir, gust, f] = v as NonNullable<Wind>;
      const c = this.data.classes[this.strength(speed)];
      lines.push(speed < this.data.classes[1].min
        ? `Windstill (${fmt(speed)} m/s)`
        : `${fmt(speed)} m/s aus ${direction(dir)} <i style="color:${c.color}">${c.name}</i>`);
      if (gust != null) lines.push(`Böen bis ${fmt(gust)} m/s`);
      if (f) lines.push("Föhnverdacht: Nordwind, trockene Luft");
      if (!this.liveLayer()) {
        const past = [3, 2, 1].filter((d) => k - d >= 0).map((d) => this.data.values.wind[s.code]?.[k - d])
          .filter((x): x is NonNullable<Wind> => x != null);
        if (past.length) lines.push(`Davor: ${past.map(([sp, d]) => `${fmt(sp)} aus ${direction(d)}`).join(" · ")}`);
      }
      return lines;
    }
    const n = v as number;
    const main = `${this.text(n)} ${unit}`;
    if (id === "temperature") {
      lines.push(`${main}${this.liveLayer() ? `, ${clock.format(live!)} Uhr` : ""}`);
      const before = k >= 24 ? at("temperature", 24) : null;
      if (before != null) lines.push(`Vor 24 Stunden: ${fmt(before)} °C`);
    } else if (id === "t_change") {
      lines.push(`${main} gegenüber 24 Stunden vorher`);
      const now = at("temperature");
      if (now != null) lines.push(`Jetzt ${fmt(now)} °C`);
    } else if (id === "tmin" || id === "tmax") {
      lines.push(`${id === "tmin" ? "Tiefstwert" : "Höchstwert"} ${main}`);
    } else if (id === "precipitation") {
      lines.push(`${main} in der Stunde`);
      const day = at("n24");
      if (day != null) lines.push(`Letzte 24 Stunden: ${fmt(day)} mm`);
    } else if (id === "n24") {
      lines.push(`${main} in 24 Stunden`);
    } else if (id === "n72") {
      lines.push(`${main} im ganzen Zeitraum`);
    } else if (id === "humidity") {
      lines.push(`${main} relative Feuchte`);
      const td = at("dewpoint");
      if (td != null) lines.push(`Taupunkt ${fmt(td)} °C`);
    } else if (id === "ptend") {
      lines.push(`${main} in 3 Stunden`);
      const p = at("pressure");
      if (p != null) lines.push(`Luftdruck ${fmt(p)} hPa`);
    } else if (id === "snow") {
      lines.push(n < 1 ? "Kein Schnee" : `${main} Schnee`);
      const d = at("snow24");
      if (d != null && Math.abs(d) >= 1) lines.push(`${signed(d, 0)} cm in 24 Stunden`);
    } else if (id === "sun") {
      lines.push(`${fmt(n, 0)} Minuten Sonne in der Stunde`);
      const gs = at("radiation");
      if (gs != null) lines.push(`Globalstrahlung ${fmt(gs, 0)} W/m²`);
    }
    return lines;
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
    const lines = [
      `<b>${esc(s.name)}</b> <span>${s.alt.toLocaleString("de-DE")} m · ${EXPOSURE_ONE[s.exposure]}</span>`,
      ...this.tooltip(s, v as number | NonNullable<Wind>),
    ];
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
