import raw from "../data/event.json";

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
};

export type Legend = { levels: number[]; colors: string[] };

export const event = raw as {
  generated: string;
  hours: Hour[];
  key: { before: string; front: string; after: string };
  total: { area_mean: number; wettest: { name: string; mm: number }[] };
  stations: { temperature: number; precipitation: number; wind: number };
  legend: Record<"temperature" | "precipitation" | "total" | "wind", Legend>;
};

const tz = "Europe/Rome";
const at = (h: Hour) => new Date(h.utc.replace("Z", ":00Z"));

export const hourOf = (h: Hour) =>
  Number(new Intl.DateTimeFormat("de-DE", { hour: "numeric", hourCycle: "h23", timeZone: tz }).format(at(h)));

export const dayLabel = (h: Hour) =>
  new Intl.DateTimeFormat("de-DE", { weekday: "long", day: "numeric", month: "long", timeZone: tz }).format(at(h));

export const shortDay = (h: Hour) =>
  new Intl.DateTimeFormat("de-DE", { weekday: "short", day: "numeric", month: "numeric", timeZone: tz }).format(at(h));

export const timeLabel = (h: Hour) => `${dayLabel(h)}, ${hourOf(h)} Uhr`;

export const byStamp = (s: string) => event.hours.find((h) => h.stamp === s)!;

export const num = (v: number | null | undefined, digits = 1) =>
  v == null ? "–" : v.toLocaleString("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export const mapUrl = (variable: string, stamp: string, small = false) =>
  `${import.meta.env.BASE_URL.replace(/\/$/, "")}/maps/${variable}/${stamp}${small ? "-s" : ""}.webp`;
