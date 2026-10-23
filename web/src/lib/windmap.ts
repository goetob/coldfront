// Interactive wind layer: station arrows drawn as SVG over the rendered relief.
// Hours come from wind.json (written by render_maps.py); in live mode the newest
// 10-minute values come straight from the open API of the Landeswetterdienst.

type Station = { code: string; name: string; name_it: string; alt: number; x: number; y: number; exposure: Exposure };
type Exposure = "summit" | "slope" | "valley";
type Value = [speed: number, direction: number, gust: number | null, foehn: number] | null;
type WindData = {
  extent: [number, number, number, number];
  size: [number, number];
  times: string[];
  classes: { min: number; name: string; color: string }[];
  gust_label: number;
  exposures: Record<Exposure, string>;
  stations: Station[];
  values: Record<string, Value[]>;
};
type Frame = { label: string; values: Map<string, Value>; live: boolean };

const API = "https://geoservices.buergernetz.bz.it/services/meteo/v1/sensors";
const LIVE_EVERY = 5 * 60e3;
const SVG = "http://www.w3.org/2000/svg";
const ARROW = 40; // arrow length in map pixels (the map is 1690 px wide)
const WIDTH = [0, 2.2, 2.8, 3.4, 4.1, 4.8];
const EXPOSURE_ONE: Record<Exposure, string> = { summit: "Gipfel oder Kamm", slope: "Hang", valley: "Tal" };
const DIRS = ["Nord", "Nordnordost", "Nordost", "Ostnordost", "Ost", "Ostsüdost", "Südost", "Südsüdost",
  "Süd", "Südsüdwest", "Südwest", "Westsüdwest", "West", "Westnordwest", "Nordwest", "Nordnordwest"];

const fmt = (v: number, digits = 1) =>
  v.toLocaleString("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits });
const local = new Intl.DateTimeFormat("de-DE", {
  weekday: "short", day: "numeric", month: "numeric", hour: "numeric", minute: "2-digit", timeZone: "Europe/Rome",
});
const parseUtc = (s: string) => new Date(s.replace("Z", ":00Z"));
// API dates carry their own offset name: 2026-10-23T15:30:00CEST
const parseApi = (s: string) => new Date(s.slice(0, 19) + (s.endsWith("CEST") ? "+02:00" : "+01:00"));
const direction = (deg: number) => DIRS[Math.round(deg / 22.5) % 16];
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => `&${({ "&": "amp", "<": "lt", ">": "gt", '"': "quot" } as Record<string, string>)[c]};`);

function el<K extends keyof SVGElementTagNameMap>(name: K, attrs: Record<string, string | number> = {}) {
  const node = document.createElementNS(SVG, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  return node;
}

class WindMap {
  root: HTMLElement;
  svg: SVGSVGElement;
  tip: HTMLElement;
  slider: HTMLInputElement;
  when: HTMLOutputElement;
  play: HTMLButtonElement;
  liveButton: HTMLButtonElement | null;
  data!: WindData;
  frame!: Frame;
  index = 0;
  timer: number | undefined;
  liveTimer: number | undefined;
  filter = "all";
  selected: string | null = null;

  constructor(root: HTMLElement) {
    this.root = root;
    this.svg = root.querySelector("svg.windmap-layer")!;
    this.tip = root.querySelector(".windmap-tip")!;
    this.slider = root.querySelector("input[type=range]")!;
    this.when = root.querySelector("output")!;
    this.play = root.querySelector("button.play")!;
    this.liveButton = root.querySelector("button.live");
  }

  async init() {
    const res = await fetch(this.root.dataset.src!);
    this.data = await res.json();
    const n = this.data.times.length;
    this.slider.max = String(n - 1);
    this.slider.value = String(n - 1);
    this.slider.addEventListener("input", () => this.showHour(Number(this.slider.value)));
    this.play.addEventListener("click", () => this.toggleplay());
    this.liveButton?.addEventListener("click", () => (this.frame?.live ? this.showHour(n - 1) : this.goLive()));
    this.root.querySelectorAll<HTMLInputElement>("input[name^=exposure]").forEach((input) =>
      input.addEventListener("change", () => {
        this.filter = input.value;
        this.draw();
      }),
    );
    this.svg.addEventListener("pointerleave", (e) => {
      if ((e as PointerEvent).pointerType === "mouse") this.select(null);
    });
    this.root.querySelector(".windmap-stage")!.addEventListener("click", (e) => {
      if (!(e.target as Element).closest("[data-code]")) this.select(null);
    });
    // Arrows keep a legible size when the map is drawn small (phones).
    let width = 0;
    new ResizeObserver(() => {
      const w = this.svg.clientWidth;
      if (this.frame && Math.abs(w - width) > 40) this.draw();
      width = w;
    }).observe(this.svg);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && this.frame?.live) this.goLive();
    });
    this.root.classList.add("ready");
    if (this.liveButton) await this.goLive();
    else this.showHour(n - 1);
  }

  hourFrame(k: number): Frame {
    const values = new Map<string, Value>();
    for (const s of this.data.stations) values.set(s.code, this.data.values[s.code][k]);
    return { label: `${local.format(parseUtc(this.data.times[k]))} Uhr, Stundenmittel`, values, live: false };
  }

  showHour(k: number) {
    this.stopLive();
    this.index = k;
    this.slider.value = String(k);
    this.frame = this.hourFrame(k);
    this.draw();
  }

  toggleplay() {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = undefined;
      this.play.textContent = "Abspielen";
      this.play.setAttribute("aria-pressed", "false");
      return;
    }
    if (this.frame.live || this.index >= this.data.times.length - 1) this.index = -1;
    this.play.textContent = "Anhalten";
    this.play.setAttribute("aria-pressed", "true");
    this.timer = window.setInterval(() => {
      const next = this.index + 1;
      if (next >= this.data.times.length) return this.toggleplay();
      this.showHour(next);
    }, 450);
  }

  stopLive() {
    clearTimeout(this.liveTimer);
    this.liveTimer = undefined;
    this.liveButton?.setAttribute("aria-pressed", "false");
  }

  async goLive() {
    if (this.timer) this.toggleplay();
    this.stopLive();
    try {
      const res = await fetch(API, { headers: { Accept: "application/json" } });
      const rows: { SCODE: string; TYPE: string; DATE: string; VALUE: number | null }[] = await res.json();
      const by = new Map<string, Record<string, { v: number; t: Date }>>();
      for (const r of rows) {
        if (r.VALUE == null || !["WG", "WR", "WG.BOE"].includes(r.TYPE)) continue;
        const rec = by.get(r.SCODE) ?? {};
        rec[r.TYPE] = { v: r.VALUE, t: parseApi(r.DATE) };
        by.set(r.SCODE, rec);
      }
      const values = new Map<string, Value>();
      let newest = 0;
      for (const s of this.data.stations) {
        const rec = by.get(s.code);
        // Leave out stations whose last report is more than an hour old.
        if (!rec?.WG || !rec.WR || Date.now() - rec.WG.t.getTime() > 3600e3) {
          values.set(s.code, null);
          continue;
        }
        newest = Math.max(newest, rec.WG.t.getTime());
        values.set(s.code, [rec.WG.v, rec.WR.v, rec["WG.BOE"]?.v ?? null, 0]);
      }
      if (!newest) throw new Error("no current values");
      this.frame = { label: `Live, Stand ${local.format(new Date(newest))} Uhr, 10-Minuten-Mittel`, values, live: true };
      this.slider.value = this.slider.max;
      this.liveButton?.setAttribute("aria-pressed", "true");
      this.draw();
      this.liveTimer = window.setTimeout(() => !document.hidden && this.goLive(), LIVE_EVERY);
    } catch {
      this.showHour(this.data.times.length - 1);
      this.when.value += " (Live-Werte gerade nicht erreichbar)";
    }
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
    const { svg, data, frame } = this;
    svg.replaceChildren();
    const k = Math.min(2.6, Math.max(1, 1000 / (svg.clientWidth || 1000)));
    const len = ARROW * k;
    svg.style.setProperty("--k", String(k));
    let shown = 0;
    let strongest: [number, string] | null = null;
    const foehn: string[] = [];
    for (const s of data.stations) {
      const v = frame.values.get(s.code);
      if (!v || !this.visible(s)) continue;
      shown++;
      const [speed, dir, gust, f] = v;
      if (gust != null && (!strongest || gust > strongest[0])) strongest = [gust, s.name];
      if (f) foehn.push(s.name);
      const [px, py] = this.project(s);
      const c = this.strength(speed);
      const color = data.classes[c].color;
      const g = el("g", { "data-code": s.code, class: `st ${s.exposure}${this.selected === s.code ? " selected" : ""}` });
      g.append(el("circle", { cx: px, cy: py, r: 16 * k, class: "hit" }));
      if (c === 0) {
        g.append(el("circle", { cx: px, cy: py, r: 4.5 * k, fill: "none", stroke: color, "stroke-width": 1.6 * k }));
      } else {
        const arrow = el("g", { transform: `translate(${px} ${py}) rotate(${dir})` });
        const head = len * 0.34, half = len * 0.17, w = WIDTH[c] * k;
        const shaft = el("line", { x1: 0, y1: -len / 2, x2: 0, y2: len / 2 - head, stroke: color, "stroke-width": w });
        const tip = el("path", {
          d: `M0,${len / 2} L${half},${len / 2 - head} L${-half},${len / 2 - head} Z`,
          fill: s.exposure === "summit" ? "var(--paper)" : color, stroke: color,
          "stroke-width": Math.max(1.2, w * 0.6), "stroke-linejoin": "miter",
        });
        arrow.append(shaft.cloneNode() as SVGElement, tip.cloneNode() as SVGElement);
        (arrow.firstChild as SVGElement).setAttribute("class", "halo");
        (arrow.childNodes[1] as SVGElement).setAttribute("class", "halo");
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
      g.addEventListener("pointerenter", () => this.select(s.code));
      g.addEventListener("click", (e) => {
        e.stopPropagation();
        this.select(s.code);
      });
      svg.append(g);
    }
    this.when.value = frame.label;
    svg.setAttribute("aria-label",
      `Wind an ${shown} Stationen, ${frame.label}` +
      (strongest ? `. Stärkste Böe ${fmt(strongest[0])} m/s in ${strongest[1]}` : "") +
      (foehn.length ? `. Föhnverdacht: ${foehn.join(", ")}` : ""));
    this.select(this.selected);
  }

  select(code: string | null) {
    this.selected = code;
    this.svg.querySelectorAll(".st.selected").forEach((n) => n.classList.remove("selected"));
    const s = this.data.stations.find((x) => x.code === code);
    const v = code ? this.frame.values.get(code) : null;
    if (!s || !v || !this.visible(s)) {
      this.tip.hidden = true;
      return;
    }
    this.svg.querySelector(`[data-code="${code}"]`)?.classList.add("selected");
    const [speed, dir, gust, f] = v;
    const c = this.data.classes[this.strength(speed)];
    const lines = [
      `<b>${esc(s.name)}</b> <span>${s.alt.toLocaleString("de-DE")} m · ${EXPOSURE_ONE[s.exposure]}</span>`,
      speed < this.data.classes[1].min
        ? `Windstill (${fmt(speed)} m/s)`
        : `${fmt(speed)} m/s aus ${direction(dir)} <i style="color:${c.color}">${c.name}</i>`,
    ];
    if (gust != null) lines.push(`Böen bis ${fmt(gust)} m/s`);
    if (f) lines.push("Föhnverdacht: Nordwind, trockene Luft");
    if (!this.frame.live) {
      const past = [3, 2, 1].map((d) => this.index - d).filter((k) => k >= 0)
        .map((k) => this.data.values[s.code][k]).filter((x): x is NonNullable<Value> => x != null);
      if (past.length) lines.push(`Davor: ${past.map(([sp, d]) => `${fmt(sp)} aus ${direction(d)}`).join(" · ")}`);
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

export function initWindMaps() {
  document.querySelectorAll<HTMLElement>(".windmap").forEach((root) => {
    new WindMap(root).init().catch(() => root.classList.add("failed"));
  });
}
