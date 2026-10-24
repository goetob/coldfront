// Archived days and station histories, as written by build_archive.py. Both are
// optional at build time: without an archive checkout the pages are just empty.
import type { Legend } from "./atlas";

export type Extreme = { value: number; name: string } | null;
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
};
export type Archive = { generated: string; days: Day[]; legend: Record<string, Legend>; extent: [number, number, number, number] };

export type Exposure = "summit" | "slope" | "valley";
export type StationInfo = { code: string; name: string; name_it: string; alt: number; x: number; y: number; exposure: Exposure };
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
};

const one = <T>(files: Record<string, { default: T }>) => Object.values(files)[0]?.default;

export const archive: Archive = one(import.meta.glob<{ default: Archive }>("../data/archive.json", { eager: true }))
  ?? { generated: "", days: [], legend: {}, extent: [0, 1, 0, 1] };

export const stationList: StationInfo[] =
  one(import.meta.glob<{ default: StationInfo[] }>("../data/stations.json", { eager: true })) ?? [];

export const histories: StationHistory[] = Object.values(
  import.meta.glob<{ default: StationHistory }>("../data/stations/*.json", { eager: true }),
).map((m) => m.default);

export const exposureName: Record<Exposure, string> = { summit: "Gipfel oder Kamm", slope: "Hang", valley: "Tal" };

const tz = "Europe/Rome";
const at = (d: string) => new Date(`${d}T12:00:00Z`);
export const dayTitle = (d: string) =>
  new Intl.DateTimeFormat("de-DE", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: tz }).format(at(d));
export const dayShort = (d: string) =>
  new Intl.DateTimeFormat("de-DE", { weekday: "short", day: "numeric", month: "numeric", timeZone: tz }).format(at(d));

/** The last value that is not missing, with its index. */
export const lastValue = (s: Series | undefined) => {
  if (!s) return null;
  for (let i = s.length - 1; i >= 0; i--) if (s[i] != null) return { value: s[i]!, i };
  return null;
};
