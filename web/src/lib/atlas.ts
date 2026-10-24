// A set of hourly maps and their statistics, as written by render_maps.py:
// the rolling window (src/data/now.json) or an event (src/data/events/<slug>.json).

export type Hour = {
  stamp: string;
  utc: string;
  local: string;
  t_mean: number;
  t_min: number;
  t_max: number;
  freezing_level: number | null;
  p_mean: number | null;
  n_area: number;
  n_max: number | null;
  wet_share: number | null;
  gust_max: number | null;
  gust_station: string | null;
  hinge_m: number | null;
  foehn?: string[];
};

export type Legend = { levels: number[]; colors: string[]; names?: string[] };

export type AtlasData = {
  generated: string;
  newest: string;
  hours: Hour[];
  key: { before: string; front: string; after: string };
  total: { area_mean: number; wettest: { name: string; mm: number }[] };
  stations: { temperature: number; precipitation: number; wind: number };
  qc: Record<string, Record<string, number>>;
  legend: Record<string, Legend>;
};

export type Atlas = AtlasData & { set: string };

export const atlas = (raw: unknown, set: string): Atlas => ({ ...(raw as AtlasData), set });

const base = import.meta.env.BASE_URL.replace(/\/$/, "");
export const href = (path: string) => `${base}${path}`;

export const byStamp = (a: Atlas, s: string) => a.hours.find((h) => h.stamp === s)!;

export const mapUrl = (a: Atlas, variable: string, stamp: string, small = false) =>
  href(`/maps/${a.set}/${variable}/${stamp}${small ? "-s" : ""}.webp`);

const tz = "Europe/Rome";
const at = (h: Hour) => new Date(h.utc.replace("Z", ":00Z"));

const hourFmt = new Intl.DateTimeFormat("de-DE", { hour: "numeric", hourCycle: "h23", timeZone: tz });
export const hourOf = (h: Hour) => Number(hourFmt.formatToParts(at(h)).find((p) => p.type === "hour")!.value);

export const dayLabel = (h: Hour) =>
  new Intl.DateTimeFormat("de-DE", { weekday: "long", day: "numeric", month: "long", timeZone: tz }).format(at(h));

export const shortDay = (h: Hour) =>
  new Intl.DateTimeFormat("de-DE", { weekday: "short", day: "numeric", month: "numeric", timeZone: tz }).format(at(h));

export const timeLabel = (h: Hour) => `${dayLabel(h)}, ${hourOf(h)} Uhr`;

export const num = (v: number | null | undefined, digits = 1) =>
  v == null ? "–" : v.toLocaleString("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits });
