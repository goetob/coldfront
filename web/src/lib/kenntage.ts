// Words for tropical nights, hot, frost and ice days (meteo/climate.py KENNTAGE).
import type { Kind, Named, StationKenntage } from "./archive";
import { locale, stationName, tr, type Lang } from "./i18n";

export const KINDS: Kind[] = ["tn", "hd", "fd", "ed"];

/** Singular and plural, and the rule. */
export const kindNames: Record<Lang, Record<Kind, { one: string; many: string; rule: string }>> = {
  de: {
    tn: { one: "Tropennacht", many: "Tropennächte", rule: "Tiefstwert mindestens 20 °C" },
    hd: { one: "Hitzetag", many: "Hitzetage", rule: "Höchstwert mindestens 30 °C" },
    fd: { one: "Frosttag", many: "Frosttage", rule: "Tiefstwert unter 0 °C" },
    ed: { one: "Eistag", many: "Eistage", rule: "Höchstwert unter 0 °C" },
  },
  it: {
    tn: { one: "notte tropicale", many: "notti tropicali", rule: "minima di almeno 20 °C" },
    hd: { one: "giorno di canicola", many: "giorni di canicola", rule: "massima di almeno 30 °C" },
    fd: { one: "giorno di gelo", many: "giorni di gelo", rule: "minima sotto 0 °C" },
    ed: { one: "giorno di ghiaccio", many: "giorni di ghiaccio", rule: "massima sotto 0 °C" },
  },
};

export const kindName = (kind: Kind, n: number, lang: Lang) => (n === 1 ? kindNames[lang][kind].one : kindNames[lang][kind].many);

/** "Bozen, Salurn und Auer"; beyond `most` names "Bozen, Salurn, Auer und 4 weiteren". */
export function nameList(stations: Named[], lang: Lang, most = 4) {
  const names = stations.map((s) => stationName(s, lang));
  const and = tr(lang, " und ", " e ");
  if (names.length <= most) return names.length > 1 ? `${names.slice(0, -1).join(", ")}${and}${names.at(-1)}` : names[0] ?? "";
  const rest = names.length - (most - 1);
  return `${names.slice(0, most - 1).join(", ")}${and}${tr(lang, `${rest} weiteren`, `altre ${rest}`)}`;
}

/** Sentences for one day: "Tropennacht in Bozen und Salurn.", "Eistag an 40 Stationen." */
export function dayTexts(k: { tn: Named[]; hd: Named[]; fd?: number; ed?: number } | undefined | null, lang: Lang): string[] {
  if (!k) return [];
  const out: string[] = [];
  for (const kind of ["tn", "hd"] as const) {
    const list = k[kind];
    if (!list.length) continue;
    const one = kindNames[lang][kind].one;
    // The list runs from the lowest station up, so "among them" names the valley floors.
    out.push(list.length <= 6
      ? tr(lang, `${one} in ${nameList(list, lang, 6)}.`, `${cap(one)} a ${nameList(list, lang, 6)}.`)
      : tr(lang, `${one} an ${list.length} Stationen, darunter ${nameList(list.slice(0, 3), lang)}.`,
                 `${cap(one)} in ${list.length} stazioni, tra cui ${nameList(list.slice(0, 3), lang)}.`));
  }
  for (const kind of ["fd", "ed"] as const) {
    const n = k[kind] ?? 0;
    if (n > 0) out.push(tr(lang, `${kindNames.de[kind].one} an ${n} ${n === 1 ? "Station" : "Stationen"}.`,
                                 `${cap(kindNames.it[kind].one)} in ${n} ${n === 1 ? "stazione" : "stazioni"}.`));
  }
  return out;
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** "bis 25. Oktober" */
export const toDate = (d: string, lang: Lang) =>
  tr(lang, "bis ", "fino al ") +
  new Intl.DateTimeFormat(locale(lang), { day: "numeric", month: "long", timeZone: "Europe/Rome" }).format(new Date(`${d}T12:00:00Z`));

/** Kinds a station has seen at all. */
export const kindsSeen = (k: StationKenntage) => KINDS.filter((kind) => k[kind].some((v) => (v ?? 0) > 0));
