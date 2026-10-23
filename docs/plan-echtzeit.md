# Plan: Vom Ereignis-Atlas zur laufenden Wetterkarte Südtirols

Stand: 23. Oktober 2026. Dieser Plan beschreibt, wie aus der Kaltfront-Seite eine
Seite wird, die immer den aktuellen Stand zeigt, ein Stück Vergangenheit
mitführt, Wind deutlich besser darstellt und weitere Messgrößen aufnimmt.

---

## 1. Ausgangslage

### Was es heute gibt

| Baustein | Heute | Für Dauerbetrieb |
|---|---|---|
| `fetch_meteo.py` | holt 7 Sensoren für ein Zeitfenster, merged in JSON pro Station×Sensor, committet nach `main` | Prinzip gut, aber Rohdaten in Git wachsen unbegrenzt (13 MB nach 4 Tagen ≈ 1,2 GB/Jahr plus Git-Historie) |
| `build_hourly.py` | verarbeitet **alle** Rohdaten jedes Mal komplett | muss auf ein Zeitfenster und inkrementell umgestellt werden |
| `render_maps.py` | Temperatur (Höhentrend + Kriging), Niederschlag (Kriging), Wind (Pfeile), Wiederverwendung per `--fresh` | Methoden gut, das Kartenbild bleibt. Fenster und Dateinamen müssen sich ändern |
| `fields.py` | Interpolation, 100-m-Gelände, 1-km-Arbeitsgitter | unverändert weiterverwendbar |
| Astro-Seite | eine erzählende Seite für 48 h, Schlüsselstunden vorher/Front/nachher | wird zur Ereignis-Vorlage. Die neue Startseite braucht einen interaktiven Kartenbetrachter |
| Workflows | Abruf manuell, Seitenbau danach, Karten im `actions/cache` | Zeitplan, Datenhaltung ohne `main`-Commits |

### Was die API kann (am 23.10.2026 geprüft)

- **Historie:** `timeseries` liefert 10-Minuten-Werte mindestens **bis 2015** zurück.
  Ein Jahr für ein Station×Sensor-Paar kommt in **einer** Anfrage (≈ 42 000 Zeilen, wenige Sekunden).
  → Die API ist selbst das Rohdatenarchiv. Wir müssen 10-Minuten-Werte nicht dauerhaft speichern.
- **Aktueller Stand:** `sensors` ohne Parameter liefert den **neuesten Wert aller
  Stationen und Sensoren** in einem Aufruf (≈ 185 kB, unter 2 s).
- **CORS:** `Access-Control-Allow-Origin: *`, d. h. **der Browser kann die API direkt abfragen**.
  Live-Werte brauchen also weder Server noch Neubau der Seite.
- **Verzögerung:** Der Wert von 15:30 war um ≈ 15:43 abrufbar, also etwa 10–15 Minuten.
- **Messgrößen** (Anzahl Stationen): Lufttemperatur 98, rel. Feuchte 97, Wind/Richtung/Böe 83,
  Sonnenscheindauer 61, Niederschlag 60, Luftdruck 59, Globalstrahlung 52, Schneehöhe 26,
  dazu Hydrologie: Wassertemperatur 124, Grundwasser 68, Pegel 64, Abfluss 27, Schwebstoff 8.

### Ein Fehler, der im Dauerbetrieb sofort auftritt

Karten heißen nach **Ortszeit** (`%Y%m%d%H`). Am **25. Oktober 2026** (Ende der Sommerzeit,
übermorgen) gibt es 02:00 Uhr zweimal, und die zweite Karte überschreibt die erste.
→ Dateinamen und Schlüssel werden auf **UTC** umgestellt, Ortszeit gibt es nur noch in der Anzeige.

---

## 2. Ziele und Grundsätze

1. **Immer aktuell:** Die Startseite zeigt den neuesten Stand, höchstens ≈ 20 Minuten alt
   (Grenze ist die API-Verzögerung).
2. **Mit Vergangenheit:** Stündliche Karten der letzten Tage zum Durchblättern und Abspielen,
   Tageskarten länger, Stationsverläufe.
3. **Gleiche Bildsprache:** Papierton, Relief, die bestehenden Farbskalen und Schriften bleiben.
4. **Ehrlich:** Messung und Interpolation bleiben unterscheidbar. Wo ein Modell dazukommt
   (z. B. Windfeld), steht es ausdrücklich dabei.
5. **Kein eigener Server, solange es ohne geht:** GitHub Actions + Pages + Browser.
   Alles muss aus der API reproduzierbar sein.
6. **Ereignisse bleiben:** Die Kaltfront-Seite wird die erste Seite unter „Ereignisse“, mit eingefrorenen Daten.

---

## 3. Architektur: drei Schichten

```
                         Meteo-API Landeswetterdienst (CC0, CORS offen, Historie ≥ 2015)
                           │                     │                         │
            alle 5 min     │       alle 30 min    │           täglich 01:xx │
          (im Browser)     ▼        (Actions)     ▼            (Actions)    ▼
 ┌────────────────────────────┐ ┌──────────────────────────────┐ ┌───────────────────────────┐
 │ LIVE                       │ │ STUNDE                       │ │ ARCHIV                    │
 │ GET /sensors               │ │ fetch (letzte 3 h, inkr.)    │ │ Vortag einfrieren:        │
 │ → Stationswerte als SVG    │ │ → QC → Stundenwerte          │ │ Stundenwerte → Parquet    │
 │   über der letzten Karte   │ │ → Felder (Kriging)           │ │ Tageswerte (Tmin/Tmax/    │
 │ → Windpfeile live          │ │ → Karten der letzten 72 h    │ │   Summen/Böen) → Parquet  │
 │ → „Stand 15:30“            │ │ → frames.json, stations.json │ │ Tageskarten               │
 └──────────────┬─────────────┘ └───────────────┬──────────────┘ └─────────────┬─────────────┘
                │                               │                              │
                └────────────► GitHub Pages (statisch): Astro-Seite + Karten + JSON ◄──┘
```

- **Live** läuft komplett im Browser und braucht keinen Build. Damit sind Wind und Stationswerte
  so frisch wie die API.
- **Stunde** erzeugt die Rasterkarten, deren Kriging zu schwer für den Browser ist,
  und hält ein rollendes Fenster.
- **Archiv** schreibt einmal am Tag unveränderliche Dateien. Damit bleibt die Git-Historie klein.

### Rhythmus und Grenzen

- Actions-Zeitplan: zweimal pro Stunde (z. B. `:17` und `:47`, kurz nach Eintreffen der 10-Minuten-Werte).
  GitHub-Cron kann sich um 5–30 Minuten verspäten, deshalb trägt die Live-Schicht die echte Aktualität.
- Pages: 48 Deploys/Tag liegen unter der weichen Grenze von 10/Stunde. Die Seite (≈ 150–250 MB Karten)
  bleibt deutlich unter dem 1-GB-Limit.
- Bei öffentlichem Repo kosten Actions-Minuten nichts. Ein Lauf dauert ≈ 2–4 min
  (Abruf ≈ 1 min, ≈ 2–4 neue Stunden × Variablen rendern).

---

## 4. Datenhaltung: Brauchen wir eine Datenbank?

### Kurz: keinen Datenbankserver, aber eine Dateidatenbank im Parquet-Format

Begründung:

- **Rohdaten** (10 min) hält die API seit mindestens 2015 vor. Wir speichern sie nur für das rollende
  Fenster und nur im Actions-Cache, nicht in Git.
- **Stundenwerte** (Aggregate nach QC) sind *unser* Mehrwert: geprüft, vektoriell gemittelt, abgeleitet.
  Klein: ≈ 100 Wetterstationen × ≈ 12 Größen × 24 h ≈ 30 000 Werte/Tag → **≈ 50 kB/Tag als Parquet,
  ≈ 20 MB/Jahr**.
- **Tageswerte** (Tmin, Tmax, Summen, max. Böe, Sonnenstunden, Neuschnee): ≈ 1 MB/Jahr.
- Abfragen sind immer „Zeitfenster × Stationen“. Das kann DuckDB direkt auf Parquet-Dateien,
  in Python wie im Browser (DuckDB-WASM), ohne Server.

### Optionen im Vergleich

| Option | Pro | Contra | Urteil |
|---|---|---|---|
| **A. Wie heute: JSON in `main`** | einfach | wächst ≈ 1 GB/Jahr, stündliche Commits verstopfen `main` | ✗ |
| **B. Parquet-Dateien auf eigenem `data`-Branch, täglich eingefroren** (+ Actions-Cache für das laufende Fenster) | kostenlos, versioniert, kein Konto, DuckDB-fähig, unveränderliche Dateien → kleine Historie | Git ist kein Objektspeicher. Nach Jahren evtl. Umzug nötig | **✓ Empfehlung** |
| **C. Parquet in Objektspeicher (Cloudflare R2 / S3)** | beliebig groß, auch für Archivkarten, direkt per HTTP für den Browser | Konto + Secrets, kleine Kosten (R2: 10 GB frei) | ✓ wenn das Archiv groß werden soll |
| **D. SQLite/DuckDB-Datei** | eine Datei, SQL | Binärdatei ändert sich bei jedem Lauf → Git-Historie explodiert | ✗ (nur als lokaler Cache) |
| **E. Postgres/TimescaleDB auf eigenem Server** | echte 10-min-Pipeline, freie Abfragen, eigene API, Alarme | Server ≈ 5–10 €/Monat, Wartung, Sicherheit, Backups | erst wenn wir eine dynamische API oder Benachrichtigungen wollen |

### Layout (Option B)

```
data-Branch
  hourly/2026/10/2026-10-22.parquet      station, time, var, value, flagged   (unveränderlich)
  daily/2026/2026-10-22.parquet          station, date, tmin, tmax, n_sum, gust_max, sun_h, hs, …
  stations.json                          Stationsliste; ihre Git-Historie zeigt Änderungen

main
  data/events/2026-10-kaltfront/         eingefrorene Stundenwerte + event.json für Ereignisseiten
```

(Umgesetzt in Phase 0: Tageswerte als eine Datei pro Tag statt einer Jahresdatei, damit
keine Datei je neu geschrieben wird; gemessen ≈ 35 kB pro Tag.)

Das laufende Fenster (heute + 72 h zurück) wird bei jedem Lauf aus Cache + API neu gebildet.
Ein Verlust des Caches kostet nur einen längeren Abruf.

**Rückwärts füllen:** Weil die API ≥ 10 Jahre liefert, lassen sich Stunden- und Tageswerte
einmalig für 2015–2026 nachrechnen (≈ 600 Paare × 1 Anfrage pro Jahr ≈ 6 000 Anfragen, ein Lauf von
1–2 h, gestaffelt). Damit werden **Abweichungen vom Normalwert** möglich
(„5 Grad wärmer als üblich für Ende Oktober“, siehe Phase 4).

---

## 5. Pipeline neu

```
scripts/
  meteo/api.py          API-Client (aus fetch_meteo.py), Retry, Rate-Limit, Paar-Liste
  meteo/timeutil.py     CET/CEST-Parsing, UTC-Stempel, Stundengrenzen, DST-sicher
  fetch.py              inkrementell: pro Paar ab letztem bekannten Zeitpunkt − 2 h Überlappung
  qc.py                 Plausibilitätsprüfung (s. u.), setzt Flags statt Werte zu löschen
  aggregate.py          10 min → Stunde (wie build_hourly, aber fensterweise, mehr Größen)
  derive.py             Taupunkt, 24-h-Änderungen, Summen 24/72 h, Drucktendenz, Föhn-Index …
  render_maps.py        wie bisher + neue Variablen, UTC-Stempel, rollendes Fenster, Aufräumen
  freeze_day.py         Vortag → hourly/…parquet, daily/…parquet (data-Branch)
  backfill.py           einmalig: Jahre nachrechnen
  build_event.py        Ereignis aus Archiv ausschneiden → events/<slug>/
```

### Qualitätskontrolle (neu, wichtig für Dauerbetrieb)

Bei einem einzelnen Ereignis fallen Ausreißer auf. Im Dauerbetrieb läuft niemand mehr über die Karten.

1. **Wertebereich** je Größe (z. B. T −45…45 °C, Böe < 70 m/s, LF 0…100 %).
2. **Sprungtest** (T > 6 K in 10 min an Talstationen), **Hängenbleiben** (exakt gleicher Wert über
   ≥ 6 h bei Wind/Temperatur, typisch für vereiste Windmesser im Winter).
3. **Räumlicher Abgleich:** Leave-one-out gegen das Kriging-Feld. Eine Station, die > 4σ abweicht,
   wird für diese Stunde markiert und aus der Interpolation genommen.
4. **Niederschlag im Winter:** ungeheizte Messer unter 0 °C unterschätzen Schnee. Solche Werte
   werden markiert und auf der Karte kenntlich gemacht.
5. Flags wandern mit ins Parquet und in die Stations-Tooltips („Wert unsicher“).

### Rollendes Fenster und Aufräumen

- Stündliche Karten: **72 h** (einstellbar), ältere werden gelöscht. Die Wiederverwendung über
  `--fresh` bleibt, nur noch die letzten 3 h werden neu gerechnet (späte Meldungen).
- Tageskarten: **90 Tage** auf Pages. Ältere lassen sich jederzeit aus dem Archiv neu rendern
  und werden nicht dauerhaft gehalten.
- `frames.json` ersetzt `event.json`: Liste der verfügbaren Stunden, Kennzahlen je Stunde,
  Legenden, Stand. `stations.json` enthält Stationen mit Kennzahlen und Kurzverlauf (letzte 72 h).

---

## 6. Karten und Messgrößen

### 6.1 Was bleibt

- **Temperatur:** Methode und Darstellung unverändert, sie ist schon gut.
  Neu: 0-°C-Linie auch auf den Vorschaubildern betonen, Werte an Stationen per Hover (Live-Schicht).
- **Niederschlag:** Stundensumme bleibt. Neu kommen **Summen über 24 h / 72 h / 7 Tage** dazu
  (die bisherige „Summe“ wird damit ein fester Zeitraum statt „Ereignis“).
  Optional Schnee/Regen-Unterscheidung: oberhalb der Schneefallgrenze (Nullgradgrenze − ≈ 300 m,
  aus T/Td-Feld) schraffiert.

### 6.2 Wind neu (Schwerpunkt)

**Probleme heute:**
- Vier Grautöne für die Stärke sind kaum unterscheidbar, auf den Vorschaubildern gibt es gar keine Farbe.
- Die Pfeillänge (∝ √v) ist schwer zu lesen und konkurriert mit der Richtung. Lange Gipfelpfeile
  überdecken Nachbarn.
- Gipfel- und Talstationen sehen gleich aus, obwohl sie physikalisch Verschiedenes messen
  (freie Strömung gegen kanalisierte Talwinde).
- Böen, oft die eigentliche Nachricht, sind nur als Kurve sichtbar, nicht auf der Karte.
- Stundenmittel glätten. Für „jetzt“ ist der letzte 10-Minuten-Wert interessanter.
- Es gibt keine Werte zum Ablesen.

**Neues Konzept: Wind als SVG im Browser über einer gerenderten Reliefkarte**

Wind wird nicht interpoliert. Deshalb gibt es keinen Grund, ihn ins Rasterbild einzubrennen.
Als SVG-Ebene über der Grundkarte wird er scharf, interaktiv und live:

1. **Richtung = Pfeil, Stärke = Farbe + Strichstärke.** Pfeile haben eine feste Länge
   (lesbare Richtung, keine Überdeckung). Die Stärke zeigt eine eigene sequenzielle Skala in 6 Klassen
   nach Beaufort, in einem Farbton, der weder mit Temperatur (Schiefer/Ziegel) noch mit
   Niederschlag (Petrol) kollidiert, z. B. Sand → Ocker → Pflaume.
   Klassen (m/s): < 1,5 still · 1,5–5,5 schwach · 5,5–10,8 mäßig · 10,8–17,2 stark · 17,2–24,5 stürmisch · > 24,5 Sturm.
2. **Böen sichtbar:** ab ≈ 17 m/s ein Böen-Etikett an der Station („26“), sonst als Ring oder Zacke am Pfeilende.
3. **Exposition aus dem Geländemodell:** Für jede Station wird ihre relative Höhe über der
   Umgebung (Topographic Position Index, 2–5 km Radius) aus `terrain.npz` berechnet.
   Daraus folgen drei Klassen: *Gipfel/Kamm*, *Hang*, *Tal*. Gipfelpfeile werden hohl gezeichnet,
   Talpfeile gefüllt. Ein Schalter zeigt „nur Höhenwind“ oder „nur Talwind“, auf Wunsch als zwei
   kleine Karten nebeneinander (Höhenströmung gegen Talwinde).
4. **Hover/Tippen:** Station, Höhe, Mittel, Böe, Richtung in Worten („Nordwest“), Trend der letzten 3 h
   als kleiner Pfeilverlauf.
5. **Live:** Auf der Startseite die neuesten 10-Minuten-Werte direkt aus `/sensors`, im Zeitregler
   die Stundenmittel aus `frames`.
6. **Föhn-Kennung:** Nordföhn ist in Südtirol nach Kaltfronten typisch. Eine Station gilt als
   föhnig, wenn der Wind aus N–NO kommt, die Feuchte binnen 3 h um > 20 Prozentpunkte fällt und die
   Temperatur trotz Kaltluft steigt. Die Kennung erscheint als Kennzeichen auf der Karte und im Lage-Text.
   Ausbau: Druckdifferenz Innsbruck–Bozen (GeoSphere Austria Open Data) als Föhn-Kurve.
7. **Stationsdetail:** Windrose der letzten 7 Tage, Verlauf von Mittel und Böe.
8. **(Optional, Phase 5) Modell-Windfeld als Hintergrund:** Strömungslinien oder Partikel-Animation
   aus einem hochaufgelösten Modell (z. B. ICON-D2 oder ICON-2I über Open-Meteo), deutlich als
   „Modell“ beschriftet, mit den Messpfeilen obendrauf. Das ergibt das schönste Bild, ist aber
   keine Messung mehr. Darüber entscheidest du.

Die Rasterkarte `wind/<stamp>.webp` entfällt. Für Vorschaubilder und Social-Preview wird
weiterhin eine statische Fassung gerendert, mit der neuen Farbskala.

### 6.3 Neue Messgrößen und abgeleitete Karten

| Größe | Quelle | Darstellung | Methode | Prio |
|---|---|---|---|---|
| **24-h-Temperaturänderung** | LT | Differenzkarte, divergierend | Differenz zweier T-Felder | hoch: macht Fronten sofort sichtbar, kostet fast nichts |
| **Tmin / Tmax des Tages** | LT | Tageskarte | Felder aus Stundenwerten | hoch |
| **Rel. Feuchte / Taupunkt** | LF + LT | Fläche | Taupunkt mit Höhentrend + Kriging wie T, Feuchte aus T- und Td-Feld (physikalisch konsistent) | hoch (Föhn, Nebel) |
| **Niederschlag 24 h / 72 h / 7 d** | N | Fläche | Kriging der Summen | hoch |
| **Luftdruck-Tendenz 3 h** | LD.RED | Fläche oder Pfeile ↑↓ | Kriging der Tendenz | mittel (Frontannäherung) |
| **Schneehöhe + Neuschnee 24 h** | HS (26) | Punkte, keine Fläche (zu dünn) | Stationswerte, Größe = Höhe | mittel, im Winter hoch |
| **Sonne / Bewölkung** | SD, GS | Punkte; Sonnenstunden als Tageskarte | Clear-Sky-Index = GS / berechnete Klarhimmelstrahlung mit Geländeabschattung | mittel |
| **Gefühlte Temperatur** | LT, WG, LF | Fläche | Windchill/Hitzeindex | niedrig |
| **Pegel / Abfluss** | W, Q | Gewässernetz mit Stationssymbolen, farbig nach Perzentil | braucht Gewässergeometrie (Provinz/OSM) | niedrig, bei Hochwasser hoch |
| Wassertemperatur, Grundwasser, Schwebstoff | – | – | – | vorerst nicht |

**Externe Ergänzungen (prüfen, nicht eingeplant):** Niederschlagsradar, Blitze,
Lawinenlagebericht der Euregio (offene CAAML-Daten), Prognosen. Zu jedem müssten Lizenz und
Zugang geklärt werden.

---

## 7. Webseite

### Struktur

```
/                       Jetzt: großer Kartenbetrachter + Lage in Zahlen + Lage-Text
/karten/<variable>      Direktlinks (temperatur, niederschlag, wind, feuchte, schnee, …)
/stationen/             Liste/Karte aller Stationen
/stationen/<code>       Verlauf 7 Tage (alle Größen), Windrose, Tagesextreme 30 Tage
/archiv/                Tageskarten der letzten 90 Tage, Kalenderansicht
/ereignisse/            kuratierte Ereignisse
/ereignisse/2026-10-kaltfront   die heutige Seite, eingefroren
/methode                Methodik, Datenqualität, Lizenzen
```

### Kartenbetrachter (Kernstück der neuen Startseite)

- **Variablen-Reiter:** Temperatur · Niederschlag · Wind · Feuchte · Δ24 h · (Schnee im Winter).
- **Zeitregler** über 72 h mit Wiedergabe (Animation), Tastatur ←/→, Tage als Marken,
  „Jetzt“-Knopf. Bilder der Nachbarstunden werden vorgeladen, Bildwechsel per Überblendung.
- **Ebenen:** gerendertes Rasterfeld (webp) + SVG-Ebene (Stationen, Wind, Werte, Tooltips).
  Stationskoordinaten liegen in EPSG:25832 und die Kartenausdehnung ist bekannt. Die Umrechnung
  ist eine lineare Abbildung ohne Kartenbibliothek. Eine Kachelkarte (MapLibre) ist nicht nötig,
  weil das 100-m-Raster ohnehin nicht weiter zoomt.
- **Zustand in der URL** (`?v=wind&t=2026-10-23T13Z`), damit sich Links teilen lassen.
- **Lage in Zahlen:** wärmste und kälteste Station, nassester Ort 24 h, stärkste Böe 24 h,
  Nullgradgrenze, Drucktendenz, jeweils mit Sparkline (vorhandene `Trace`-Komponente).
- **Lage-Text:** Vorlagen-basiert wie heute, aber ohne Ereignisannahme
  („Seit gestern Abend 6 Grad kälter, Nordföhn im Wipptal, Böen bis 85 km/h am Brenner“).
- **Veraltet-Hinweis:** Ist der neueste Kartenstand älter als 2 h, erscheint ein Banner.
  Die Live-Schicht zeigt trotzdem aktuelle Stationswerte.
- **Kleine Vielfache** (HourGrid) bleiben als Ansicht „Übersicht 72 h“ unter dem Betrachter.

### Technik

- Astro bleibt. Der Betrachter wird eine Insel in TypeScript, ohne Framework oder mit Preact (≈ 4 kB).
- Diagramme: eigene SVG-Komponenten wie `Trace`, für Stationsverläufe ggf. uPlot (≈ 45 kB).
- Stationsseiten werden aus `stations.json` statisch erzeugt. Längere Verläufe lädt der Browser
  nach Bedarf (JSON je Station, oder Parquet per DuckDB-WASM, falls Option C kommt).
- Barrierefreiheit: Alt-Texte aus Kennzahlen generiert, Regler per Tastatur bedienbar,
  Farbskalen bleiben bei Farbschwäche unterscheidbar (die vorhandenen tun das bereits weitgehend).

---

## 8. Betrieb

| Workflow | Auslöser | Tut |
|---|---|---|
| `update.yml` | Cron `17,47 * * * *` + manuell | fetch → qc → aggregate → derive → render (fresh 3 h) → astro build → Pages-Deploy. Cache: Rohdaten-Fenster, Karten |
| `freeze.yml` | Cron täglich 01:23 UTC | Vortag aus API vollständig neu holen → Parquet → Commit auf `data`-Branch → Tageskarten |
| `backfill.yml` | manuell, Jahr als Eingabe | Jahre nachrechnen → `data`-Branch |
| `event.yml` | manuell, Start/Ende/Slug | Ereignis einfrieren → Seite unter `/ereignisse/<slug>` |

- **Überwachung:** `fetch_log` als Kennzahl in `frames.json` (Fehler, leere Paare, Alter des
  neuesten Werts). Bei zwei Fehlschlägen in Folge legt der Workflow ein Issue an.
- **Sicherheit vor API-Änderungen:** Die API ist 2026 schon einmal umgezogen. Basis-URL und
  Feldnamen stehen deshalb an einer Stelle, und ein Smoke-Test vor jedem Lauf prüft das Format.
- **Kosten:** 0 € bei öffentlichem Repo (Option B). Option C ≈ 0 € im R2-Freikontingent.
- **Tests:** Unit-Tests für Zeitlogik (DST!), Aggregation (Vektormittel, Lücken), QC-Regeln,
  sowie ein Snapshot-Test des Renderers mit Fixture-Daten. Läuft in CI vor dem Deploy.

---

## 9. Umsetzung in Phasen

| Phase | Inhalt | Ergebnis | Umfang |
|---|---|---|---|
| **0 Fundament** | UTC-Stempel (DST-Fehler), Pipeline fensterweise und inkrementell, Rohdaten raus aus `main` (Cache), `data`-Branch, Basis-QC, Zeitplan, Tests für Zeitlogik | läuft stündlich stabil, Seite zeigt rollende 48–72 h im *bisherigen* Layout | M |
| **1 Jetzt-Seite** | Kartenbetrachter (Reiter, Regler, Animation, URL-Zustand), Lage in Zahlen, Lage-Text ohne Ereignis, Veraltet-Banner. Kaltfront → `/ereignisse/` | neue Startseite | M–L |
| **2 Wind** | SVG-Windebene, neue Farbskala, Böen, Expositionsklassen aus DEM, Live-Werte aus `/sensors`, Föhn-Kennung, statische Vorschau neu | Wind deutlich besser, live | M |
| **3 Weitere Größen** | Δ24 h, Feuchte/Taupunkt, Summen 24/72 h/7 d, Drucktendenz, Schnee (Punkte), Sonne | 5–6 neue Karten | M |
| **4 Archiv & History** | `freeze_day`, Tageskarten 90 d, Stationsseiten, Backfill 2015+, Normalwerte und Anomaliekarten | Rückblick und Einordnung | M–L |
| **5 Optional** | Modell-Windfeld/Partikel, Hydrologie, externe Daten, automatische Ereigniserkennung (z. B. > 30 mm/24 h landesweit, Druckabfall > 8 hPa/12 h), zweisprachig DE/IT | Ausbau | je S–L |

Empfohlene Reihenfolge: 0 → 2 → 1 → 3 → 4. Wind vor dem Betrachter, weil die SVG-Ebene, die dabei
entsteht, die Grundlage für den Betrachter ist. Phase 0 sollte vor dem 25. Oktober (Zeitumstellung)
fertig sein oder zumindest den DST-Fehler beheben.

---

## 10. Risiken

| Risiko | Gegenmaßnahme |
|---|---|
| API ändert Adresse oder Format erneut | zentraler Client, Smoke-Test, Issue bei Fehler |
| GitHub-Cron verspätet oder fällt aus | Live-Schicht im Browser, Veraltet-Banner, zwei Läufe pro Stunde |
| Stationsausfälle verfälschen Felder | QC + Mindestanzahl Stationen je Karte, sonst Karte als lückenhaft markieren |
| Winterliche Messfehler (Vereisung, Schnee im Niederschlagsmesser) | QC-Regeln 2 und 4, Hinweis in der Legende |
| Pages-Größe wächst | rollendes Fenster, Archivkarten nur 90 d, alles aus Daten reproduzierbar |
| Modelldaten werden für Messung gehalten | getrennte Ebene, eigene Beschriftung, standardmäßig aus |
| Öffentliche Last auf der Landes-API durch Live-Abfragen | Abfrage höchstens alle 5 min und nur bei sichtbarem Tab. Bei vielen Besuchern stattdessen eine eigene `latest.json` per Actions (alle 10–30 min) |

---

## 11. Entscheidungen (23.10.2026)

- **Betrieb:** nur GitHub (Actions + Pages, Option B), Live-Werte im Browser. Kein Server, kein Objektspeicher.
- **Vergangenheit:** 72 h stündliche Karten, Tageskarten 90 Tage, Stationsverläufe.
  Backfill und Normalwerte vorerst nicht.
- **Wind:** nur Messung (Abschnitt 6.2, Punkte 1–7), kein Modellfeld.
- **Neue Größen:** alle vier Gruppen. Reihenfolge: Δ24 h + Tmin/Tmax → Feuchte/Taupunkt →
  Summen + Drucktendenz → Schnee + Sonne.

- **Einheit Wind:** m/s, wie bisher.
- **Sprache:** Deutsch und Italienisch (eigene Phase nach dem Betrachter; die Stationsnamen
  liefert die API in beiden Sprachen).
- **Ereignisse:** Kaltfront bleibt als erstes Ereignis. Dazu kommt eine automatische Erkennung,
  die Ereignisse vorschlägt (Phase 5, vorgezogen nach Phase 4).

## 12. Stand der Umsetzung

**Phase 0 (Fundament) erledigt:**
- Karten und Schlüssel nach UTC-Stunden benannt; Tests für die Zeitumstellung (23-, 24- und 25-Stunden-Tage).
- `scripts/meteo/`: API-Client mit wiederverwendeten Verbindungen (Abruf ≈ 20 s statt mehrerer Minuten),
  Zeitlogik, Plausibilitätsprüfung, Stundenaggregation. Neu abgerufen werden auch Schneehöhe,
  Sonnenscheindauer und Globalstrahlung.
- `fetch.py` holt inkrementell, Rohdaten liegen nur noch im Actions-Cache statt in `main`.
- `freeze_day.py` und `freeze.yml` archivieren jeden Tag auf dem Branch `data`.
- `update.yml` läuft zweimal pro Stunde, rendert nur neue Stunden und meldet Ausfälle als Issue.
- Startseite zeigt die letzten 72 Stunden mit Lage-Text und Kennzahlen, Hinweis bei veralteten Daten.
  Die Kaltfront liegt unter `/ereignisse/2026-10-kaltfront/`.

**Phase 2 (Wind) erledigt:**
- Interaktive Windkarte (`WindMap`): SVG-Pfeile über der Reliefkarte, Regler und Wiedergabe über das
  Fenster, Live-Modus mit den neuesten 10-Minuten-Werten direkt aus `/sensors` (alle 5 min, nur bei
  sichtbarem Tab), Tooltip mit Werten, Richtung in Worten und den drei Stunden davor, Filter nach Exposition.
- Farbskala in sechs Beaufort-Klassen (m/s), feste Pfeillänge, Böen-Etiketten ab 17,2 m/s.
- Exposition aus dem Geländemodell (Gipfel/Kamm, Hang, Tal), Gipfelpfeile mit offener Spitze.
- Föhnverdacht als Kennzeichen auf Karte, Tooltip und im Lage-Text.
- Statische Windkarten (Vorschaubilder, Ereignisse) im selben Stil.
- Offen aus 6.2: Windrose und 7-Tage-Verlauf je Station kommen mit den Stationsseiten (Phase 4).

Nächster Schritt: Phase 1 (Kartenbetrachter für alle Größen auf Basis der Windkarte).
