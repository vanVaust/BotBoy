# Lokale Sicherheitsintegration — 16.–17. Oktober 2026

BotBoy wurde auf ausdruecklichen Nutzerauftrag von zwei autonomen
Entwickler-Agenten und einem Integrator korrigiert und gegengeprueft.
Untersuchungsbasis: main `afc84e432b4bafafb7e631875aef106117219fc1` und
Security-Branch `6c6ef4f136c78692bd126cc1ebee452576f79a64`.
Arbeitszweig: `fix/security-integration-2026-10-16`.
Fortsetzung und unabhaengige Gegenpruefung: 17. Oktober 2026 (Europe/Berlin).

Das ist eine lokale, unveroeffentlichte Integration. GitHub-main und PR #1
wurden nicht veraendert. Ein vollstaendiges Audit aller Dateien, eine
Produktionsfreigabe und der Nachweis einer kompromittierten Installation
sind damit nicht verbunden.

## Arbeitsteilung und Gegenpruefung

- Agent Identitaet: Login/JWT/Credentials, Tenant-/Rollenbindung, API-Kontext,
  Memory-/Task-Pagination, Worker-Grenzen, HTTP-/WebSocket-Integration.
- Agent Ausfuehrung: expliziter finaler Gate, Approval-Lebenszyklus,
  Task-/History-/Cache-Kontext, Command-Umgehungen, Scheduler-Reautorisierung.
- Integrator: Branch-Abgleich, Minimalinstallation, Packaging/CI,
  stdlib-/MCP-/WebSocket-Adapter, globale Leseflaechen, Gegenpruefungen und
  gemeinsame Verifikation.

Die Agenten haben sich direkt zu Schnittstellen und Gegenbeispielen
abgestimmt. Jeder Bereich wurde zusaetzlich aus Sicht eines anderen
Beteiligten angegriffen; die Pruefung blieb auf konkrete, reproduzierbare
Fehler und unmittelbare Folgeprobleme begrenzt.

## Behobene Befunde

1. Fremde Task-Details auf main: Benutzer-/Tenant-/Objektgrenzen der
   integrierten Proxy-Pfade bleiben erhalten und sind ueber echte Requests
   negativ getestet.
2. Login-Token verwendete default statt Credential-Tenant: Passwort- und
   API-Key-Anmeldung binden Antwort, Access und Refresh an denselben Tenant.
3. Command- und Admin-Kontext verlor Tenant/Rollen: ein verifizierter
   SecurityContext wird explizit bis zu Task, Persistenz, History und Cache
   weitergereicht. Credentialquelle und -version bleiben fuer spaetere
   Reautorisierung erhalten.
4. Credential-Ausstellung im falschen Tenant und fremdes Principal-Upsert:
   Tenant-/Systemregeln werden an Request- und atomarer Store-Grenze geprueft.
5. Passwortreset, deaktivierte Principals, Rollen-/Tenantwechsel und widerrufene
   API-Keys: bestehende Login-Credentials werden erneut gegen den Store geprueft.
6. Refresh-Replay: atomarer Verbrauch des Refresh-Token-Identifier.
7. Approval-HTTP500 durch privaten DB-Zugriff ueber Proxy: explizite
   Lifecycle-/Approval-Schnittstelle nach Objektberechtigungspruefung.
8. Approval-Replay am finalen Effekt: atomarer Execution-Claim neben dem
   vorherigen Resume-Verbrauch. Grant bindet Task, Principal, Tenant, Befehl,
   Capability und Autorisierungsversion.
9. Ausfuehrungs-Gate war Gateway-Importpatch: jetzt direkter Aufruf im
   transportneutralen CommandExecutionService, auch ohne Gateway-Import.
10. Geschuetzte APIs durch generische Commands umgehbar: externe globale
    Command-Familien werden vor Cache/Dispatch abgewiesen. Globale
    Self-Healing-/DAG-Nachlaeufe werden nicht durch normale Remote-Nutzer
    ausgeloest.
11. Worker-Node-/Queue-Uebernahme und gefaelschte Identitaetsmetadaten:
    serverseitige Tenant-/Ownerbindung, Schutz vorhandener fremder Objekte,
    identische Tenantbindung fuer gepaarte Queue und Lease-Lesesichten.
12. Filterung nach Pagination: Task-/Memory-Filter laufen vor SQL-Limit und
    Offset. Trace-Kandidaten werden vollstaendig autorisiert und danach
    paginiert; gleiche Principal-ID in fremdem Tenant bleibt unsichtbar.
13. stdlib-REST und MCP-HTTP: expliziter Kontext, scoped Datenpfade,
    serverseitige Approvals und Fail-closed fuer nicht freigegebene Operationen.
14. Direkter LLM-WebSocketpfad: alle Nachrichten laufen durch den
    Ausfuehrungskern; Credentials werden bei jeder Nachricht neu geprueft.
15. Basisinstallation brauchte undeclared FastAPI: gemeinsame HTTP-Fehler sind
    transportneutral; Basis-CLI/stdlib bleiben ohne FastAPI importierbar.
16. Acceptance uebernahm Entwicklerpakete: Basis/Standard werden in leere
    Environments mit tatsaechlicher Dependency-Aufloesung installiert. Offline
    ist ein explizites Wheelhouse vorgesehen.
17. Venv-Python-Auswahl, Temp-/Artifact-Ueberlappung, Importkontamination:
    Interpreter-Symlink bleibt erhalten; disjunkte Writes und isolierte
    Paketimporte werden geprueft.
18. Gefaelschte Forwarded-Header und Frameworkmutation: explizite ASGI-
    Middleware, konfigurierbare Trusted-Proxies, stdlib bindet Rate-Limits an
    den echten Peer ohne explizites Proxy-Vertrauen.
19. Doppelte History-Routen/OpenAPI-Operationen und veralteter Status:
    Registrierung konsolidiert; Snapshot unterscheidet lokale Verifikation,
    historische Behauptungen und offenen Release-Nachweis.
20. Verzoegerte Scheduler-Ausfuehrung verlor Kontext und erfand Systemrechte:
    separater authoritativer DB-Kontext, exakte Credentialquelle/-version,
    Befehlsbindung und erneute Principal-/Rollen-/Tenant-/Key-Pruefung vor dem
    Effekt. Workflow-Payload kann diese Werte nicht ersetzen. Alte Jobs ohne
    ausreichende Bindung werden bei aktivierter Authentifizierung abgewiesen.
21. Authentifizierter FastAPI-Start ohne persistenten Widerrufsspeicher:
    Speicherfehler brechen den Start sicher ab; stdlib erfuellt denselben
    Vertrag. Lokaler Betrieb ohne Auth kann weiterhin ohne Persistenz starten.
22. Konkurrierende Kontextreduktionen konnten entzogene Rechte wiederherstellen:
    atomarer Compare-and-swap auf dem gelesenen Kontext; konkurrierende
    Erstbindung oder Update werden abgewiesen und verlangen Revalidierung.
23. Gleichzeitige erste Worker-Registrierung konnte fremde IDs uebernehmen:
    Mandantenpruefung, Node, Queue und Faehigkeiten liegen in einer
    BEGIN-IMMEDIATE-Transaktion. Fehler rollen die gesamte Registrierung
    zurueck; lokale und ausdrueckliche Systemadministration bleiben moeglich.

## Empirische Nachweise

Abschliessende gemeinsame Pruefung am 17. Oktober 2026, Linux/Python 3.12.14,
FastAPI 0.138.2:

| Pruefung | Ergebnis |
|---|---|
| unittest-Vollsuite mit CI-striktem Build-Gate | 388 Tests, OK, keine Skips |
| Gebaute sdist/Wheel und isolierte Basis-/Standardinstallation | Bestanden, Bestandteil der Vollsuite |
| pytest einschliesslich freier Testfunktionen, ohne doppelte Build-Acceptance | 388 bestanden, 74 Unterfaelle bestanden |
| Release-Smoke | 6 Tests, OK |
| Release-Evals | 4/4 Manifestfaelle und Seeds, Integritaet OK, Passrate 1.000 |
| Dependency-Pruefung | Keine unaufgeloesten Anforderungen |
| Syntax und gesamter Integrationsdiff | compileall und git diff HEAD --check bestanden |

Die pytest-Warnungen betreffen erwartete lokale Auth-disabled-Testfixtures
und die Deprecation des aktuellen Starlette/httpx-Testclients; es gab keine
Testfehler. Die lokal vorbereitete CI-Matrix hat vier Kombinationen und ist
syntaktisch geprueft, aber nicht auf GitHub ausgefuehrt.

Protokolle und ein maschinenlesbarer Nachweis liegen im gemeinsamen Workspace
unter `/workspace/reports/botboy-integration-2026-10-17/`.

Zusaetzliche gezielte und historische Zwischenpruefungen:

- Erste gemeinsame Vollsuite: 355 unittest-Tests, OK; inklusive echter
  Basis-/Standardinstallation aus gebautem Wheel, keine Skips.
- Packaging-/Isolationspruefung: 5 Tests, OK.
- Identitaets-Agent: 63 fokussierte Tests, OK; echte FastAPI-, stdlib- und
  WebSocket-Verbindungen, Nicht-default-Tenants und positive/negative Faelle.
- 16 parallele Refresh-Versuche ueber mehrere Auth-Instanzen: ein Erfolg.
- 12 parallele finale Execution-Claims ueber 12 TaskStore-Instanzen und
  verschiedene SQLite-Verbindungen: ein Gewinner.
- MCP-Servicekontext: echte temporaere SQLite-Stores, gefaelschte Metadaten,
  same-principal/foreign-tenant Memory und History, gesperrte Umwege und
  zurueckgesetzte ContextVars.
- Abschliessende unabhaengige Execution-Pruefung: 44 Tests bestanden;
  Worker-Transaktion von einem zweiten Agenten mit 16 Tests gegengeprueft.
- Zwei CAS-Race-Regressionen wurden gegen das alte blinde Upsert gezielt rot
  ausgefuehrt und bestehen mit dem neuen Compare-and-swap. Die Negativkontrolle
  lief in einem separaten Prozess ohne Ruecksetzen der Produktdateien.
- Worker-Registrierung: deterministische Kollisionen auf Node- und Queue-ID,
  vollstaendiger Rollback bei Fehlern fuer Datei-/In-Memory-Datenbanken sowie
  erlaubte Mandantenupdates und ausdrueckliche Systemadministration.

Testmengen ueberlappen; ihre Zahlen sind nicht zu einer Zahl unabhaengiger
Tests zu addieren. Aeltere Zwischenlaeufe enthielten echte Defekte und
veraltete Test-Doubles; sie wurden korrigiert und gezielt erneut geprueft.
Im abschliessenden Lauf wurde ausserdem der ausgelieferte Smoke-Test an den
korrigierten Workspace-Namen des Status-Snapshots angepasst.

## Kompatibilitaet und bewusste Grenzen

- Alte Login-JWTs ohne Credentialversion erfordern erneutes Login.
- Alte geplante Jobs ohne eindeutig revalidierbare Credentialquelle/-version
  werden im authentifizierten Betrieb gesperrt und muessen neu angelegt werden.
- Ein Approval erlaubt hoechstens einen finalen Ausfuehrungsversuch. Bei
  Fehler nach dem Claim kann ein Effekt bereits stattgefunden haben; Retry
  braucht einen neuen Grant. Das ist keine Garantie erfolgreichen Exactly-once.
- Approvals sind an den Task-Eigentuemer gebunden. Dies ist kein implementierter
  Vier-Augen-Prozess oder eine allgemeine Freigabe zur Administration fremder
  Benutzer-Tasks; OS-/Tool-Isolation ist separat zu bewerten.
- Externe globale CLI-Familien, Planner-/Hybrid-/Host-Introspektion verwenden
  keine ungescopten internen Stores. Sie sind remote gesperrt, bis passende
  autorisierte Handler existieren. Lokal bleiben CLI und MCP-stdio
  Operator-Schnittstellen.
- MCP-HTTP verwendet einen serverseitigen Serviceprincipal pro gemeinsamem
  Transport-Token, keine individuelle JWT-Anmeldung. Tools wie globales
  Scheduling, Host-Introspektion und Memory-Loeschung sind dort nicht freigegeben.
- Prozessweite Telemetrie und Dashboard verlangen bei Auth eine Systemrolle.
  Tenant-Admins erhalten darueber keinen globalen Datenzugriff.
- WebSocket antwortet ueber die normale Command-Pipeline. Der alte direkte
  LLM-Streamingpfad ist entfernt.
- Trace-Filter sind korrekt, benoetigen derzeit aber O(N)-Scans/N+1-Zugriffe.
  Eine indexierte Tenant-Spalte ist ein Folgeprojekt.
- Einige Gateway-Proxy-Hooks sind weiter importzeitige Erweiterungen. Ihre
  schrittweise Ablosung ist Architekturschuld, kein abgeschlossener Punkt.
- Organisationsverwaltung ueber private DB-APIs ist bei aktivierter Auth
  fail-closed blockiert und kein freigegebenes Adminfeature.
- OS-Sandbox, Last-/Dauerbetrieb, Live-LLM-/Providerverhalten, saemtliche
  Artefaktpfade und jede historische Aenderung sind nicht vollstaendig auditiert.
- Die erweiterte CI-Matrix fuer Windows/Linux und Python 3.10/3.13 ist lokal
  vorbereitet. Sie wurde nicht auf GitHub ausgefuehrt; lokal Python 3.12/Linux.
- Keine Pushes, GitHub-Kommentare, Repositoryeinstellungen oder Deployments.
  Vor einer externen Freigabe bleiben Code-Review, CI-Matrix und Betriebspruefung
  erforderlich.

## Reproduzierbare gemeinsame Kommandos

```sh
python -m unittest discover -s tests -v
python -m pytest tests -q --ignore=tests/test_release_build_install.py
python -m botboy.release_smoke -v
python -m botboy evals --summary
python -m pip check
```

Die unittest-Suite baut sdist/Wheel und installiert Basis/Standard in getrennte
leere venvs. pytest fuehrt zusaetzlich freie Testfunktionen aus. CI installiert
Build-Tooling explizit; fehlendes Tooling darf dort nicht als Skip durchgehen.
