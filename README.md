# BotBoy

BotBoy ist eine lokal laufende Python-Anwendung mit CLI, stdlib-HTTP-Gateway, optionalem FastAPI-Gateway, Task-, Trace- und History-Persistenz, Skill-Routing und MCP-Server.

## Release-Inhalt

Dieser Release-Baum enthaelt:

- `botboy/` als Python-Paket
- `botboy/data/` mit Status-Snapshot und Release-Eval-Bundle
- `botboy/web/` mit der ausgelieferten Web-Oberflaeche
- `skills/` als ausgelieferte Skill-Bibliothek
- `examples/skills/` als Beispielskills
- `botboy_mcp_server.py`
- `setup.py`, `MANIFEST.in`, `requirements.txt`, `.env.example`
- `tests/` als source-tree Release-Smokes

Zusaetzlich liegen die fuer installierte Pakete benoetigten Runtime-Ressourcen gebuendelt unter `botboy/bundled/`.

## Installation

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Start

CLI:

```powershell
python -m botboy
```

stdlib-Gateway:

```powershell
python -m botboy serve
```

MCP-Server:

```powershell
python botboy_mcp_server.py
```

Worker-Daemon fuer lokale v2-Worker-Fabric:

```powershell
python -m botboy worker-daemon --worker-id executor --node-id local-executor --once
```

Remote-/Auth-Readiness vor einem nicht-lokalen Betrieb:

```powershell
python -m botboy security remote-readiness 0.0.0.0
```

Ein nicht-lokaler Gateway-Bind wie `0.0.0.0` startet fail-closed, solange Auth, stabiler JWT-Secret, nicht-wildcard CORS und Rate-Limit nicht aktiv sind. MCP-HTTP startet bei nicht-lokalem Bind nur mit `BOTBOY_MCP_HTTP_TOKEN`.

## Release-Verifikation

Lokaler Release-Smoke:

```powershell
python -m botboy.release_smoke -v
```

CLI-Alias:

```powershell
python -m botboy test
```

Release-Eval-Bundle:

```powershell
python -m botboy evals --summary
```

Source-Tree-Release-Tests:

```powershell
python -m unittest discover -s tests -v
```

Erwartetes Ergebnis: `OK` ohne Fehler.

Vollstaendige Release-Acceptance aus sauberem Build- und Install-Pfad:

```powershell
.\.venv-build\Scripts\python.exe -m botboy.release_acceptance full --python .\.venv-build\Scripts\python.exe --bootstrap-python python --outdir .\.botboy-runtime\post-edge-hardening-dist
```

Der Acceptance-Runner verwendet standardmaessig einen OS-Temp-Root unter `%TEMP%\\botboy-release-acceptance` (Linux/macOS: `$TMPDIR/botboy-release-acceptance`) und erzwingt disjunkte Write-Sets zwischen Temp-Root und Artifact-Outdir. Build-/Install-Tempdaten bleiben damit isoliert vom Workspace, sofern nicht explizit anders konfiguriert. Mit `BOTBOY_ACCEPTANCE_TEMP_ROOT` kann der Temp-Root ueberschrieben werden, und mit `BOTBOY_ACCEPTANCE_STANDARD_SITE_PACKAGES` kann bei Offline-Install-Acceptance ein explizites Seed-`site-packages` gesetzt werden.

Die source-tree Vollsuite enthaelt den Build-/Install-Acceptance-Lauf jetzt standardmaessig. Wenn ein Interpreter mit `build` und ein Bootstrap-Interpreter mit `pip` verfuegbar sind, wird die saubere Build-/Install-Pruefung automatisch in `python -m unittest discover -s tests -v` mit ausgefuehrt.

Der aktuelle reproduzierbare Release-Stand steht in `botboy/data/STATUS_SNAPSHOT.json`.

## Wichtige Dateien

- `botboy/__main__.py` orchestrator export
- `botboy/cli.py` CLI entrypoint
- `botboy/runtime.py` shared runtime bootstrap
- `botboy/command_execution_service.py` ausgelagerte Command-Execution-Huelle mit Trace-, Cache-, History- und Task-Finalisierung
- `botboy/command_router.py` zentraler Router fuer die aktive Command-Dispatch-Surface
- `botboy/handoff_route_support.py` ausgelagerter Handoff-Route-Support fuer Single- und Batch-Handoffs
- `botboy/intelligence_route_support.py` ausgelagerte plan/think/skill/llm/hybrid-search-Routes
- `botboy/async_command_support.py` shared async command handling for evals and task resume
- `botboy/introspection_support.py` Runtime-Introspection fuer Monitoring, Worker, Reflection, Delegation und A2A
- `botboy/gateway/server.py` FastAPI-Gateway
- `botboy/gateway/simple_server.py` stdlib-Gateway
- `botboy/gateway/simple_handler_surface.py` ausgelagerte stdlib-Handler-Surface fuer Auth-, Runtime-, Task- und Principal-Endpunkte
- `botboy/gateway/simple_server_runtime.py` ausgelagerter stdlib-Runtime- und Lifecycle-Wrapper fuer Start/Stop/Thread/Server
- `botboy/gateway/routes_core.py` FastAPI core/status/monitoring routes
- `botboy/gateway/routes_tasks.py` FastAPI task/merge/worker routes inklusive versionierter `/api/v2/workers/*`-Worker-Node-Surface
- `botboy/gateway/routes_auth.py` FastAPI auth/principal routes
- `botboy/gateway/routes_ws.py` FastAPI WebSocket route
- `botboy/gateway/dashboard_payload.py` shared dashboard enrichment for FastAPI and stdlib
- `botboy/gateway/merge_actions.py` shared merge action execution for FastAPI and stdlib
- `botboy/gateway/simple_routing.py` shared stdlib route resolution for GET/POST/DELETE
- `botboy/operator_workbench_support.py` shared merge-review queue and operator workbench support
- `botboy/agent_ops_support.py` shared command support for contracts, skill routing, handoff advice and A2A
- `botboy/history_support.py` shared history command rendering and filtering
- `botboy/task_command_support.py` shared task command parsing, merge-review actions and read-side task views
- `botboy/introspection_command_support.py` shared command support for workers, reflection archive and archetype introspection
- `botboy/runtime_surface_support.py` shared help/status runtime surface output
- `botboy/runtime_command_support.py` shared memory and scheduler command handling
- `botboy/migrations/runner.py` additive Schema-Migrationen mit Versionsverfolgung
- `botboy/migrations/task_store.py` Task-Store-Migrationen fuer Worker-Nodes, Execution-Queues und Queue-Leases
- `botboy/bootstrap_service.py` ausgelagerter Bootstrap- und Initialisierungspfad des Orchestrators
- `botboy/orchestrator_lifecycle_service.py` ausgelagerter Trace-, Task- und Shutdown-Lifecycle des Orchestrators
- `botboy/task_merge_service.py` ausgelagerte Merge-Review-, Policy- und Payload-Logik
- `botboy/worker_handoff_service.py` ausgelagerte Worker-Handoff- und Parent-Sync-Logik
- `botboy/gateway/simple_server_support.py` zentrale stdlib-Gateway-Support-Surface fuer Access, Auth, Task-Dekoration und Handler-State
- `botboy/gateway/simple_read_handlers.py` shared stdlib GET handlers for history and scheduler routes
- `botboy/gateway/simple_runtime_read_handlers.py` shared stdlib GET handlers for health, status, metrics, dashboard, skills and memory routes
- `botboy/gateway/simple_runtime_write_handlers.py` shared stdlib POST handlers for command execution and memory writes
- `botboy/gateway/simple_task_read_handlers.py` shared stdlib GET handlers for trace, task, worker und worker-node read-side routes
- `botboy/gateway/simple_task_write_handlers.py` shared stdlib POST handlers for merge actions, resume, cancel, reassign und worker-node writes
- `botboy/gateway/simple_auth_handlers.py` shared stdlib auth and principals handlers
- `botboy/tasks.py` Task-Store
- `botboy/evals.py` Eval-/Replay-Runner
- `botboy/release_smoke.py` installierbarer Release-Smoke
- `botboy/release_acceptance.py` sauberer Build-/Install-/Artifact-Gate
- `botboy/data/botboy_skill_registry.json` Skill-Registry
- `botboy/data/STATUS_SNAPSHOT.json` Release-Snapshot
- `.github/workflows/release-verification.yml` CI-Gate fuer Build, Install und Release-Tests

## Hinweise

- Laufzeitdaten werden standardmaessig unter `~/.botboy/` angelegt. Mit `BOTBOY_HOME` kann ein anderer Root gesetzt werden.
- Die ausgelieferte Default-Skill-Konfiguration verwendet die gebuendelte Skill-Bibliothek, nicht `~/.botboy/skills`.
- Das Release-Eval-Bundle ist absichtlich klein und dient als reproduzierbarer Smoke, nicht als Dev-Vollsuite.
- Der Build-/Install-Acceptance-Pfad kann weiterhin direkt ueber `botboy.release_acceptance` oder CI gefahren werden; die normale lokale Vollsuite deckt ihn jetzt ebenfalls mit ab.
- Login-Endpunkte fallen jetzt fail-closed: es gibt keinen Development-JWT-Fallback mehr, und Credential-/API-Key-Stores weichen bei Oeffnungsfehlern nicht mehr still auf `:memory:` aus.
- Nicht-lokale Gateway-Binds und MCP-HTTP sind fail-closed: lokale Defaults bleiben entwicklungsfreundlich, Remote-Betrieb muss explizit abgesichert werden.
- Die UI-Acceptance deckt jetzt zusaetzlich den Live-`/api/dashboard`-Vertrag in FastAPI und stdlib ab; `web/index.html` ist die kanonische Control-Center-Surface, `web/dashboard.html` bleibt die Legacy-Dashboard-Seite.
- WebSocket- und stdlib-Shutdown-Randpfade sind weiter verengt; `routes_ws.py` behandelt nur noch echte Laufzeit-/Nutzfehler fail-closed, und `simple_server_runtime.py` ruft `shutdown()` nicht mehr unnoetig ohne laufenden Serving-Thread auf.
- Die erste `v2`-Worker-Fabric-Scheibe ist jetzt vorhanden: additive Task-Store-Migrationen, persistente Worker-Nodes und Execution-Queues, `worker node ...`-CLI-Kommandos sowie versionierte `/api/v2/workers/*`-Endpunkte in FastAPI und stdlib.
