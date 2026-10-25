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
scripts/freeze_day.py     abgeschlossene Tage -> Parquet auf dem Branch data
scripts/build_archive.py  Archiv -> Tageskarten (90 Tage), Stationsverläufe, web/src/data/archive.json
scripts/prepare_geo.py    Geländemodell + Landesgrenze -> data/geo/  (einmalig)
scripts/meteo/            API-Client, Zeitlogik, Plausibilitätsprüfung, Stundenaggregation
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
- **Archiv:** Der Branch `data` bekommt jeden Tag zwei unveränderliche
  Parquet-Dateien, `hourly/JJJJ/MM/JJJJ-MM-TT.parquet` (Station, Zeit, Größe,
  Wert, Zahl verworfener Rohwerte) und `daily/JJJJ/JJJJ-MM-TT.parquet`
  (Extreme, Summen, Mittel je Station), zusammen etwa 35 kB pro Tag, dazu
  `stations.json`. Lesen geht z. B. mit DuckDB:
  `SELECT * FROM 'hourly/2026/10/*.parquet' WHERE var = 'LT'`.
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
  nächste Lauf nach, was fehlt. Manuell: ein Tag (`date`) oder ein Zeitraum
  (`from`, `to`), mit einer Anfrage pro Station und Sensor je zehn Tage.

## Archiv und Stationen

`build_archive.py` liest den Branch `data` und baut daraus

- `/archiv/`: die letzten 90 Tage als Kalender, jede mit eigener Seite:
  Karten der höchsten und tiefsten Temperatur und der Niederschlagssumme,
  wärmste, kälteste, nasseste Station, stärkste Böe, meiste Sonne;
- `/stationen/`: alle Wetterstationen auf einer Karte und als Tabelle, jede mit
  eigener Seite: Verläufe der letzten 7 Tage (Archiv plus laufendes Fenster),
  Windrose, Tageswerte der letzten 30 Tage.

Ein angeklickter Stations-Tooltip im Kartenbetrachter verlinkt auf die Stationsseite.

## Live-Karte

Im Live-Modus (Standard beim Öffnen der Startseite) holt der Browser die neuesten
10-Minuten-Werte von der Schnittstelle des Landeswetterdienstes und rechnet die
Temperatur- und Feuchtefläche selbst (`web/src/lib/livefield.ts`, gezeichnet von
`livemap.ts`): Höhentrend mit Knick und Kriging der Reste wie in `fields.py`, auf dem
100-m-Raster der gerenderten Karten. Das Gelände lädt schon, während die Schnittstelle
antwortet; bis die Fläche steht, bleibt die gerenderte Karte der letzten Stunde stehen.
`render_maps.py --live` legt dafür Gelände (`geo.png`: Höhe in Rot und Grün,
Landesfläche in Blau), Umriss und Orte (`overlay.png`) und die Spanne der
Reliefschattierung (`live.json`) neben die Karten. `tests/test_live_parity.py` prüft,
dass Browser und Python dieselben Werte und dieselbe Schattierung liefern. Eine offene
Seite lädt die Live-Werte alle 5 Minuten und neue Karten alle 15 Minuten nach.

## Ereignisse

`detect_events.py` prüft jede Stunde des Archivs und des laufenden Fensters am ganzen
Messnetz: Temperatursturz (Median der Stationen ≤ −6 K gegenüber dem Vortag), Niederschlag
(≥ 20 mm in 24 h im Mittel), Sturm (Böen ≥ 20,8 m/s an ≥ 3 Stationen), Neuschnee
(≥ 20 cm in 24 h an ≥ 3 Stationen) und Hitze (≥ 32 °C an ≥ 15 Stationen). Markierte Stunden,
die weniger als einen Tag auseinanderliegen, bilden ein Ereignis (mindestens zwei Stunden).
Ergebnis in `web/src/data/detected.json`; die Seite `/ereignisse/` listet sie mit Kennzahlen
und Links auf Kartenbetrachter, Archivtage und ausgewählte Ereignisse, Archiv und Startseite
markieren sie. Die Schwellen sind auf die ersten 90 Archivtage abgestimmt.

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
