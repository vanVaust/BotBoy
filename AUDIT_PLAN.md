# BotBoy – Code-Audit und Fortschrittsregister

Stand: 2026-09-28. Dieses Dokument protokolliert Analyseschritte, nicht den Sicherheitsstatus des Produkts. Keine Codeänderung wird hierdurch autorisiert.

## Unveränderliche Untersuchungsbasis
- main: ae510a31afc7d2c96d244cbafa382ae2c8dab59f
- security/v5.1-foundation: 6c6ef4f136c78692bd126cc1ebee452576f79a64
- Offener PR #1: https://github.com/vanVaust/BotBoy/pull/1 (60 geänderte Dateien, 182 Commits laut PR-Metadaten bei Bestandsaufnahme). Der PR-Text kann gegenüber dem aktuellen Diff veraltet sein.
- CI am PR-Head: release-verification erfolgreich; das beweist nicht, dass alle Audit-Kriterien erfüllt sind.

## Statusregeln
OFFEN = noch nicht begonnen; IN ARBEIT = geprüft, aber Abnahmekriterium offen; ABGESCHLOSSEN = Nachweis und Abnahmekriterium dokumentiert; BLOCKIERT = konkretes Hindernis dokumentiert. Jede Änderung eines Status braucht Datum, Commit, Prüfmethode, Ergebnis und Verweis. Dateibestand, Codelektüre, ausgeführte Tests und Release-Freigabe sind strikt getrennte Nachweise.

## Arbeitsphasen und Gates
| ID | Phase | Abnahmekriterium | Status |
|---|---|---|---|
| A0 | Untersuchungsbasis und PR-Kontext | Beide Branch-Head-SHAs und offener PR festgehalten | ABGESCHLOSSEN |
| A1 | Vollständiges Dateiinventar | Jeder versionierte Pfad beider SHAs rekursiv erfasst und klassifiziert; inklusive Skills, Assets, Daten, Beispiele, Tests und Workflows | IN ARBEIT |
| A2 | Branch-Delta | Neue, entfernte und geänderte Pfade vollständig abgeglichen; PR-Diff auf aktuelle Basis bezogen | IN ARBEIT |
| A3 | Dokumentation und Build | Soll-Ist-Matrix, Abhängigkeiten, Installations- und Startpfade geprüft | OFFEN |
| A4 | Architektur und Schnittstellen | CLI, FastAPI, stdlib-Gateway, WebSocket, MCP, UI und interne Aufrufpfade mit Datenflüssen kartiert | OFFEN |
| A5 | Task- und Ausführungskern | Zustandswechsel, Persistenz, Migrationen, Queue-Leases, Worker, Handoff, Merge, Recovery und Konkurrenzfälle geprüft | OFFEN |
| A6 | Sicherheit und Autorisierung | Identität, Mandant, Objekt, Capability, Approval, finaler Effekt, Secrets, Artefakte und Sandbox entlang aller Eintrittspunkte geprüft | IN ARBEIT |
| A7 | Agentik und Produktflächen | LLM, Memory, Skills, Evaluationen, UI, Monitoring und Betriebsmodi geprüft | OFFEN |
| A8 | Tests und Release | Testmatrix erstellt; Builds, Tests und Security-/Release-Gates tatsächlich ausgeführt oder mit Grund als nicht ausführbar markiert | IN ARBEIT |
| A9 | Befunde und Maßnahmen | Jeder Befund mit Branch, Pfad, Reproduktion, Auswirkung, Schweregrad, Gegenmaßnahme und Regressionstest erfasst | IN ARBEIT |

## Protokoll
| Datum | ID | Nachweis | Ergebnis / Restarbeit |
|---|---|---|---|
| 2026-09-28 | A0 | GitHub-Branchliste und PR #1 | Zwei Branch-Spitzen fixiert; PR offen. Scope-Baseline abgeschlossen. |
| 2026-09-28 | A1 | GitHub-Verzeichnislisten von Root, botboy, gateway und tests; weitere Verzeichnisse zuvor teilweise gelistet | Noch kein vollständiger rekursiver Dateibaum; keine Behauptung vollständiger Codelektüre. |
| 2026-09-28 | A2 | PR #1, get_files und Commit-Diff | 60 Dateien im PR; inhaltliche Review und vollständiger SHA-gegen-SHA-Abgleich noch offen. |
| 2026-09-28 | A8 | PR #1 Check Runs | release-verification am Head erfolgreich; keine lokalen Tests durch dieses Audit ausgeführt. |
| 2026-09-28 | A1/A2 | GitHub-Rootlisten beider fixierter Branches; PR #1 get/get_files | Root auf security enthält zusätzlich docs/, auf main AUDIT_PLAN.md; PR meldet 60 geänderte Dateien gegenüber Basis 91af7c0, nicht gegenüber aktuellem main 7aeb610; PR-Mergeability dirty. Rekursives Inventar und aktueller SHA-gegen-SHA-Abgleich fehlen. |
| 2026-09-28 | A6/A9 | PR #1 get_files: botboy/gateway/routes_auth.py und botboy/gateway/auth.py | Statischer Befund F-001: Login setzt AuthPrincipal(metadata={org_id: ...}), nicht AuthPrincipal.org_id; JWTAuth.create_pair liest org_id-Feld und setzt so bei Nicht-Default-Mandanten default in Tokens. Regressionstest und Laufzeitreproduktion ausstehend. |
| 2026-09-28 | A8 | PR #1 get_check_runs am Head 6c6ef4f | release-verification am 2026-09-27 erfolgreich; keine Tests in dieser Audit-Sitzung ausgeführt. CI-Erfolg ersetzt keine Mandanten-Bindungsprüfung. |

## Befunde und Maßnahmen
### F-001 – Login verliert Mandantenbindung (statisch bestätigt, Laufzeitprüfung offen)
- Branch/SHA: security/v5.1-foundation @ 6c6ef4f136c78692bd126cc1ebee452576f79a64; PR #1.
- Pfade: botboy/gateway/routes_auth.py (auth_login/AuthPrincipal), botboy/gateway/auth.py (AuthPrincipal/JWTAuth._coerce_principal/create_pair).
- Nachweis: auth_login ermittelt org_id aus API-Key oder Principal-Store, baut aber AuthPrincipal(..., metadata={"org_id": org_id}) ohne org_id=org_id. AuthPrincipal.org_id hat Default "default"; _coerce_principal verwendet dieses Feld und create_pair schreibt es als JWT-Claim.
- Reproduktion zur Absicherung: Principal in tenant-a anlegen; über /api/auth/login anmelden; org_id im JSON der Loginantwort und im verifizierten Access- und Refresh-JWT vergleichen. Erwartet tenant-a, statisch abgeleitet default. Beide Anmeldearten prüfen.
- Auswirkung: Falsche Tenant-Identität in ausgegebenen JWTs; abhängig von nachfolgenden Prüfpfaden Zugriffsausfall oder fehlerhafte Mandantenzuordnung. Keine Behauptung eines ausnutzbaren Cross-Tenant-Zugriffs ohne End-to-End-Nachweis.
- Vorläufiger Schweregrad: HOCH (Sicherheitsgrenze, bis Laufzeitreproduktion/Impact-Analyse).
- Maßnahme: Beim Login org_id explizit an AuthPrincipal(org_id=org_id) übergeben, Metadaten nicht als autoritative Tenant-Quelle verwenden. Tests für Passwort- und API-Key-Login, nicht-default Tenant, Access/Refresh sowie nachfolgende Tenant-Autorisierung ergänzen.
- Status: IN ARBEIT; weder Codekorrektur noch Regressionstests in dieser Audit-Sitzung vorgenommen.

## Prüfhinweise, noch keine bestätigten Defekte
- Letzte Ausführungsfreigabe: Im PR-Diff installiert gateway/__init__.py importzeitige Patches; execution_security_gate.py umhüllt CommandExecutionService._run_route. Tatsächliche Abdeckung aller Ausführungspfade und Approval-Replay prüfen.
- PR-Beschreibung und 2026-09-25-Plantext gegen aktuellen Head und CI abgleichen; Beschreibung ist kein Implementierungsbeweis.

## Nächster Schritt
F-001 durch End-to-End-Test absichern und beheben; A1 rekursiv abschließen, A2 auf den fixierten SHAs statt nur auf dem PR-Basis-Diff prüfen und erst dann A3/A4 als abgeschlossen markieren. Nach jedem abgeschlossenen Gate dieses Dokument mit Datum, Evidenz und Restpunkten aktualisieren.