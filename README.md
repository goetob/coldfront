# coldfront

Wetterkarten für Südtirol aus den Messstationen des Landeswetterdienstes:
Temperatur, Niederschlag und Wind, Stunde für Stunde, als statische Atlasseite.
Anlass ist die Kaltfront vom 20. bis 22. Oktober 2026.

## Ablauf

```
scripts/fetch_meteo.py    Rohdaten der Meteo-API -> data/raw/          (GitHub Actions, manuell)
scripts/prepare_geo.py    Geländemodell + Landesgrenze -> data/geo/     (einmalig)
scripts/build_hourly.py   5/10-Minuten-Werte -> Stundenwerte -> data/processed/hourly.json
scripts/render_maps.py    Interpolation + Karten -> web/public/maps/, web/src/data/event.json
web/                      Astro-Seite, gebaut nach web/dist/
```

Lokal:

```sh
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/fetch_meteo.py --hours 72
.venv/bin/python scripts/build_hourly.py
.venv/bin/python scripts/render_maps.py --start 2026-10-20T12
cd web && npm ci && BASE=/ npx astro dev
```

`prepare_geo.py` braucht die sechs Copernicus-DEM-Kacheln N46/N47 × E010–E012
(`https://copernicus-dem-30m.s3.amazonaws.com/`) und die Provinzgrenzen aus
`openpolis/geojson-italy`; das Ergebnis liegt bereits in `data/geo/`.

## Workflows

- `fetch-meteo.yml` holt Messdaten und committet sie nach `claude/meteo-data`.
  Während der Kaltfront lief er stündlich; jetzt wird er nur noch manuell
  gestartet (Actions → Fetch meteo data → Run workflow). Die API liefert
  Daten rückwirkend, mit `hours` lässt sich also nachholen.
- `site.yml` baut nach jedem Abruf Karten und Seite und veröffentlicht sie auf
  GitHub Pages. Das gezeigte Fenster steht in `EVENT_START`.

## Methode

- **Temperatur:** pro Stunde Höhentrend aus den Stationen (lineare Regression,
  bei Inversionen mit Knick), übertragen auf das 100-m-Geländemodell, plus
  Ordinary Kriging der Residuen (exponentielles Variogramm, 30 km Reichweite).
- **Niederschlag:** Ordinary Kriging ohne Höhenbezug, Stundensummen und Summe
  über den Zeitraum. Unter 0,2 mm/h bleibt die Karte leer.
- **Wind:** keine Interpolation; Pfeile an den Stationen, vektorielles
  Stundenmittel. Die Pfeillänge wächst mit der Wurzel der Geschwindigkeit.

## Daten und Lizenzen

- Messdaten: Landeswetterdienst Südtirol, Open Data (CC0),
  `https://geoservices.buergernetz.bz.it/services/meteo/v1/`.
  Die frühere Adresse `daten.buergernetz.bz.it/services/meteo/v1` ist abgeschaltet.
- Geländemodell: Copernicus DEM GLO-30, © DLR e.V. 2010–2014 und © Airbus
  Defence and Space GmbH 2014–2018, bereitgestellt im Rahmen des Copernicus-Programms.
- Landesgrenze: ISTAT über openpolis/geojson-italy, CC BY 4.0.
- Schriften: Newsreader und IBM Plex Sans Condensed, SIL Open Font License (`assets/fonts/`).
