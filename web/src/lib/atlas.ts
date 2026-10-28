// A set of hourly maps and their statistics, as written by render_maps.py:
// the rolling window (src/data/now.json) or an event (src/data/events/<slug>.json).
import { locale, type Lang } from "./i18n";

export type Hour = {
  stamp: string;
  utc: string;
  local: string;
  t_mean: number;
  t_min: number;
  t_max: number;
  t_min_alt?: number | null; // elevation of the coldest and warmest map cell
  t_max_alt?: number | null;
  freezing_level: number | null;
  p_mean: number | null;
  n_area: number;
  n_max: number | null;
  wet_share: number | null;
  gust_max: number | null;
  gust_station: string | null;
  hinge_m: number | null;
  top_m?: number | null; // summit hinge of the temperature trend
  foehn?: string[];
  fog?: string[]; // stations in fog or low stratus, by day (meteo/fog.py)
  fog_top?: [low: number, high: number] | null;
  layers?: string[];
  ta_mean?: number; // 24-hour mean against normal, area mean (K)
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
  names?: Record<string, string>; // German station name -> Italian
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

/** "Freitag, 23. Oktober" / "venerdì 23 ottobre" */
export const dayLabel = (h: Hour, lang: Lang = "de") =>
  new Intl.DateTimeFormat(locale(lang), { weekday: "long", day: "numeric", month: "long", timeZone: tz }).format(at(h));

/** "Fr., 23.10." / "ven 23/10" */
export const shortDay = (h: Hour, lang: Lang = "de") =>
  new Intl.DateTimeFormat(locale(lang), { weekday: "short", day: "numeric", month: "numeric", timeZone: tz }).format(at(h));

/** "17 Uhr" / "ore 17" */
export const clock = (hour: number, lang: Lang = "de") => (lang === "de" ? `${hour} Uhr` : `ore ${hour}`);

export const timeLabel = (h: Hour, lang: Lang = "de") => `${dayLabel(h, lang)}, ${clock(hourOf(h), lang)}`;

export const num = (v: number | null | undefined, digits = 1, lang: Lang = "de") =>
  v == null ? "–" : v.toLocaleString(locale(lang), { minimumFractionDigits: digits, maximumFractionDigits: digits });
