# Release Refactor Plan

## Zielbild
BotBoy wird als sauberer, installierbarer Release-Baum weitergebaut: gebuendelte Skills und Example-Skills sind out-of-the-box ladbar, Release-Smokes und Eval-Bundle sind reproduzierbar, die CLI ist schlank, und Gateway-/Orchestrator-Logik wird schrittweise in klar getrennte Module gezogen.

## Ownership
- n1: Tests, Pruefen, Evaluieren, Release-Smokes, Paritaet und Regressionsschutz.
- n2: Kommunikation, Vermittlung, Integrationsreihenfolge, Besitzgrenzen, laufende Planpflege.
- n3: Radikales effektives Bauen, grosse produktive Schnitte, Kernlogik verschieben.
- n4: Radikales effizientes Bauen, kleine risikoarme Extraktionen, saubere Kontrakte.
- n5: Radikales komplexes Bauen, Merge-/Task-/Gateway-Komplexitaet, mehrstufige Zerlegung.
- Lokaler Integrator: fasst Aenderungen zusammen, zieht Contracts nach, verifiziert Gesamtfluss und blockiert keine fremden Arbeiten.

## Aktuelle Architekturbaustellen
- `botboy/__main__.py` ist noch zu gross und traegt CLI, Bootstrap, Orchestrator und Spezialbefehle zusammen.
- `botboy/gateway/server.py` ist noch ein monolithischer Router mit Payload-, Review- und Auth-Surfaces in einer Datei.
- Release-Tests muessen als `botboy.release_smoke` und `tests/` sauber zusammenlaufen, ohne Dev-Workspace-Annahmen.
- Default-Skill-Aufloesung muss gebuendelte Assets bevorzugen und nur bei Bedarf auf externe Pfade fallen.
- Release-Metadaten muessen reproduzierbar bleiben und keine absoluten Entwicklerpfade enthalten.

## Integrationsreihenfolge
1. n1 haertet die Release-Smokes und Paritaetschecks.
2. n4 zieht zuerst risikoarme Extraktionen und Hilfsfunktionen heraus.
3. n3 schneidet danach grosse CLI-/Orchestrator-Bloecke aus `__main__.py`.
4. n5 zerlegt die Gateway-Routerpfade in klarere Teilmodule und fasst Merge-/Task-Surfaces zusammen.
5. Lokaler Integrator verbindet die Schnitte, passt Imports an und laeuft danach durch die komplette Verifikation.

## Fortschritt
- Erledigt: Release-Smoke und `tests/` laufen als source-tree-taugliche Release-Matrix ohne Dev-Workspace-Pfade.
- Erledigt: gemeinsame Task-/Worker-/Merge-Darstellung in `botboy/gateway/task_support.py` extrahiert und in FastAPI- wie stdlib-Gateway verdrahtet.
- Erledigt: Release-Snapshot wieder auf die kanonische Smoke-Semantik (`python -m botboy.release_smoke -v`) ausgerichtet.
- Offen: eigentliche Route-Registrierung in `botboy/gateway/server.py` weiter in Router-Module schneiden.
- Offen: `botboy/__main__.py` in Orchestrator-, Task- und Merge-Surfaces zerlegen.

## Offene Risiken
- Teilweise doppelte Verantwortung zwischen Release-Smoke, Eval-Bundle und Tests muss sauber abgegrenzt bleiben.
- Monolithische Dateien koennen beim Schnitt verdeckte Import- oder Initialisierungsreihenfolgen haben.
- Parallel laufende Agenten koennen an denselben Faehigkeiten arbeiten; deshalb gilt: keine Ruecknahmen, nur additive und klar begrenzte Aenderungen.
- Verpackung und Runtime muessen nach jedem grösseren Schnitt erneut verifiziert werden, bevor weitere Extraktionen starten.
