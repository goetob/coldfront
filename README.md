# coldfront

Wetterkarten für Südtirol aus den Messstationen des Landeswetterdienstes:
Temperatur, Niederschlag und Wind, Stunde für Stunde. Die Startseite zeigt
laufend die letzten 72 Stunden; einzelne Wetterlagen bleiben als Ereignisse
erhalten, zuerst die Kaltfront vom 20. bis 22. Oktober 2026.
Wie es weitergeht, steht in [`docs/plan-echtzeit.md`](docs/plan-echtzeit.md).

## Ablauf

```
scripts/fetch.py          rollendes Fenster der Rohdaten -> data/raw/  (inkrementell, nicht in Git)
scripts/build_hourly.py   Prüfung + Stundenwerte -> data/processed/hourly.json
scripts/render_maps.py    Interpolation + Karten -> web/public/maps/<set>/, web/src/data/…
scripts/freeze_day.py     abgeschlossene Tage und Monate -> Parquet auf dem Branch data
scripts/build_normals.py  Archiv -> Normalwerte je Station und Kalendertag (normals/ auf data)
scripts/build_archive.py  Archiv -> Tageskarten (90 Tage), Anomalien, Stationsverläufe, web/src/data/archive.json
scripts/prepare_geo.py    Geländemodell + Landesgrenze -> data/geo/  (einmalig)
scripts/meteo/            API-Client, Zeitlogik, Plausibilitätsprüfung, Stundenaggregation,
                          Archivlayout (archive.py), Normalwerte und Einordnungen (climate.py)
data/events/<slug>/       eingefrorene Stundenwerte eines Ereignisses
web/                      Astro-Seite, gebaut nach web/dist/
```

Kartennamen und Schlüssel sind UTC-Stunden (`20261025T01Z`), damit die
doppelte Stunde bei der Umstellung auf Winterzeit zwei Karten bekommt.

Lokal:

```sh
python -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest tests
.venv/bin/python scripts/fetch.py
.venv/bin/python scripts/build_hourly.py --hours 72
.venv/bin/python scripts/render_maps.py --hours 72
.venv/bin/python scripts/render_maps.py --hourly data/events/2026-10-kaltfront/hourly.json \
    --start 2026-10-20T12 --out web/public/maps/2026-10-kaltfront \
    --meta web/src/data/events/2026-10-kaltfront.json
cd web && npm ci && BASE=/ npx astro dev
```

`prepare_geo.py` braucht die sechs Copernicus-DEM-Kacheln N46/N47 × E010–E012
(`https://copernicus-dem-30m.s3.amazonaws.com/`) und die Provinzgrenzen aus
`openpolis/geojson-italy`; das Ergebnis liegt bereits in `data/geo/`.

## Daten

Die API liefert 10-Minuten-Werte bis mindestens 2015 zurück; sie ist das
Archiv der Rohdaten. Gespeichert wird nur, was wir daraus machen:

- **Rollendes Fenster:** Rohdaten der letzten 96 Stunden und die Karten liegen
  im Actions-Cache. Geht er verloren, holt der nächste Lauf das Fenster neu.
- **Archiv:** Der Branch `data` hält die Stunden- und Tageswerte seit Januar 2015,
  zusammen etwa 14 MB pro Jahr:
  - `hourly/JJJJ/MM/JJJJ-MM-TT.parquet` (Station, Zeit, Größe, Wert, Zahl verworfener
    Rohwerte) und `daily/JJJJ/JJJJ-MM-TT.parquet` (Extreme, Summen, Mittel je Station):
    ein Tag, so wie der tägliche Lauf ihn schreibt;
  - `hourly/JJJJ-MM.parquet` und `daily/JJJJ-MM.parquet`: ein ganzer Monat. Ist ein Monat
    vorbei, fasst der tägliche Lauf seine Tagesdateien darin zusammen; das Nachladen
    schreibt gleich Monatsdateien. Gibt es für einen Tag beide, gilt die Tagesdatei.
    Bei einem Monat Tageswerten ist die Monatsdatei 7× kleiner und 10× schneller gelesen
    (29 statt 205 kB), bei den Stundenwerten 3× schneller;
  - `stations.json`, `normals/` (Normalwerte) und `events.json` (Ereignisse des ganzen Archivs).

  Gelesen wird über `scripts/meteo/archive.py`; mit DuckDB z. B.
  `SELECT * FROM 'hourly/**/*.parquet' WHERE var = 'LT'` (dabei Tage, die in beiden
  Formen vorliegen, nur einmal zählen). Stationen, die es heute nicht mehr gibt, fehlen:
  Die API nennt nur die aktuellen.
- **Normalwerte:** je Station und Kalendertag aus allen Tagen ±15 Tage in den
  abgeschlossenen Jahren (jetzt 2015–2025): Mittel, Tiefst- und Höchstwert der Temperatur
  mit 10. und 90. Perzentil, Häufigkeit von Tagen ab 1 mm, mittlerer Niederschlag und
  90. Perzentil der nassen Tage. Eine Station braucht 5 Jahre mit Messungen, unter 8 gelten
  ihre Normalwerte als unsicher. Die Datei ändert sich nur, wenn ein Jahr voll ist.
- **Ereignisse:** `data/events/<slug>/hourly.json` und `event.json` liegen in
  `main`; die Karten werden beim Bau daraus gerendert.

## Workflows

- `update.yml` läuft zweimal pro Stunde (`:17`, `:47`): holt neue Messwerte,
  rechnet die letzten drei Stunden neu (späte Meldungen), rendert Ereignisse
  nur bei Änderungen, baut die Seite und veröffentlicht sie auf GitHub Pages.
  Weil GitHub geplante Läufe hier nur alle paar Stunden startet, stößt jeder
  Lauf den nächsten selbst an (Job `next`: wartet bis `:17` oder `:47`, dann
  `workflow_dispatch`); der Zeitplan startet die Kette nur neu, wenn sie
  gerissen ist. Anhalten: laufenden Workflow abbrechen oder die
  Repository-Variable `SELF_TIMER` auf `off` setzen. Schlagen Läufe fehl,
  öffnet er ein Issue und schließt es beim nächsten Erfolg.
- `freeze.yml` archiviert täglich um 01:23 UTC auf `data`, und zwar alle Tage
  seit dem neuesten archivierten bis gestern (höchstens 30). Weil GitHub
  geplante Läufe hier oft stundenlang verzögert oder auslässt, holt so der
  nächste Lauf nach, was fehlt. Danach fasst er beendete Monate zusammen und rechnet
  Normalwerte und Ereignisse über das ganze Archiv neu. Manuell: ein Tag (`date`) oder
  das Nachladen ab `from` (bis `to` oder gestern): Monat für Monat, vom neuesten zum
  ältesten, eine Anfrage pro Station, Sensor und Monat, Push alle sechs Monate. Nach
  `budget` Minuten (Standard 270) beginnt der Job keinen Monat mehr; mit `chain` startet er
  dann den nächsten Job. Was schon da ist, wird übersprungen, ein Abbruch kostet also
  höchstens die Monate seit dem letzten Push. 2015 bis Juli 2026 dauern rund 5–7 Stunden.

## Archiv und Stationen

`build_archive.py` liest den Branch `data` und baut daraus

- `/archiv/`: die letzten 90 Tage als Kalender, jede mit eigener Seite:
  Karten der höchsten und tiefsten Temperatur, der Niederschlagssumme und des
  Tagesmittels gegenüber normal, wärmste, kälteste, nasseste Station, stärkste Böe,
  meiste Sonne; Abweichung im Landesmittel und Einordnung gegenüber allen Jahren;
- `/stationen/`: alle Wetterstationen auf einer Karte und als Tabelle (mit den Jahren
  hinter ihren Normalwerten), jede mit eigener Seite: Verläufe der letzten 7 Tage
  (Archiv plus laufendes Fenster), Windrose, Tageswerte der letzten 30 Tage mit
  Normalband, Niederschlag gegenüber normal und bemerkenswerte Tage.

Der Kartenlauf holt vom Branch `data` nur Tageswerte, Normalwerte, Ereignisse und die
Stundenwerte der letzten Monate (Sparse-Checkout, etwa 1 s) und bleibt so gleich schnell,
wie lang das Archiv auch wird.

**Anomalien und Einordnung** (`scripts/meteo/climate.py`):
- Anomaliekarte: Tagesmittel jeder Station minus ihr normales Tagesmittel, ohne
  Höhentrend interpoliert (der steckt schon im Normalwert). Für heute zeigt der
  Kartenbetrachter unter Temperatur „Gegenüber normal“: das Mittel der letzten
  24 Stunden je Station gegen ihr normales Tagesmittel, Stunde für Stunde. 24 Stunden
  enthalten immer einen ganzen Tagesgang und sind so jederzeit vergleichbar.
- Landeswerte mitteln über die Stationen mit Normalwerten. Die absolute Landestemperatur
  ist deren mittlerer Normalwert plus ihre mittlere Abweichung, damit Jahre mit weniger
  Stationen nicht wärmer oder kälter erscheinen.
- „wärmster 25. Oktober seit 2015“: Rang des Tagesmittels unter demselben Kalendertag
  aller Jahre (genannt werden die drei wärmsten und kältesten, ab 5 Jahren).
  „wärmster/kältester/nassester Tag seit …“: der letzte frühere Tag mit mindestens so
  hohem Höchstwert (bzw. so tiefem Tiefstwert, so viel Niederschlag), wenn er mehr als
  45 Tage zurückliegt; Niederschlag ab 3 mm im Landesmittel, 10 mm an einer Station.

Ein angeklickter Stations-Tooltip im Kartenbetrachter verlinkt auf die Stationsseite.

## Live-Karte

Im Live-Modus (Standard beim Öffnen der Startseite) holt der Browser die neuesten
10-Minuten-Werte von der Schnittstelle des Landeswetterdienstes und rechnet die
Temperatur-, Feuchte- und Niederschlagsfläche selbst (`web/src/lib/livefield.ts`,
gezeichnet von `livemap.ts`): Höhentrend mit Knick und Kriging der Reste wie in
`fields.py`, beim Niederschlag Kriging allein; auf dem 100-m-Raster der gerenderten
Karten. Live-Niederschlag ist die Intensität des letzten 10-Minuten-Werts (× 6, mm/h). Das Gelände lädt schon, während die Schnittstelle
antwortet; bis die Fläche steht, bleibt die gerenderte Karte der letzten Stunde stehen.
`render_maps.py --live` legt dafür Gelände (`geo.png`: Höhe in Rot und Grün,
Landesfläche in Blau), Umriss und Orte (`overlay.png`) und die Spanne der
Reliefschattierung (`live.json`) neben die Karten. `tests/test_live_parity.py` prüft,
dass Browser und Python dieselben Werte und dieselbe Schattierung liefern. Eine offene
Seite lädt die Live-Werte alle 5 Minuten und neue Karten alle 15 Minuten nach.

## Ereignisse

`detect_events.py` prüft jede Stunde des Archivs und des laufenden Fensters am ganzen
Messnetz:
- Temperatursturz: Das Mittel der letzten 24 Stunden liegt im Median der Stationen ≥ 5 K
  unter dem der 24 Stunden davor.
- Niederschlag: ≥ 20 mm in 24 h im Mittel der Stationen.
- Sturm: Böen über dem 99,5. Perzentil der eigenen Station (mindestens 17,2 m/s) an
  ≥ 5 Stationen; Stationen mit weniger als einem Jahr Messungen zählen ab 20,8 m/s.
- Neuschnee: ≥ 30 cm in 24 h an ≥ 3 Stationen.
- Hitze: ≥ 32 °C an ≥ 15 Stationen.

Markierte Stunden, die weniger als einen Tag auseinanderliegen, bilden ein Ereignis
(mindestens zwei Stunden). Über das ganze Archiv läuft die Erkennung einmal täglich in
`freeze.yml` (`events.json` auf `data`, mit den Böen-Perzentilen je Station); der
Kartenlauf durchsucht nur die letzten 120 Tage und übernimmt die älteren Ereignisse.
Ergebnis in `web/src/data/detected.json`; die Seite `/ereignisse/` listet sie mit Kennzahlen
und Links auf Kartenbetrachter, Archivtage und ausgewählte Ereignisse, Archiv und Startseite
markieren sie.

Die ersten Schwellen (Böen ≥ 20,8 m/s an 3 Stationen, Stundenwert 6 K unter dem Vortag,
20 cm Neuschnee) waren auf Juli bis Oktober 2026 abgestimmt. Über 2015–2025 schlugen sie etwa
22-mal (Sturm), 16-mal (Temperatursturz) und 12-mal (Neuschnee) im Jahr an: Die Gipfelstationen
über 2800 m haben solche Böen an den meisten Wintertagen, ein Gewitter kühlt einzelne Nachmittage
ebenso stark ab, und 20 cm fallen an drei hohen Stationen in fast jeder Schneewoche. Mit den
jetzigen Regeln sind es im Jahr etwa 7 Stürme, 8 Temperaturstürze, 6 Neuschneelagen,
10 Starkniederschläge und 2 Hitzewellen, zusammen rund 22 Ereignisse (268 von 2015 bis Oktober
2026), darunter alle der ersten 90 Archivtage und Vaia im Oktober 2018.

## Sprachen

Die Seite ist zweisprachig: Deutsch an der Wurzel, Italienisch unter `/it/`
(`/it/eventi/`, `/it/archivio/`, `/it/stazioni/`). Jede Seite verlinkt oben rechts
auf ihre Fassung in der anderen Sprache und nennt beide per `hreflang`.

- Routen und kleine Helfer in `web/src/lib/i18n.ts`; die Seiten selbst liegen als
  Ansichten mit `lang`-Eigenschaft in `web/src/views/`, die Dateien unter
  `web/src/pages/` und `web/src/pages/it/` reichen nur die Sprache durch.
- Texte des Kartenbetrachters im Wörterbuch in `web/src/lib/viewer.ts` und `MapViewer.astro`.
- Stationsnamen liefert die API in beiden Sprachen (`name_it`); die Karten selbst
  tragen beide Ortsnamen (BOZEN / Bolzano) und gelten so für beide Fassungen.
- Ereignisse: `title_it` und `summary_it` in `event.json`.

## Methode

- **Temperatur:** pro Stunde Höhentrend aus den Stationen (lineare Regression,
  bei Inversionen mit Knick), übertragen auf das 100-m-Geländemodell, plus
  Ordinary Kriging der Residuen (exponentielles Variogramm, 30 km Reichweite).
- **Niederschlag:** Ordinary Kriging ohne Höhenbezug, Stundensummen und Summe
  über den Zeitraum. Unter 0,2 mm/h bleibt die Karte leer.
- **Wind:** keine Interpolation; Pfeile gleicher Länge an den Stationen,
  vektorielles Stundenmittel, Stärke als Farbe und Strichstärke in
  Beaufort-Klassen (`scripts/meteo/wind.py`). Stationen werden aus dem
  Geländemodell als Gipfel/Kamm, Hang oder Tal eingeordnet; Gipfelpfeile haben
  eine offene Spitze. Böen ab 17 m/s stehen als Zahl daneben, „F“ markiert
  Föhnverdacht (Nordwind, Böen, trockene Luft). Auf der Seite ist Wind eine
  SVG-Ebene über `base.webp`.

## Kartenbetrachter

Startseite und Ereignisse haben einen Betrachter (`web/src/lib/viewer.ts`) mit
einem Regler über alle Stunden, Wiedergabe, Pfeiltasten und Tooltips an den
Stationen. Reiter und Ebenen:

| Reiter | Ebenen | Karte |
|---|---|---|
| Temperatur | Stunde · Änderung in 24 h · Tiefst- und Höchstwert des Tages | Fläche |
| Niederschlag | Stunde · 24 Stunden (gleitend) · ganzer Zeitraum | Fläche |
| Feuchte | relative Feuchte (Taupunkt mit Höhentrend, mit dem Temperaturfeld verrechnet) | Fläche |
| Luftdruck | Änderung in 3 Stunden | Fläche |
| Wind | Pfeile, Böen, Föhnverdacht | nur Stationen |
| Schnee | Schneehöhe, Änderung in 24 h im Tooltip | nur Stationen |
| Sonne | Sonnenschein in der Stunde, Globalstrahlung im Tooltip | nur Stationen |

Die 24-Stunden-Größen brauchen einen Tag Vorlauf; deshalb baut der Workflow
96 Stunden Stundenwerte und zeigt 72. Gespeist wird der Betrachter aus
`maps/<set>/viewer.json` (Stationswerte je Stunde und Tag) und den Kartenbildern.
„Live“ holt die neuesten 10-Minuten-Werte für Temperatur und Wind im Browser
direkt von der API. Der Zustand steht in der URL, z. B.
`?v=wind&t=20261022T09Z#karte`; die Vorschaubilder darunter öffnen ihre Stunde
im Betrachter.
- **Prüfung:** Rohwerte außerhalb physikalischer Grenzen, einzelne Ausreißer
  und über viele Stunden eingefrorene Werte fallen heraus (`scripts/meteo/qc.py`).
  Leicht negative Schneehöhen auf aperem Boden gelten als 0 cm.

## Daten und Lizenzen

- Messdaten: Landeswetterdienst Südtirol, Open Data (CC0),
  `https://geoservices.buergernetz.bz.it/services/meteo/v1/`.
  Die frühere Adresse `daten.buergernetz.bz.it/services/meteo/v1` ist abgeschaltet.
- Geländemodell: Copernicus DEM GLO-30, © DLR e.V. 2010–2014 und © Airbus
  Defence and Space GmbH 2014–2018, bereitgestellt im Rahmen des Copernicus-Programms.
- Landesgrenze: ISTAT über openpolis/geojson-italy, CC BY 4.0.
- Schriften: Newsreader und IBM Plex Sans Condensed, SIL Open Font License (`assets/fonts/`).
