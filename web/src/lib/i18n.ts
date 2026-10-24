// The site in German and Italian: routes, formats and station names.
// German lives at the root, Italian under /it/ with its own path names.

export type Lang = "de" | "it";
export const LANGS: Lang[] = ["de", "it"];

const LOCALES: Record<Lang, string> = { de: "de-DE", it: "it-IT" };
export const locale = (lang: Lang) => LOCALES[lang];

export type Route = "home" | "events" | "event" | "archive" | "day" | "stations" | "station";
const SEGMENTS: Record<Lang, Record<"events" | "archive" | "stations", string>> = {
  de: { events: "ereignisse", archive: "archiv", stations: "stationen" },
  it: { events: "eventi", archive: "archivio", stations: "stazioni" },
};

/** Site path of a page, without the deployment base: path("it", "day", "2026-10-22"). */
export function path(lang: Lang, route: Route, param?: string) {
  const root = lang === "de" ? "/" : `/${lang}/`;
  const seg = SEGMENTS[lang];
  switch (route) {
    case "home": return root;
    case "events": return `${root}${seg.events}/`;
    case "event": return `${root}${seg.events}/${param}/`;
    case "archive": return `${root}${seg.archive}/`;
    case "day": return `${root}${seg.archive}/${param}/`;
    case "stations": return `${root}${seg.stations}/`;
    case "station": return `${root}${seg.stations}/${param}/`;
  }
}

export const other = (lang: Lang): Lang => (lang === "de" ? "it" : "de");
export const languageName: Record<Lang, string> = { de: "Deutsch", it: "Italiano" };

type Named = { name: string; name_it?: string };
/** A station's name in the page language; the API carries both. */
export const stationName = (s: Named, lang: Lang) => (lang === "it" && s.name_it ? s.name_it : s.name);

/** German station names to Italian ones, for texts that carry only the German name. */
export const nameMap = (names: Record<string, string> | undefined, lang: Lang) =>
  (name: string | null | undefined) => (name == null ? "" : lang === "it" ? names?.[name] ?? name : name);

/** Pick the string of the page language. */
export const tr = <T>(lang: Lang, de: T, it: T) => (lang === "de" ? de : it);
