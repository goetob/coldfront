// Archived days and station histories, as written by build_archive.py. Both are
// optional at build time: without an archive checkout the pages are just empty.
import type { Legend } from "./atlas";
import { locale, type Lang } from "./i18n";

export type Extreme = { value: number; name: string; name_it?: string } | null;
export type Day = {
  date: string;
  maps: string[];
  tmax_area?: number;
  tmin_area?: number;
  nsum_area?: number;
  warmest: Extreme;
  coldest: Extreme;
  wettest: Extreme;
  windiest: Extreme;
  sunniest: Extreme;
  snow_stations: number;
  stations: number;
  tanom_area?: number;
  anomaly?: { stations: number; above_p90: number; below_p10: number } | null;
  climate?: DayClimate | null;
};
// What stands out about a day against all archived years (meteo/climate.py).
export type Rank = { warm: number; cold: number; years: number; first: number };
export type Since = { since: string } | { record: true; first: string };
export type Notable = { same_date?: Rank; warm_since?: Since; cold_since?: Since; wet_since?: Since };
export type DayClimate = Notable & { t_anom: number | null; tmax_anom: number | null; tmin_anom: number | null; n_net: number | null };
export type Normals = { period: [number, number]; window_days: number; stations: number };
export type Archive = {
  generated: string; days: Day[]; legend: Record<string, Legend>; extent: [number, number, number, number];
  normals?: Normals | null;
};

export type Exposure = "summit" | "slope" | "valley";
export type Quality = "ok" | "short" | "none";
export type StationInfo = {
  code: string; name: string; name_it: string; alt: number; x: number; y: number; exposure: Exposure;
  years?: number; quality?: Quality; // years of data behind the normals
};
export type Series = (number | null)[];
export type StationHistory = {
  code: string;
  name: string;
  name_it: string;
  alt: number;
  lon: number;
  lat: number;
  exposure: Exposure;
  hourly: { times: string[] } & Record<string, Series>;
  daily: { dates: string[] } & Record<string, Series>;
  climate?: StationClimate;
};
export type StationClimate = {
  years: number; quality: Quality; period: [number, number];
  norm_tmean?: Series; norm_tmin?: Series; norm_tmax?: Series; norm_n_mean?: Series; norm_n_freq?: Series;
  notable?: Record<string, Notable>;
};

const one = <T>(files: Record<string, { default: T }>) => Object.values(files)[0]?.default;

export const archive: Archive = one(import.meta.glob<{ default: Archive }>("../data/archive.json", { eager: true }))
  ?? { generated: "", days: [], legend: {}, extent: [0, 1, 0, 1] };

export const stationList: StationInfo[] =
  one(import.meta.glob<{ default: StationInfo[] }>("../data/stations.json", { eager: true })) ?? [];

export const histories: StationHistory[] = Object.values(
  import.meta.glob<{ default: StationHistory }>("../data/stations/*.json", { eager: true }),
).map((m) => m.default);

export const exposureNames: Record<Lang, Record<Exposure, string>> = {
  de: { summit: "Gipfel oder Kamm", slope: "Hang", valley: "Tal" },
  it: { summit: "vetta o cresta", slope: "versante", valley: "fondovalle" },
};

const tz = "Europe/Rome";
const at = (d: string) => new Date(`${d}T12:00:00Z`);
export const dayTitle = (d: string, lang: Lang = "de") =>
  new Intl.DateTimeFormat(locale(lang), { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: tz }).format(at(d));
export const dayShort = (d: string, lang: Lang = "de") =>
  new Intl.DateTimeFormat(locale(lang), { weekday: "short", day: "numeric", month: "numeric", timeZone: tz }).format(at(d));

/** The last value that is not missing, with its index. */
export const lastValue = (s: Series | undefined) => {
  if (!s) return null;
  for (let i = s.length - 1; i >= 0; i--) if (s[i] != null) return { value: s[i]!, i };
  return null;
};
