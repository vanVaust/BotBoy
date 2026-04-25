# BotBoy Skill Portfolio

## Zweck

Dieses Dokument ist die kanonische Skill-Portfolio-Planung fuer BotBoy.

Es verbindet:

- den aktuellen on-disk Skillbestand
- den frueheren Omni-Katalog
- externe Leitplanken zu MCP, Tooling, Evals, Agent-Sicherheit und Tracing

Ziel ist kein maximal grosses Skillset, sondern ein kompetentes, belastbares Portfolio mit klarer Implementierungsreihenfolge.

## Research Basis

### Lokale Quellen

- `botboy/data/botboy_skill_registry.json`
- `skills/public_skills/*`
- `skills/internal_skills/*`
- `skills/frozen_roadmap/*`
- `../Botboy_final_trash/botboi_finished_before_publication_cleanup/BAU-botboI-mitClaude/phase2_extracted/botboy_v3_dev/skill-catalog/BOTBOY_OMNI_SKILL_CATALOG.md`
- `../Botboy_final_trash/botboi_finished_before_publication_cleanup/BAU-botboI-mitClaude/phase2_extracted/botboy_v3_dev/skill-catalog/SKILL_INTEGRATION_PLAYBOOK.md`

### Externe Leitplanken

- MCP Server Concepts: [modelcontextprotocol.io/docs/learn/server-concepts](https://modelcontextprotocol.io/docs/learn/server-concepts)
- OpenAI API Docs Overview: [developers.openai.com/api/docs/models](https://developers.openai.com/api/docs/models)
- OpenAI Evals API Reference: [developers.openai.com/api/reference/resources/evals](https://developers.openai.com/api/reference/resources/evals)
- OWASP Top 10 for Agentic Applications 2026: [genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)
- OWASP Secure MCP Server Development: [genai.owasp.org/resource/a-practical-guide-for-secure-mcp-server-development](https://genai.owasp.org/resource/a-practical-guide-for-secure-mcp-server-development/)
- OpenTelemetry Traces: [opentelemetry.io/docs/concepts/signals/traces](https://opentelemetry.io/docs/concepts/signals/traces/)

### Daraus abgeleitete harte Regeln

1. Skills muessen an echte Plattformoberflaechen andocken: Tools, Resources, Prompts, Queues, Memory, Traces, Policies, UI.
2. Safety, Eval und Observability kommen vor Breite.
3. Das oeffentliche Skill-Portfolio bleibt klein und klar; die Tiefe liegt intern.
4. Frontier- und Forschungs-Skills bleiben bewusst spaet oder eingefroren.
5. Jeder Skill braucht einen belastbaren Platz im Stack: bauen, pruefen, betreiben, absichern oder orchestrieren.

## Aktueller On-Disk Cut

### `public_skills` - 8

- `botboy-cloud-topology-planner`
- `botboy-communication-doctrine`
- `botboy-defensive-security-auditor`
- `botboy-dense-coding-composer`
- `botboy-formal-logic-engine`
- `botboy-memory-architecture-engine`
- `botboy-reflection-loop-director`
- `botboy-server-fabric-engine`

### `internal_skills` - 12

- `botboy-adversarial-defense-lab`
- `botboy-control-center-smokes`
- `botboy-eval-flywheel`
- `botboy-event-signal-orchestrator`
- `botboy-gateway-hardening`
- `botboy-isolation-cage-designer`
- `botboy-omni-skill-architect`
- `botboy-runtime-policy-auditor`
- `botboy-skill-contract-author`
- `botboy-skill-evolution-forge`
- `botboy-trace-investigator`
- `botboy-virtual-dev-range`

### `frozen_roadmap` - 5

- `botboy-automata-compiler-lab`
- `botboy-cross-modal-mapper`
- `botboy-crypto-protocol-lab`
- `botboy-isomorphism-invariance-mapper`
- `botboy-multi-domain-simulator`

## Portfolio-Buckets

- `public_skills`: externe, klar vermarktbare und fachlich sichere Skills
- `internal_skills`: Plattformmechanik, Safety, Quality, Routing, Betrieb
- `frozen_roadmap`: spaetere Differenzierung, Experimente, Forschung

## Bewertungslogik

- `must-have`: noetig fuer einen belastbaren BotBoy-Kern
- `essential`: stark wertsteigernd und sauber begruendbar, aber nicht Blocker fuer den Kern
- `nice-to-have`: sinnvoll, aber erst nach stabilem Kern oder nur fuer Differenzierung

## 100-Skill Roadmap

Legende:

- `Status shipped`: existiert bereits
- `Status planned`: sinnvoller naechster Ausbau, noch nicht als Skill gebaut
- `Status shipped-frozen`: existiert, ist aber nicht Teil des Kernportfolios

### Wave 1 - Plattformkern

| Rank | Skill | Category | Bucket | Tier | Status | Warum an dieser Stelle |
|---|---|---|---|---|---|---|
| 1 | `botboy-skill-contract-author` | operational-core | internal_skills | must-have | shipped | Kanonisiert Skill-Struktur und Metadaten. Ohne das skaliert die Bibliothek nicht sauber. |
| 2 | `botboy-runtime-policy-auditor` | operational-core | internal_skills | must-have | shipped | Prueft Approval, Sandbox, Fail-Closed und Laufzeitpolitik. |
| 3 | `botboy-eval-flywheel` | operational-core | internal_skills | must-have | shipped | Macht aus Bugs und Features reproduzierbare Evals. |
| 4 | `botboy-trace-investigator` | operational-core | internal_skills | must-have | shipped | Korrelierte Analyse ueber Trace, History, Task und Principal. |
| 5 | `botboy-gateway-hardening` | operational-core | internal_skills | must-have | shipped | Haltet CLI, stdlib-Gateway und FastAPI auf denselben Sicherheits- und Vertragsgrenzen. |
| 6 | `botboy-control-center-smokes` | operational-core | internal_skills | must-have | shipped | Verhindert Payload- und UI-Drift im Operator-Hauptfenster. |
| 7 | `botboy-defensive-security-auditor` | security-isolation | public_skills | must-have | shipped | Defensive Security ist fuer Agenten kein Extra, sondern Pflicht. |
| 8 | `botboy-dense-coding-composer` | coding-engineering | public_skills | must-have | shipped | Hoher Umsetzungshebel bei kleinen, praezisen Aenderungen. |
| 9 | `botboy-server-fabric-engine` | infra-systems | public_skills | must-have | shipped | Kernskill fuer Server, Gateways, Services und Topologie. |
| 10 | `botboy-memory-architecture-engine` | cognition-memory-reflection | public_skills | must-have | shipped | BotBoy braucht ein bewusstes Memory-Design statt zufaelliger Persistenz. |

### Wave 2 - Governance, Replay, Release

| Rank | Skill | Category | Bucket | Tier | Status | Warum an dieser Stelle |
|---|---|---|---|---|---|---|
| 11 | `botboy-reflection-loop-director` | cognition-memory-reflection | public_skills | must-have | shipped | Verankert Review- und Selbstkorrektur-Loops. |
| 12 | `botboy-omni-skill-architect` | foundation-governance | internal_skills | must-have | shipped | Gibt dem Gesamtportfolio Taxonomie und Rollout-Disziplin. |
| 13 | `botboy-skill-evolution-forge` | foundation-governance | internal_skills | must-have | shipped | Erlaubt kontrollierte Skill-Evolution statt Skill-Wildwuchs. |
| 14 | `botboy-schema-contract-guardian` | operational-core | internal_skills | must-have | planned | Schuetzt JSON-, UI-, CLI- und MCP-Vertraege gegen Drift. |
| 15 | `botboy-approval-gate-designer` | security-isolation | internal_skills | must-have | planned | Approval-Flaechen muessen nachvollziehbar, minimal und fail-closed sein. |
| 16 | `botboy-observability-signal-fuser` | operational-core | internal_skills | must-have | planned | Vereinigt Logs, Spans, Queue-Zustaende und Policies in einem Diagnosebild. |
| 17 | `botboy-e2e-replay-smith` | operational-core | internal_skills | must-have | planned | End-to-end Replays sind der vernuenftige Gegenpol zu nur lokalen Unit-Tests. |
| 18 | `botboy-release-hardening-rig` | coding-engineering | internal_skills | must-have | planned | Macht aus einem Arbeitsbaum einen sicheren Release-Baum. |
| 19 | `botboy-incident-triage-operator` | operator-experience | internal_skills | must-have | planned | Transformiert Fehler in reproduzierbare Ursachenketten und klare Aktionen. |
| 20 | `botboy-network-resilience-mapper` | infra-systems | internal_skills | must-have | planned | Netzwerkpfade und Ausfallzonen werden fuer Agentensysteme sonst unsichtbar. |

### Wave 3 - Safety, Tasks, Handoffs

| Rank | Skill | Category | Bucket | Tier | Status | Warum an dieser Stelle |
|---|---|---|---|---|---|---|
| 21 | `botboy-secret-surface-minimizer` | security-isolation | internal_skills | must-have | planned | Reduziert Secret-Leaks, Ueberprivilegierung und Scope-Ausweitung. |
| 22 | `botboy-auth-boundary-auditor` | security-isolation | internal_skills | must-have | planned | Multi-Principal- und Tenant-Grenzen muessen explizit geprueft werden. |
| 23 | `botboy-task-decomposition-engine` | operational-core | internal_skills | must-have | planned | Gute Delegation beginnt mit sauberer Zerlegung. |
| 24 | `botboy-handoff-state-normalizer` | operational-core | internal_skills | must-have | planned | Stabile Handoffs brauchen normierte Task- und Merge-Payloads. |
| 25 | `botboy-queue-durability-engine` | operational-core | internal_skills | must-have | planned | Retry, Idempotenz und Persistenz gehoeren in einen eigenen Kernskill. |
| 26 | `botboy-workflow-state-replayer` | operational-core | internal_skills | must-have | planned | Ermoeglicht Recovery, Postmortems und deterministische Fehleranalyse. |
| 27 | `botboy-merge-resolution-adjudicator` | operational-core | internal_skills | essential | planned | Multi-Worker-Merges brauchen explizite Konfliktpolitik statt Nebenwirkung. |
| 28 | `botboy-delegation-advisor` | cognition-memory-reflection | internal_skills | essential | planned | Entscheidet, was lokal bleibt und was an Worker geht. |
| 29 | `botboy-tool-schema-normalizer` | mcp-integrations | internal_skills | must-have | planned | Reduziert Werkzeugchaos durch strikte, minimale und kompatible Toolschemata. |
| 30 | `botboy-mcp-server-factory` | mcp-integrations | internal_skills | essential | planned | Macht sichere MCP-Server reproduzierbar statt ad hoc. |

### Wave 4 - Wissen, Kontext, Modellwahl

| Rank | Skill | Category | Bucket | Tier | Status | Warum an dieser Stelle |
|---|---|---|---|---|---|---|
| 31 | `botboy-resource-index-engine` | mcp-integrations | internal_skills | essential | planned | MCP Resources und Templates muessen indexierbar und abfragbar sein. |
| 32 | `botboy-prompt-catalog-curator` | mcp-integrations | internal_skills | essential | planned | Prompts sind ein eigener Vertragsraum und gehoeren katalogisiert. |
| 33 | `botboy-knowledge-ingestion-pipeline` | knowledge-retrieval | internal_skills | essential | planned | Fuehrt Dateien, Docs, Connector-Kontext und Ereignisse sauber zusammen. |
| 34 | `botboy-retrieval-ranking-tuner` | knowledge-retrieval | internal_skills | essential | planned | Retrieval ohne Ranking-Qualitaet liefert nur teuren Lärm. |
| 35 | `botboy-context-budget-optimizer` | cognition-memory-reflection | internal_skills | essential | planned | Tokenbudget und Signal-Rausch-Verhaeltnis muessen bewusst optimiert werden. |
| 36 | `botboy-document-grounding-auditor` | knowledge-retrieval | public_skills | essential | planned | Macht Aussagen gegen Quellen und Dokumente belastbar. |
| 37 | `botboy-policy-test-forge` | security-isolation | internal_skills | essential | planned | Policies muessen als Tests vorliegen, nicht nur als Text. |
| 38 | `botboy-environment-bootstrapper` | infra-systems | internal_skills | essential | planned | Ein Agentensystem ohne reproduzierbaren Bring-up ist nicht wartbar. |
| 39 | `botboy-file-ops-safety-guard` | security-isolation | internal_skills | essential | planned | Dateizugriffe brauchen harte Sicherheits- und Scope-Regeln. |
| 40 | `botboy-model-routing-optimizer` | infra-systems | internal_skills | essential | planned | Unterschiedliche Modelle und Reasoning-Tiers muessen bewusst geroutet werden. |

### Wave 5 - Naechster Kern um den bestehenden Bestand

| Rank | Skill | Category | Bucket | Tier | Status | Warum an dieser Stelle |
|---|---|---|---|---|---|---|
| 41 | `botboy-event-signal-orchestrator` | foundation-governance | internal_skills | essential | shipped | Bindet Ausloeser und Skillaktivierung an konkrete Ereignisse. |
| 42 | `botboy-isolation-cage-designer` | security-isolation | internal_skills | essential | shipped | Isolationsdesign wird relevant, sobald BotBoy mehr externe Flaechen oeffnet. |
| 43 | `botboy-cloud-topology-planner` | infra-systems | public_skills | essential | shipped | Cloud- und Rollout-Form fuer produktive Bereitstellung. |
| 44 | `botboy-formal-logic-engine` | logic-math-structures | public_skills | essential | shipped | Wichtig fuer Invarianten, Policies und Widerspruchspruefung. |
| 45 | `botboy-communication-doctrine` | operator-experience | public_skills | essential | shipped | Handoffs, Erklaerungen und Operator-Kommunikation muessen sauber sein. |
| 46 | `botboy-agent-identity-broker` | security-isolation | internal_skills | essential | planned | Scoped Worker-Identitaeten und Rollenmodelle fuer Subsysteme. |
| 47 | `botboy-failover-strategy-planner` | infra-systems | internal_skills | essential | planned | Definiert Fallback-Baeume und degradierte Modi. |
| 48 | `botboy-human-approval-explainer` | operator-experience | public_skills | essential | planned | Sorgt dafuer, dass Approval-Anfragen verstanden statt nur bestaetigt werden. |
| 49 | `botboy-operator-workbench-designer` | operator-experience | public_skills | essential | planned | Hebt das Control Center zu einer echten Operator-Arbeitsflaeche. |
| 50 | `botboy-third-party-mcp-vetter` | security-isolation | internal_skills | essential | planned | Prueft externe MCP-Server auf Vertrauenswuerdigkeit und Risiko. |

### Wave 6 - Operatorik, Schedules, Datasets

| Rank | Skill | Category | Bucket | Tier | Status | Warum an dieser Stelle |
|---|---|---|---|---|---|---|
| 51 | `botboy-recovery-runbook-author` | operator-experience | internal_skills | essential | planned | Wandelt Incident-Wissen in reproduzierbare Runbooks um. |
| 52 | `botboy-audit-log-presenter` | operator-experience | internal_skills | essential | planned | Macht Activity Logs, Tools und Merge-Historie lesbar. |
| 53 | `botboy-change-impact-summarizer` | operator-experience | public_skills | essential | planned | Kommuniziert Aenderungsfolgen schnell und sachlich. |
| 54 | `botboy-priority-scheduler-tuner` | operational-core | internal_skills | essential | planned | Verhindert Starvation und schlechte Work-Queue-Verteilung. |
| 55 | `botboy-deadlock-contention-analyst` | operational-core | internal_skills | essential | planned | Erkennt Blockaden, Locking und konkurrierende Pfade. |
| 56 | `botboy-artifact-lineage-curator` | operational-core | internal_skills | essential | planned | Stabile Parent-Child- und Merge-Artefaktketten. |
| 57 | `botboy-benchmark-suite-builder` | coding-engineering | internal_skills | essential | planned | Performance- und Qualitaetsbenchmarking als eigene Schicht. |
| 58 | `botboy-example-corpus-curator` | knowledge-retrieval | public_skills | essential | planned | Gute Beispielkorpora verbessern Prompts, Demos und Regressionen. |
| 59 | `botboy-scenario-dataset-curator` | knowledge-retrieval | internal_skills | essential | planned | Hegt und pflegt Szenariodatensaetze fuer Replay und Eval. |
| 60 | `botboy-connector-capability-profiler` | mcp-integrations | internal_skills | essential | planned | Bewertet Connectoren nach Berechtigungen, Risiko und Nutzen. |

### Wave 7 - Memory und Integrations-Tiefe

| Rank | Skill | Category | Bucket | Tier | Status | Warum an dieser Stelle |
|---|---|---|---|---|---|---|
| 61 | `botboy-reflection-memory-governor` | cognition-memory-reflection | internal_skills | essential | planned | Steuert, wann Reflexionen gespeichert, verdichtet oder geloescht werden. |
| 62 | `botboy-memory-retention-policer` | cognition-memory-reflection | internal_skills | essential | planned | Erzwingt TTL, Retention und Privacy-Regeln. |
| 63 | `botboy-conversation-state-weaver` | cognition-memory-reflection | internal_skills | essential | planned | Verbindet Thread-, Task- und Sitzungskontext. |
| 64 | `botboy-knowledge-graph-builder` | knowledge-retrieval | internal_skills | essential | planned | Baut strukturierte Beziehungen ueber Skills, Artefakte und Entitaeten. |
| 65 | `botboy-retrieval-eval-lab` | knowledge-retrieval | internal_skills | essential | planned | Bewertet Recall, Grounding und Retrieval-Qualitaet systematisch. |
| 66 | `botboy-third-party-api-adapter` | mcp-integrations | internal_skills | essential | planned | Holt Nicht-MCP-APIs sauber in BotBoys Vertragswelt. |
| 67 | `botboy-protocol-compatibility-lab` | mcp-integrations | internal_skills | essential | planned | Testet Protokoll- und Versionskompatibilitaet ueber Surfaces hinweg. |
| 68 | `botboy-cross-service-contract-mapper` | mcp-integrations | internal_skills | essential | planned | Uebersetzt Contract-Grenzen zwischen CLI, API, UI und Workern. |
| 69 | `botboy-migration-safety-director` | coding-engineering | internal_skills | essential | planned | Plant Migrationen, Rollbacks und Datenpfad-Sicherheit. |
| 70 | `botboy-data-shape-reconciler` | coding-engineering | internal_skills | essential | planned | Haelt Payloads und Datenformen ueber Module hinweg konsistent. |

### Wave 8 - Delivery, Kosten, Sandboxes

| Rank | Skill | Category | Bucket | Tier | Status | Warum an dieser Stelle |
|---|---|---|---|---|---|---|
| 71 | `botboy-build-pipeline-optimizer` | coding-engineering | internal_skills | essential | planned | Verbessert Test-, Build- und Packaging-Durchsatz ohne Blindflug. |
| 72 | `botboy-dependency-boundary-auditor` | coding-engineering | internal_skills | essential | planned | Begrenzt Dependency-Sprawl und Trust-Leaks. |
| 73 | `botboy-storage-lifecycle-planner` | infra-systems | internal_skills | essential | planned | Regelt DB, Artefakte, Caches und Retention. |
| 74 | `botboy-cost-latency-optimizer` | infra-systems | internal_skills | essential | planned | Balanciert Nutzererlebnis, Modellkosten und Antwortzeiten. |
| 75 | `botboy-capacity-routing-planner` | infra-systems | internal_skills | essential | planned | Reagiert auf Quoten-, Last- und Kapazitaetsengpaesse. |
| 76 | `botboy-deployment-blueprint-author` | infra-systems | public_skills | essential | planned | Liefert ausrollbare Blaupausen statt nur Konzepten. |
| 77 | `botboy-disaster-recovery-drillmaster` | infra-systems | internal_skills | essential | planned | Uebt Restore, Fallback und Wiederanlauf als reale Betriebsfaehigkeit. |
| 78 | `botboy-sandbox-workload-profiler` | security-isolation | internal_skills | essential | planned | Macht Isolationstiers messbar statt nur theoretisch. |
| 79 | `botboy-adversarial-defense-lab` | coding-engineering | internal_skills | essential | shipped | Safe Adversarial Testing fuer robuste Defensive. |
| 80 | `botboy-virtual-dev-range` | coding-engineering | internal_skills | nice-to-have | shipped | Sinnvoll fuer spaetere Hochrisiko-Experimente, aber nicht Kern-v1. |

### Wave 9 - Erweiterung nach stabilem Kern

| Rank | Skill | Category | Bucket | Tier | Status | Warum an dieser Stelle |
|---|---|---|---|---|---|---|
| 81 | `botboy-automata-compiler-lab` | logic-math-structures | frozen_roadmap | nice-to-have | shipped-frozen | Wertvoll erst, wenn BotBoy staerker DSL- und State-Machine-lastig wird. |
| 82 | `botboy-multi-domain-simulator` | research-frontier | frozen_roadmap | nice-to-have | shipped-frozen | Simulation ist hebelstark, aber zu breit fuer den Kern. |
| 83 | `botboy-agent-cloning-orchestrator` | cognition-memory-reflection | internal_skills | essential | planned | Echter Parallelbetrieb mit sauberer Ownership und Rueckfuehrung. |
| 84 | `botboy-web-automation-broker` | mcp-integrations | public_skills | nice-to-have | planned | Einheitliche Browser- und Web-Automation ueber verschiedene Backends. |
| 85 | `botboy-a2a-interop-pilot` | mcp-integrations | internal_skills | nice-to-have | planned | Bounded Agent-to-Agent-Kommunikation ohne wilde Kopplung. |
| 86 | `botboy-crypto-protocol-lab` | security-isolation | frozen_roadmap | nice-to-have | shipped-frozen | Erst relevant, wenn BotBoy mehr kryptografische Vertrauenspfade braucht. |
| 87 | `botboy-counterexample-miner` | logic-math-structures | internal_skills | nice-to-have | planned | Findet kleine, harte Gegenbeispiele gegen Regeln und Invarianten. |
| 88 | `botboy-rule-exception-cartographer` | logic-math-structures | internal_skills | nice-to-have | planned | Kartiert Ausnahmezonen in Policies und Routinglogik. |
| 89 | `botboy-test-dataset-synthesizer` | knowledge-retrieval | internal_skills | nice-to-have | planned | Baut Eval-Daten aus Traces, Spezifikationen und Failure-Faellen. |
| 90 | `botboy-synthetic-world-modeler` | research-frontier | frozen_roadmap | nice-to-have | planned | Simulierte Testwelten fuer Policies und autonome Loops. |

### Wave 10 - Frontier und Forschung

| Rank | Skill | Category | Bucket | Tier | Status | Warum an dieser Stelle |
|---|---|---|---|---|---|---|
| 91 | `botboy-isomorphism-invariance-mapper` | logic-math-structures | frozen_roadmap | nice-to-have | shipped-frozen | Reizvoll, aber weit weg vom aktuellen Produktionskern. |
| 92 | `botboy-physics-math-solver` | research-frontier | frozen_roadmap | nice-to-have | planned | Spezifisch fuer spaetere mathematisch-physische Problemklassen. |
| 93 | `botboy-fractal-pattern-lab` | research-frontier | frozen_roadmap | nice-to-have | planned | Differenzierung, aber kein Kernskill fuer BotBoy v1. |
| 94 | `botboy-evolutionary-design-lab` | research-frontier | frozen_roadmap | nice-to-have | planned | Such- und Evolutionsverfahren erst bei reiferem System sinnvoll. |
| 95 | `botboy-radio-spectrum-lab` | research-frontier | frozen_roadmap | nice-to-have | planned | Nur fuer spezielle spaetere Simulations- oder Analysepfade relevant. |
| 96 | `botboy-orbital-sensor-simulator` | research-frontier | frozen_roadmap | nice-to-have | planned | Zu spezialisiert fuer den Kern, spaeter denkbar. |
| 97 | `botboy-holographic-ui-lab` | research-frontier | frozen_roadmap | nice-to-have | planned | UI-Forschung, nicht Produktkern. |
| 98 | `botboy-cross-modal-mapper` | research-frontier | frozen_roadmap | nice-to-have | shipped-frozen | Kreative Differenzierung, aber nicht fuer die Hauptreife noetig. |
| 99 | `botboy-autonomy-boundary-lab` | research-frontier | frozen_roadmap | nice-to-have | planned | Erforscht sichere Grenzen von Agent-Autonomie. |
| 100 | `botboy-self-improvement-safety-lab` | research-frontier | frozen_roadmap | nice-to-have | planned | Behandelt Meta-Lernen und Selbstmodifikation erst nach harter Kernreife. |

## Empfohlene Implementierungsreihenfolge

### Phase A - Kern absichern

Ranks `1-20`.

Ziel:

- Vertragsstabilitaet
- Policies
- Traces
- Evals
- Releasefaehigkeit
- Incident-Diagnose

### Phase B - Task- und Workflow-Tiefgang

Ranks `21-40`.

Ziel:

- Handoffs
- Queues
- State Replay
- MCP-Vertragsraum
- Grounding
- Modell- und Kontextsteuerung

### Phase C - Operator- und Integrationsreife

Ranks `41-60`.

Ziel:

- Approval-Erklaerbarkeit
- Operator-Workbench
- Recovery-Runbooks
- Datasets
- Connector-Bewertung

### Phase D - Memory- und Delivery-Hardening

Ranks `61-80`.

Ziel:

- Retention und Governance
- Cross-Service-Kompatibilitaet
- Migration
- Kosten, Kapazitaet, Deployments
- Sandboxes unter realer Last

### Phase E - Erweiterung und Frontier

Ranks `81-100`.

Ziel:

- sichere Parallelisierung
- A2A
- fortgeschrittene Simulation
- Forschung
- Differenzierung ohne Kernrisiko

## Konkrete Baufolge ab heutigem Stand

Von den Top-100 sind bereits `25` als Skillordner vorhanden. Die naechsten `12`, die BotBoy den groessten praktischen Hebel geben, sind:

1. `botboy-schema-contract-guardian`
2. `botboy-approval-gate-designer`
3. `botboy-observability-signal-fuser`
4. `botboy-e2e-replay-smith`
5. `botboy-release-hardening-rig`
6. `botboy-incident-triage-operator`
7. `botboy-network-resilience-mapper`
8. `botboy-secret-surface-minimizer`
9. `botboy-auth-boundary-auditor`
10. `botboy-task-decomposition-engine`
11. `botboy-handoff-state-normalizer`
12. `botboy-queue-durability-engine`

## Portfolio-Fazit

Die richtige Zielgroesse fuer BotBoy ist nicht "moeglichst viele" Skills, sondern:

- ein kleiner, klarer oeffentlicher Satz
- ein tiefer interner Satz fuer Safety, Quality und Betrieb
- ein bewusst eingefrorener Forschungsrand

Der aktuelle harte Schnitt in `public_skills`, `internal_skills` und `frozen_roadmap` bildet genau diese Logik ab.
