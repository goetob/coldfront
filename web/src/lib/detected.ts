// Events found by detect_events.py, with their titles and sentences in either
// language. The file is optional at build time: without it the list is empty.
import { href, num } from "./atlas";
import { locale, path, stationName, tr, type Lang } from "./i18n";

export type EventType = "cold" | "rain" | "snow" | "storm" | "heat";
type At = { value: number; name: string; name_it?: string } | null;
type Count = { peak: string; stations: number; station: At };
export type Detected = {
  id: string;
  types: EventType[];
  start: string;
  end: string;
  hours: number;
  days: string[];
  peak: string;
  ongoing: boolean;
  cold?: { peak: string; median: number; station: At };
  rain?: { peak: string; mean: number; station: At };
  storm?: Count;
  snow?: Count;
  heat?: Count;
};
type Rules = { cold: number; rain: number; gust: number; gust_pct?: number; gust_floor?: number; snow: number; heat: number };
type File = { generated: string; from: string; to: string; rules: Rules; events: Detected[] };

const file = Object.values(import.meta.glob<{ default: File }>("../data/detected.json", { eager: true }))[0]?.default;
export const detected: Detected[] = file?.events ?? [];
export const rules = file?.rules;
export const detectedFrom = file?.from;

const NAMES: Record<EventType, [string, string]> = {
  cold: ["Kaltfront", "Fronte freddo"],
  rain: ["Starkniederschlag", "Forti precipitazioni"],
  snow: ["Neuschnee", "Neve fresca"],
  storm: ["Sturm", "Tempesta"],
  heat: ["Hitze", "Caldo intenso"],
};
export const typeName = (t: EventType, lang: Lang) => tr(lang, ...NAMES[t]);

/** "Hitze und Kaltfront" / "Caldo intenso e fronte freddo" */
export function eventTitle(e: Detected, lang: Lang) {
  const names = e.types.map((t, i) => {
    const n = typeName(t, lang);
    return i > 0 && lang === "it" ? n[0].toLowerCase() + n.slice(1) : n;
  });
  const and = tr(lang, " und ", " e ");
  return names.length < 2 ? names.join("") : `${names.slice(0, -1).join(", ")}${and}${names.at(-1)}`;
}

const utc = (s: string) => new Date(s.replace("Z", ":00Z"));

/** "21.–22. Oktober 2026" / "21–22 ottobre 2026" */
export function eventSpan(e: Detected, lang: Lang) {
  const fmt = new Intl.DateTimeFormat(locale(lang), { day: "numeric", month: "long", year: "numeric", timeZone: "Europe/Rome" });
  return fmt.formatRange(utc(e.start), utc(e.end));
}

/** One sentence per kind of event, with the station where it was strongest. */
export function eventFacts(e: Detected, lang: Lang): string[] {
  const n = (v: number, d = 1) => num(v, d, lang);
  const at = (s: At) => (s ? stationName(s, lang) : "–");
  const out: string[] = [];
  for (const t of e.types) {
    if (t === "cold" && e.cold) {
      const s = e.cold.station;
      out.push(tr(lang,
        `Mittel der letzten 24 Stunden ${n(e.cold.median)} K gegenüber den 24 Stunden davor (Median der Stationen), am stärksten ${at(s)} mit ${n(s?.value ?? 0)} K.`,
        `Media delle ultime 24 ore ${n(e.cold.median)} K rispetto alle 24 ore precedenti (mediana delle stazioni), il calo maggiore a ${at(s)} con ${n(s?.value ?? 0)} K.`));
    } else if (t === "rain" && e.rain) {
      const s = e.rain.station;
      out.push(tr(lang,
        `In 24 Stunden im Mittel ${n(e.rain.mean)} mm, am meisten ${at(s)} mit ${n(s?.value ?? 0)} mm.`,
        `In 24 ore in media ${n(e.rain.mean)} mm, il massimo a ${at(s)} con ${n(s?.value ?? 0)} mm.`));
    } else if (t === "storm" && e.storm) {
      const s = e.storm.station;
      out.push(rules?.gust_pct
        ? tr(lang,
          `Ungewöhnlich starke Böen an ${e.storm.stations} Stationen gleichzeitig (über dem ${n(rules.gust_pct)}. Perzentil der Station, mindestens ${n(rules.gust_floor ?? 17.2)} m/s), die stärkste ${n(s?.value ?? 0)} m/s, ${at(s)}.`,
          `Raffiche insolitamente forti a ${e.storm.stations} stazioni insieme (sopra il ${n(rules.gust_pct)}° percentile della stazione, almeno ${n(rules.gust_floor ?? 17.2)} m/s), la più forte ${n(s?.value ?? 0)} m/s, ${at(s)}.`)
        : tr(lang,
          `Böen ab ${n(rules?.gust ?? 20.8)} m/s (Beaufort 9) an ${e.storm.stations} Stationen gleichzeitig, die stärkste ${n(s?.value ?? 0)} m/s, ${at(s)}.`,
          `Raffiche da ${n(rules?.gust ?? 20.8)} m/s (Beaufort 9) a ${e.storm.stations} stazioni insieme, la più forte ${n(s?.value ?? 0)} m/s, ${at(s)}.`));
    } else if (t === "snow" && e.snow) {
      const s = e.snow.station;
      const cm = n(rules?.snow ?? 20, 0);
      out.push(tr(lang,
        `Mindestens ${cm} cm Neuschnee in 24 Stunden an ${e.snow.stations} Stationen, am meisten ${at(s)} mit ${n(s?.value ?? 0, 0)} cm.`,
        `Almeno ${cm} cm di neve fresca in 24 ore a ${e.snow.stations} stazioni, il massimo a ${at(s)} con ${n(s?.value ?? 0, 0)} cm.`));
    } else if (t === "heat" && e.heat) {
      const s = e.heat.station;
      const deg = n(rules?.heat ?? 32, 0);
      out.push(tr(lang,
        `${deg} Grad oder mehr an ${e.heat.stations} Stationen gleichzeitig, am heißesten ${at(s)} mit ${n(s?.value ?? 0)} °C.`,
        `${deg} gradi o più a ${e.heat.stations} stazioni insieme, la più calda ${at(s)} con ${n(s?.value ?? 0)} °C.`));
    }
  }
  return out;
}

/** Viewer layer that shows the event best. */
const LAYER: Record<EventType, string> = { cold: "t_change", rain: "n24", snow: "snow", storm: "wind", heat: "temperature" };

/** Link to the peak hour in the map viewer, if that hour is still in the rolling window. */
export function viewerLink(e: Detected, lang: Lang, hours: { utc: string; stamp: string }[]) {
  const h = hours.find((x) => x.utc === e.peak);
  return h ? href(`${path(lang, "home")}?v=${LAYER[e.types[0]]}&t=${h.stamp}#karte`) : null;
}

/** Events touching a given local day. */
export const eventsOn = (day: string) => detected.filter((e) => e.days.includes(day));
