# Changelog

Format nach [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
Versionierung nach [Semantic Versioning](https://semver.org/lang/de/).

**Hinweis zur Vorgeschichte:** Dieses Changelog beginnt mit dem Aufräumen vom
18.10.2026. Für die Zeit davor gibt es keine gepflegte Änderungsliste — der Stand
war `0.6.0-dev` ohne Git-Tags. Was vorher geplant war, steht in `docs/archiv/`;
was tatsächlich geschah, zeigt `git log`. Es wird hier nicht rückwirkend erfunden.

## [Unreleased]

### Entfernt

- `botboy/bundled_assets/` — byte-identische Kopie von `botboy/bundled/`,
  131 Dateien. Nur `bundled` wurde je ausgeliefert (`package_data` in `setup.py`,
  `MANIFEST.in`). Der unerreichbare Rückfallzweig in
  `resources.bundled_assets_dir()` ist mit entfallen. (R2)
- `scratch_test.py` im Wurzelverzeichnis — ohne eigene Testfälle; es importierte
  `OrchestratorServiceIntegrationTests` und wurde durch das Namensmuster `*_test.py`
  ein zweites Mal eingesammelt, sodass drei Fälle pro Lauf doppelt liefen. (R3)
- `psutil` aus der Zusatzgruppe `standard` — keine Referenz im gesamten Repository.
- `docker` und `sentence-transformers` aus der Zusatzgruppe `full` — die
  Python-Pakete werden nirgends importiert. `security/sandbox.py` ruft das
  `docker`-Programm über die Kommandozeile auf und braucht das Paket nicht;
  `sentence_transformers` steht nur als Zeichenkette in
  `runtime.available_modules()`, das Importierbarkeit prüft und nichts verlangt. (R1)

### Geändert

- `requirements.txt` verweist jetzt auf die Zusatzgruppen in `setup.py` und
  installiert nur noch die Kernanforderung. Vorher listete die Datei 13 Pakete
  flach, als wären alle Pflicht; wer ihr folgte, installierte PyTorch und
  Audio-Systembibliotheken für Funktionen, die der Code nicht aufruft. Die
  Stufung in `setup.py` war bereits richtig. (R1)
- Jeder verbliebene Eintrag in `extras_require` nennt im Kommentar, warum er
  dort steht: Importstelle oder Zusicherung der Standardlaufzeit durch
  `release_acceptance` und `tests/test_release_cli_gateway.py`. (R1)
- Die zehn Planungsdokumente aus dem Wurzelverzeichnis liegen unter
  `docs/archiv/`, jedes mit Kopfhinweis „historisch, nicht mehr gültig". Im
  Wurzelverzeichnis gilt ab jetzt genau ein Architekturdokument. (R4)

### Hinzugefügt

- `ARCHITEKTUR.md` — gültiges Architektur- und Funktionskonzept vom 18.10.2026:
  gemessener Ist-Stand, Soll-Ist-Abgleich, acht Architekturentscheidungen,
  Zielarchitektur, Aufräumplan, Fahrplan mit Toren.
- `CHANGELOG.md` — diese Datei.
- `docs/backlog.md` — ein Rückstand statt zehn konkurrierender Listen. Teil A
  enthält die am Code gemessenen Vorhaben mit Abnahmekriterien, Teil B die
  mechanische Übernahme der 134 offenen Kästchen aus
  `docs/archiv/BOTBOY_V2_V7_ROADMAP.md`, ausdrücklich als ungepflegter Zustand
  gekennzeichnet. (R4)

### Noch offen und bewusst nicht in dieser Reihe

- `LICENSE` fehlt. Das Repository ist öffentlich; ohne Lizenz sind alle Rechte
  vorbehalten und niemand darf den Code nutzen oder weitergeben. Entscheidung
  steht aus (`docs/backlog.md`, V1).
- `SECURITY.md` und ein Meldeweg fehlen (V3).
- Die Git-Historie ist nicht auf Geheimnisse geprüft; der aktuelle Stand ist
  sauber (V2, M4).

### Prüfstand

- Suite vor dem Aufräumen: 395 bestanden, 2 übersprungen, 74 Subtests.
- Suite danach: 392 bestanden, 2 übersprungen, 74 Subtests. Die Differenz von
  drei Fällen ist genau die doppelte Einsammlung durch `scratch_test.py`.
- Anweisungsabdeckung zum Zeitpunkt der Messung: 64 % über 15.818 Anweisungen.
  `botboy/policy.py` liegt dabei bei 0 % — der Entscheidungspunkt, auf dem das
  Sicherheitsversprechen ruht, ist ungeprüft. Behebung ist F1 im Rückstand.
