# BotBoy v2.5 bis v5 Execution Roadmap

Letzte Aktualisierung: 2026-06-14  
Arbeitsstand: v5 local production candidate, Rebase auf GitHub-v5 integriert, Full-Suite lokal gruen.

## Zielbild

BotBoy wird als lokal-first Orchestrator mit stabiler Single-Host-Runtime, Worker-Fabric, Governance, Replay/Trace-Auswertung, Gateway-Parität und veröffentlichbarem GitHub-Release geführt. Remote-/Team-Betrieb bleibt fail-closed und wird nur mit expliziter Auth-, Approval- und Deployment-Policy freigegeben.

## Aktueller Stand

- v2.5 Runtime-Basis ist abgeschlossen: Tasks, Worker-Nodes, Queue-Leases, Gateway-Routen, UI-Control-Center, Release-Smoke und Eval-Assets sind vorhanden.
- v3 Replay-/Trace-Arbeit ist auf v5 portiert: Replay-Diffs sind nicht mehr an entfernte Workflow-IR-Dateien gekoppelt, sondern leben in `botboy.security.replay_diff`.
- v4/v5 Grundlagen sind vorhanden: Governance, Zero-Trust-Modul, Tenant-Sandbox, Replay-Harness und DAG/Mass-Escalation-Strukturen existieren.
- Dashboard-Payload enthält einen `control_center_contract` mit Queue-, Replay- und Incident-Segmenten.
- Offener Gate: normaler Push nach GitHub ohne Force, danach CI-/Remote-Abgleich.

## Agenten-Orchestrierung

1. Senior Software Architect: hält Zielarchitektur, Bounded Contexts, öffentliche Contracts und Release-Gates konsistent.
2. Senior Runtime Engineer: verantwortet TaskStore, Queue-Leases, Worker-Fabric, Replay-Diff-Persistenz und Recovery.
3. Senior Security Engineer: verantwortet Auth, Approval, Governance, Zero-Trust, Tenant-Sandbox und Remote-Fail-Closed-Regeln.
4. Senior Gateway Engineer: hält FastAPI, stdlib Gateway, CLI und MCP in Contract-Parität.
5. Senior UI/UX Engineer: hält Control Center, Dashboard-Vertrag und Operator-Signale konsistent.
6. Senior Test/Release Engineer: verantwortet Smokes, Eval-Bundle, Full-Suite, Packaging und CI.
7. Senior Product/Operations Architect: trennt produktionsnahe v2.5/v5-Ziele von langfristigen Agent-OS-Nordsternen.

## Nächste Arbeitspakete

### NOW-01: Rebase sauber abschließen

- Konfliktmarker in Roadmap, Status-Snapshot, Dashboard, UI und Release-Tests entfernen.
- Entfernte Workflow-IR-Dateien nicht wiederherstellen.
- Replay-Diff-Funktion auf v5-Struktur portieren.
- Verifikation: Der Konfliktmarker-Scan ueber den Arbeitsbaum muss leer sein.

### NOW-02: Replay-Diff-Persistenz final prüfen

- `replay_diffs` Migration in TaskStore prüfen.
- `compare_replay_payloads`, `persist_replay_diff_report`, `load_replay_diff_report`, `list_replay_diff_summaries` und `summarize_replay_diffs` testen.
- Replay-Harness muss `compare_runs` bereitstellen und bei vorhandenem TaskStore persistieren.
- Dashboard muss `replay_diff_total`, `replay_diff_drift_count` und `control_center_contract.segments.replay.replay_diff` ausgeben.

### NOW-03: Release-Gates ausführen

- `python -m py_compile` auf geänderte Python-Dateien.
- `python -m unittest tests.test_replay_diff tests.test_gateway_dashboard_payload tests.test_release_ui_acceptance tests.test_release_resources -v`.
- `python -m botboy.release_smoke -v`.
- `python -m botboy evals --summary`.
- Ergebnis lokal: `python -m unittest discover -s tests -v` lief mit `Ran 197 tests, OK (skipped=1)`.

### NOW-04: GitHub-Veröffentlichung

- Rebase abschließen.
- Commit auf `main` behalten, kein Force-Push.
- `git push origin main`.
- Danach GitHub-Stand mit lokalem Stand vergleichen.

## v5 Produktionsabschluss

BotBoy ist für öffentlichen GitHub-Release bereit, wenn diese Gates grün sind:

- Keine Merge-Konflikte, keine toten Imports, keine gelöschten Modulabhängigkeiten.
- Full-Suite grün oder jeder Skip explizit begründet.
- Release-Smoke grün.
- Eval-Summary grün.
- Dashboard/API-Contract grün.
- Remote-Risiken in Dokumentation und Defaults fail-closed.
- Packaging- und Install-Gate im CI verfügbar.

## Offene Risiken

- Der GitHub-v5-Commit war groß und hat alte Workflow-IR-Dateien entfernt; Regressionsrisiko liegt vor allem in importseitigen Altverweisen.
- Remote-/Team-Betrieb bleibt erst nach separater Auth-/Deployment-Härtung produktionsreif.
- v5 ist lokal produktionsnah, aber erst nach finaler Full-Suite und GitHub-CI als veröffentlichter Release-Kandidat einzustufen.
