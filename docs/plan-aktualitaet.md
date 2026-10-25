# Plan: Aktuelle Karten ohne externen Taktgeber

> **Stand 25.10.2026: A, B, D und das Nachladen aus G sind umgesetzt** (Live-Temperatur und
> -Feuchte im Browser, erst 250 m, dann automatisch 100 m; Selbsttakt mit Auffang-Cron).
> Den Service Worker aus G gibt es nicht: GitHub Pages liefert die Dateien mit
> Revalidierung, ein zweiter Besuch lädt Gelände und Relief also ohnehin nur bei Änderungen
> neu, und ein Service Worker könnte veraltete Seiten festhalten.

Stand 25.10.2026. Ziel: Wer die Seite öffnet, sieht den Stand der letzten zehn Minuten, und der
Verlauf hat keine stundenlangen Lücken. Kein Server, kein fremder Dienst, keine Kosten.

## 1. Ausgangslage (gemessen)

- **Geplante Läufe kommen selten.** Eingestellt ist alle 30 Minuten (`17,47 * * * *`), tatsächlich
  gestartet sind die Läufe vom 24./25.10. um 04:40, 09:08, 13:21, 16:59, 19:30, 21:55, 00:20 und
  05:21 UTC, also alle 2½ bis 5 Stunden. GitHub nennt den Zeitplan ausdrücklich „best effort“.
  Unter Last werden Läufe verzögert oder verworfen. In einem veröffentlichten Vergleich lag die
  mittlere Verspätung bei 26 Minuten, die längste bei über 10 Stunden.
- **Ein Lauf ist kurz.** Abrufen, Rendern und Veröffentlichen dauern im Normalfall rund 3 Minuten.
  Nur wenn sich der Renderer ändert, sind es 12 Minuten.
- **Kosten:** Das Repository ist öffentlich. Actions auf Standard-Runnern und GitHub Pages sind
  damit kostenlos. Grenzen: 6 Stunden je Job; Pages hat weiche Grenzen von 100 GB Traffic im Monat
  und 1 GB Seitengröße. Die Grenze von 10 Builds pro Stunde gilt nicht für eigene
  Actions-Workflows.
- **Die API eignet sich für den Browser.** `/sensors` liefert den letzten Wert aller 993 Sensoren in
  einer Antwort von 180 kB, gzip-komprimiert 9,7 kB. Die Antwort trägt
  `Access-Control-Allow-Origin: *`. Temperatur kommt von 98 Stationen, Wind von 83.
- **Die Temperaturkarte ist leicht zu rechnen.** Sie besteht aus einem Höhentrend mit Knick plus
  Kriging der Reste auf einem 1-km-Gitter (≈ 170 × 110 Zellen) mit ≈ 95 Stationen. Das ist ein
  Gleichungssystem 96 × 96 und 18 000 Gitterpunkte. Im Browser dauert das wenige Millisekunden. Die
  Feinheit kommt allein über den Höhentrend aus dem Geländemodell.

## 2. Lösungswege

### A. Live-Karte im Browser – „direkt von GitHub Pages“ (empfohlen, Kern)

Pages liefert nur noch statische Zutaten:
- das Geländemodell als 16-Bit-PNG (250 m, 676 × 436, ≈ 300 kB; optional 100 m, ≈ 1,5 MB),
- Relief, Umriss und Ortsnamen als durchsichtige Ebene,
- Stationsliste mit Koordinaten und Höhe,
- Farbskalen.

Der Browser holt beim Öffnen und danach alle 10 Minuten `/sensors` und rechnet die Karte selbst:
Höhentrend → Kriging der Reste → auf das Gelände hochrechnen → Farbtabelle → Canvas, Relief darüber.

- **Größen:**
  - Temperatur und Taupunkt/Feuchte: echte Live-Fläche.
  - Wind: Pfeile, wie heute.
  - Schnee und Sonne: Punkte.
  - Niederschlag: `/sensors` liefert nur den letzten 10-Minuten-Wert. Live gibt es dafür nur eine
    Punktkarte „regnet gerade“; die Stundensummen bleiben bei den gerenderten Karten.
- **Gleiches Verfahren wie in Python.** Ein Test rechnet dieselbe Stunde in Python und
  TypeScript und verlangt weniger als 0,2 K Abweichung.
- **Kosten:** 0 €. Last auf der API: eine kleine Anfrage je Besuch und je 10 Minuten.
- **Grenzen:**
  - Nur der Jetzt-Zustand wird live. Der Verlauf der letzten 72 Stunden kommt weiter aus den Läufen.
  - Auf alten Handys dauert das Hochrechnen auf 100 m spürbar; 250 m reichen dort.

### B. Selbsttakt in GitHub Actions (empfohlen, für Verlauf und Archiv)

Der Workflow startet sich am Ende selbst neu, statt auf den Zeitplan zu warten. Das letzte Job
wartet bis zum nächsten Halbstunden-Takt und löst dann mit dem eingebauten `GITHUB_TOKEN`
`workflow_dispatch` aus. Laut Dokumentation ist das die ausdrückliche Ausnahme: Solche Ereignisse
erzeugen immer einen neuen Lauf.

- **Läuft weiter, wenn Läufe ausfallen.** `concurrency` verhindert doppelte Ketten. Der bestehende
  Zeitplan bleibt als Auffangnetz: Reißt die Kette, startet sie der nächste geplante Lauf neu.
- **Kosten:** 0 € bei öffentlichem Repository. Etwa 3 Minuten Arbeit und bis zu 27 Minuten Warten
  je Takt.
- **Risiken:**
  - Wartende Runner sind nicht verboten, aber auch nicht der gedachte Zweck. Bei Missbrauch könnte
    GitHub Actions für das Repository drosseln; mildern lässt sich das mit einem moderaten Takt von
    30 Minuten und ohne Endlosschleife in einem Job.
  - Die Dokumentation und einzelne Berichte widersprechen sich darin, ob der eingebaute Token
    wirklich startet. Deshalb zuerst ausprobieren; falls nicht, genügt ein feingranularer Token mit
    nur `actions: write` als Secret.
- **Variante B2:** Ein Job läuft bis zu 5 h 50 min in einer Schleife und rechnet alle 30 Minuten.
  Veröffentlicht wird dann über die Pages-API per `curl` statt über `deploy-pages`. Das sind
  weniger Starts, aber es ist aufwendiger und unübersichtlicher in den Logs. Nicht empfohlen.

### C. Besucher stößt den Lauf an – verworfen

Die Seite merkt, dass die Karten älter als 45 Minuten sind, und ruft `repository_dispatch` auf.
Dafür müsste ein Token im öffentlichen JavaScript stehen. Jeder könnte damit Läufe auslösen, und
GitHubs Secret-Scanning widerruft solche Tokens. Ohne eigenen Vermittler geht es nicht, und ein
Vermittler wäre ein externer Dienst.

### D. Zeitplan verfeinern – nur als Ergänzung

Mehr Cron-Zeilen zu ungeraden Minuten wie `7,23,37,53` erhöhen die Trefferzahl etwas, heben das
„best effort“ aber nicht auf. Sinnvoll als Auffangnetz zu B.

### E. Andere Hoster mit eigenem Takt – zur Vollständigkeit

Diese Wege brächten einen externen Dienst ins Spiel und sind deshalb nicht empfohlen:
- Cloudflare Workers Cron, kostenlos und sekundengenau,
- Netlify Scheduled Functions,
- Vercel Cron (im Gratis-Tarif nur täglich),
- cron-job.org.

### F. Modelldaten im Browser (z. B. Open-Meteo) – verworfen

Modelldaten sind keine Messwerte; laut Entscheidung bleibt es bei Messungen. Die Gratis-API von
Open-Meteo ist zudem nur nichtkommerziell nutzbar.

### G. Kleinigkeiten im Browser

- Eine offene Seite lädt alle 10 Minuten die Live-Werte neu und alle 30 Minuten die Kartenliste.
  Heute muss man dafür neu laden.
- Ein Service Worker hält Gelände und Relief im Cache; der zweite Besuch lädt dann nur 10 kB.
- Periodic Background Sync funktioniert nur in installierten Chromium-Apps und bringt keinen Nutzen.

## 3. Empfehlung

**A + B + D + G, alles kostenlos:**

| Baustein | Wirkung | Aufwand |
|---|---|---|
| A Live-Temperatur und -Feuchte im Browser | Jetzt-Karte immer 10 Minuten frisch, unabhängig von Actions | M |
| B Selbsttakt | Verlauf und gerenderte Karten etwa alle 30 Minuten statt alle 3–5 Stunden | S |
| D Zusätzliche Cron-Zeilen | Auffangnetz, falls die Kette reißt | XS |
| G Automatisches Nachladen, Service Worker | Offene Seiten bleiben aktuell, weniger Traffic | S |

## 4. Umsetzung in Schritten

1. **B + D:**
   - In `update.yml` ein Job `next`: wartet bis zum nächsten Takt und ruft dann
     `gh workflow run update.yml`. Er läuft nur, wenn der Lauf aus dem Zeitplan oder der Kette
     kommt.
   - Weitere Cron-Zeilen hinzufügen.
   - Prüfen, ob der eingebaute Token den Folgelauf wirklich startet.
   - Ein Tag Beobachtung.
2. **A, Grundlage:**
   - `prepare_geo.py` exportiert DEM-PNG, Reliefebene und Stationsliste.
   - TypeScript-Modul `live-field.ts` mit Höhentrend, Kriging und Hochrechnung.
   - Paritätstest gegen Python.
3. **A, Einbau:**
   - Der Live-Modus im Betrachter zeigt die gerechnete Fläche statt der Fläche der letzten vollen
     Stunde.
   - Tooltip mit dem interpolierten Wert an jeder Stelle.
   - Fallback auf das Bild, wenn die API nicht antwortet.
4. **G:** Nachladen der offenen Seite, Service Worker für die statischen Ebenen.
5. **Dokumentation und Beobachtung:** Abstände der Läufe in einer kleinen Statistik auf der
   Methodenseite.

## 5. Risiken

- **API nicht erreichbar oder CORS geändert:** Die Seite fällt auf die gerenderten Karten zurück,
  wie heute.
- **Python- und Browser-Karte unterscheiden sich sichtbar:** Der Paritätstest verhindert das.
  Gleiche Farbtabelle, gleiche Variogrammparameter.
- **GitHub drosselt den Selbsttakt:** Dann zurück zum reinen Zeitplan. Die Live-Karte aus A wirkt
  trotzdem weiter. Deshalb ist A der Kern und B nur Verbesserung.

## 6. Offene Fragen

1. Ist der Selbsttakt mit Wartezeit (B) in Ordnung, oder lieber nur A und D?
2. Live-Karte in 250 m (schnell, auch auf Handys) oder 100 m (wie die gerenderten Karten)?
3. Soll der Betrachter beim Öffnen die Live-Karte zeigen oder wie heute die letzte volle Stunde?

## Quellen

- [GitHub Docs: GITHUB_TOKEN](https://docs.github.com/en/actions/concepts/security/github_token)
  (Ausnahme für `workflow_dispatch` und `repository_dispatch`)
- [GitHub Docs: Limits für Actions](https://docs.github.com/en/actions/reference/actions-limits)
  (6 Stunden je Job)
- [GitHub Docs: Pages-Limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)
- [Vergleich der Cron-Verspätung GitHub gegen Cloudflare](https://josephhall.org/blog/small-bake-off/)
- [Hinweis „best effort“ beim Zeitplan](https://github.com/tamucsa/points/issues/34)
- [Open-Meteo-Nutzungsbedingungen](https://open-meteo.com/en/terms)
- [kriging.js (Ordinary Kriging im Browser)](https://www.npmjs.com/package/kriging-es6) – als
  Referenz. Eine eigene Umsetzung ist kleiner, weil das Variogramm fest ist.
