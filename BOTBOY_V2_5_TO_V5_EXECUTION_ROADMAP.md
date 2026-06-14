# BotBoy v2.5 bis v5 Execution Roadmap

Letzte Aktualisierung: 2026-06-14
Arbeitsstand: v5 GitHub-published local production candidate, Queue-Lease-API abgeschlossen, Remote-Deployment-Fail-closed-Policy implementiert.

## Zielbild

BotBoy wird als lokal-first Orchestrator mit stabiler Single-Host-Runtime, Worker-Fabric, Governance, Replay/Trace-Auswertung, Gateway-Paritaet und veroeffentlichbarem GitHub-Release gefuehrt. Remote-/Team-Betrieb bleibt fail-closed und wird nur mit expliziter Auth-, Approval- und Deployment-Policy freigegeben.

## Aktueller Stand

- v2.5 Runtime-Basis ist abgeschlossen: Tasks, Worker-Nodes, Queue-Leases, Gateway-Routen, UI-Control-Center, Release-Smoke und Eval-Assets sind vorhanden.
- Queue-Leases sind vertikal abgeschlossen: Acquire, Renew, Release, List, Summary, Expiry, Recovery-Signal, Parallelismus-Limit, Drain-Schutz, FastAPI-/stdlib-Routen und Dashboard-Signale sind getestet.
- v3 Replay-/Trace-Arbeit ist auf v5 portiert: Replay-Diffs sind nicht mehr an entfernte Workflow-IR-Dateien gekoppelt, sondern leben in `botboy.security.replay_diff`.
- v4/v5 Grundlagen sind vorhanden: Governance, Zero-Trust-Modul, Tenant-Sandbox, Replay-Harness und DAG/Mass-Escalation-Strukturen existieren.
- Dashboard-Payload enthaelt einen `control_center_contract` mit Queue-, Replay- und Incident-Segmenten.
- GitHub-Publikation ist abgeschlossen: `main` ist ohne Force-Push mit `origin/main` synchron.
- Release-CI ist auf Node-24-kompatible Action-Majors aktualisiert: `actions/checkout@v6`, `actions/setup-python@v6`.
- Remote-/Team-Betrieb ist fail-closed gehaertet: nicht-lokale Gateway-Binds benoetigen Auth, stabilen JWT-Secret, nicht-wildcard CORS und Rate-Limit; MCP-HTTP benoetigt ein Token.

## Agenten-Orchestrierung

1. Senior Software Architect: haelt Zielarchitektur, Bounded Contexts, oeffentliche Contracts und Release-Gates konsistent.
2. Senior Runtime Engineer: verantwortet TaskStore, Queue-Leases, Worker-Fabric, Replay-Diff-Persistenz und Recovery.
3. Senior Security Engineer: verantwortet Auth, Approval, Governance, Zero-Trust, Tenant-Sandbox und Remote-Fail-Closed-Regeln.
4. Senior Gateway Engineer: haelt FastAPI, stdlib Gateway, CLI und MCP in Contract-Paritaet.
5. Senior UI/UX Engineer: haelt Control Center, Dashboard-Vertrag und Operator-Signale konsistent.
6. Senior Test/Release Engineer: verantwortet Smokes, Eval-Bundle, Full-Suite, Packaging und CI.
7. Senior Product/Operations Architect: trennt produktionsnahe v2.5/v5-Ziele von langfristigen Agent-OS-Nordsternen.

## Arbeitspakete

### NOW-01: Rebase sauber abschliessen

- Status: abgeschlossen.
- Konfliktmarker in Roadmap, Status-Snapshot, Dashboard, UI und Release-Tests entfernen.
- Entfernte Workflow-IR-Dateien nicht wiederherstellen.
- Replay-Diff-Funktion auf v5-Struktur portieren.
- Verifikation: Der Konfliktmarker-Scan ueber den Arbeitsbaum muss leer sein.

### NOW-02: Queue-Lease-API finalisieren

- Status: abgeschlossen.
- TaskStore: `acquire_queue_lease`, `renew_queue_lease`, `release_queue_lease`, `list_queue_leases`, `queue_summary`.
- Semantik: Expiry, recoverable expired leases, max parallelism, drain prevents assignment.
- Surface: FastAPI und stdlib unter `/api/v2/workers/leases*`.
- Verifikation: Queue-Lease-Contract, Gateway-Live-Routen, UI-Acceptance und Full-Suite.

### NOW-03: Replay-Diff-Persistenz final pruefen

- Status: abgeschlossen.
- `replay_diffs` Migration in TaskStore ist vorhanden.
- `compare_replay_payloads`, `persist_replay_diff_report`, `load_replay_diff_report`, `list_replay_diff_summaries` und `summarize_replay_diffs` sind getestet.
- Replay-Harness stellt `compare_runs` bereit und persistiert bei vorhandenem TaskStore.
- Dashboard gibt `replay_diff_total`, `replay_diff_drift_count` und `control_center_contract.segments.replay.replay_diff` aus.

### NOW-04: Release-Gates ausfuehren

- Status: abgeschlossen.
- `python -m py_compile` auf geaenderte Python-Dateien.
- `python -m unittest tests.test_replay_diff tests.test_gateway_dashboard_payload tests.test_release_ui_acceptance tests.test_release_resources -v`.
- `python -m botboy.release_smoke -v`.
- `python -m botboy evals --summary`.
- Ergebnis nach Remote-Hardening: `python -m unittest discover -s tests -v` lief mit `Ran 214 tests, OK`.

### NOW-05: GitHub-Veroeffentlichung

- Status: abgeschlossen.
- Rebase abschliessen.
- Commit auf `main` behalten, kein Force-Push.
- `git push origin main`.
- Danach GitHub-Stand mit lokalem Stand vergleichen.
- Release-CI nach Push muss gruen sein und darf keine kurzfristige Node-20-Action-Deprecation mehr enthalten.

### NOW-06: Remote-Deployment-Haertung

- Status: abgeschlossen.
- Zentrale Policy in `botboy.gateway.security` prueft lokale vs. nicht-lokale Bind-Hosts.
- FastAPI- und stdlib-Gateway scheitern beim Start fail-closed, wenn ein nicht-lokaler Bind ohne Auth, stabilen JWT-Secret, nicht-wildcard CORS oder Rate-Limit versucht wird.
- MCP-HTTP scheitert bei nicht-lokalem Bind ohne `BOTBOY_MCP_HTTP_TOKEN`.
- CLI-Readiness ist lauffaehig: `python -m botboy security remote-readiness 0.0.0.0`.
- Verifikation: Security-/Gateway-/MCP-Regressionen pruefen Ablehnung und erlaubte Remote-Konfiguration.

## v5 Produktionsabschluss

BotBoy ist fuer den oeffentlichen GitHub-Release bereit, wenn diese Gates gruen sind:

- Keine Merge-Konflikte, keine toten Imports, keine geloeschten Modulabhaengigkeiten.
- Full-Suite gruen oder jeder Skip explizit begruendet.
- Release-Smoke gruen.
- Eval-Summary gruen.
- Dashboard/API-Contract gruen.
- Remote-Risiken in Dokumentation und Defaults fail-closed.
- Packaging- und Install-Gate im CI verfuegbar.

## Offene Risiken

- Externe GitHub-Actions muessen nach jedem Push weiter beobachtet werden; lokal sind Release-Smoke, Eval-Summary und Full-Suite die verbindlichen Gates.
- Remote-/Team-Betrieb ist nicht automatisch aktiviert; er ist nur mit expliziter Auth-/Secret-/CORS-/Rate-Limit-Policy startbar.
- v5 bleibt lokal-first. Cloud- oder Multi-Host-Topologien sollen nur als explizite, rollbackfaehige Deployment-Scheiben entstehen.
