// Words for anomalies and rankings against the normals (meteo/climate.py).
import { num } from "./atlas";
import type { Notable, Rank, Since } from "./archive";
import { locale, tr, type Lang } from "./i18n";

const tz = "Europe/Rome";
const at = (d: string) => new Date(`${d}T12:00:00Z`);
const dayMonth = (d: string, lang: Lang) =>
  new Intl.DateTimeFormat(locale(lang), { day: "numeric", month: "long", timeZone: tz }).format(at(d));
const fullDate = (d: string, lang: Lang, ref?: string) =>
  new Intl.DateTimeFormat(locale(lang), {
    day: "numeric", month: "long", timeZone: tz, ...(ref && ref.slice(0, 4) === d.slice(0, 4) ? {} : { year: "numeric" }),
  }).format(at(d));

const ORD_DE = ["", "zweit", "dritt"];
const ORD_IT = ["", "secondo ", "terzo "];
/** Ranks worth a mention: the top three either way. */
export const rankShown = (r?: Rank) => !!r && (r.warm <= 3 || r.cold <= 3);

/** "wärmster 25. Oktober seit 2015", "il secondo 25 ottobre più freddo dal 2015". */
export function rankText(r: Rank, date: string, lang: Lang) {
  const warm = r.warm <= r.cold;
  const place = (warm ? r.warm : r.cold) - 1;
  const dm = dayMonth(date, lang);
  return tr(lang,
    `${ORD_DE[place]}${warm ? "wärmster" : "kältester"} ${dm} seit ${r.first}`,
    `il ${ORD_IT[place]}${dm} più ${warm ? "caldo" : "freddo"} dal ${r.first}`);
}

/** "seit dem 12. August", "seit Beginn der Reihe (2015)". */
function sinceText(s: Since, date: string, lang: Lang) {
  if ("record" in s) return tr(lang, `seit Beginn der Reihe ${s.first.slice(0, 4)}`, `da inizio serie (${s.first.slice(0, 4)})`);
  return tr(lang, `seit dem ${fullDate(s.since, lang, date)}`, `dal ${fullDate(s.since, lang, date)}`);
}

/** Sentences for what stands out about a day; the network or one station. */
export function notableTexts(n: Notable | undefined | null, date: string, lang: Lang): string[] {
  if (!n) return [];
  const out: string[] = [];
  if (rankShown(n.same_date)) out.push(rankText(n.same_date!, date, lang));
  if (n.warm_since) out.push(`${tr(lang, "wärmster Tag", "giorno più caldo")} ${sinceText(n.warm_since, date, lang)}`);
  if (n.cold_since) out.push(`${tr(lang, "kältester Tag", "giorno più freddo")} ${sinceText(n.cold_since, date, lang)}`);
  if (n.wet_since) out.push(`${tr(lang, "nassester Tag", "giorno più piovoso")} ${sinceText(n.wet_since, date, lang)}`);
  return out;
}

/** First letter up, for a sentence. */
export const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** "+2,3 K" */
export const signed = (v: number | null | undefined, lang: Lang, digits = 1) =>
  v == null ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : "±"}${num(Math.abs(v), digits, lang)} K`;

/** "2,3 Grad wärmer als normal", "nahe am Normalwert". */
export function anomalyText(v: number, lang: Lang) {
  const a = num(Math.abs(v), 1, lang);
  if (Math.abs(v) < 0.5) return tr(lang, "nahe am Normalwert", "vicino alla norma");
  return v > 0
    ? tr(lang, `${a} Grad wärmer als normal`, `${a} gradi più caldo del normale`)
    : tr(lang, `${a} Grad kälter als normal`, `${a} gradi più freddo del normale`);
}
