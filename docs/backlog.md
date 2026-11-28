# Rückstand

**Stand:** 18.10.2026 · Dieser Rückstand ersetzt die Vorhabenlisten der zehn
Dokumente in `docs/archiv/`. Gültige Architektur und Reihenfolge: `ARCHITEKTUR.md`.

## Herkunft und Grenze dieser Liste

Zwei Quellen, getrennt gehalten:

1. **Abschnitt A** stammt aus dem Architektur- und Funktionskonzept vom 18.10.2026.
   Die Einträge sind am Code gemessen und tragen eine maschinell prüfbare Abnahme.
2. **Abschnitt B** ist die mechanische Übernahme aller 134 offenen Kästchen aus
   `docs/archiv/BOTBOY_V2_V7_ROADMAP.md`. Keines war als erledigt markiert, der
   Zustand wurde dort also nie gepflegt — **offen bedeutet hier nicht ungebaut.**
   Mehrere dieser Punkte sind inzwischen umgesetzt. Jeder Punkt ist vor der
   Bearbeitung gegen den Code zu prüfen.

**Grenze:** Die anderen neun Archivdokumente sind Prosa ohne Statusmarker. Aus
ihnen wurde nichts mechanisch übernommen; sie können weitere offene Vorhaben
enthalten.

---

## A — Aufräumen und Fertigstellen (gemessen, mit Abnahme)

Vollständige Beschreibung samt Begründung, Trade-offs und Abnahmekriterien in
`ARCHITEKTUR.md`, Kapitel 6 bis 8.

### Aufräumen

- [x] **R1** Abhängigkeitsliste auf die Wirklichkeit bringen
- [x] **R2** Doppelten Skill-Baum `bundled_assets` entfernen
- [x] **R3** `scratch_test.py` entfernen
- [x] **R4** Dokumente konsolidieren
- [ ] **R5** Ein Gateway: elf Sicherheitstestmodule auf den Standardbibliotheks-Pfad portieren, dann den FastAPI-Pfad entfernen (Aufwand L, Risiko hoch)
- [ ] **R6** Entscheidung über die ungeprüften Randmodule: prüfen oder entfernen (Aufwand M)
- [ ] **R7** Modulumzug in acht Pakete, Importschranken maschinell erzwingen (Aufwand L)
- [ ] **R8** Neun Datenbanken zu einer zusammenlegen, ein Schema, ein Migrationslauf (Aufwand L) — **Voraussetzung für F2**
- [ ] **R9** Skills auf Stufe 1 und 2 bringen, null Skripte im ausgelieferten Paket (Aufwand L)

### Kern

- [ ] **F1** `policy.py` zum einzigen Entscheidungspunkt ausbauen: Grundhaltung Ablehnen, argumentabhängiges Risiko, Verbotsvorrang, monotone Laufmarkierungen. Ziel 95 % Abdeckung mit Zweigen (heute 0 %)
- [ ] **F1b** Freigaben argumentgebunden, einmalig, befristet
- [ ] **F1c** Sichere Vorgaben: `enable_auth` auf `True`, lokale Bindung, Starterweigerung, Vorgabetest
- [ ] **F1d** Idempotenzschlüssel je Schritt, damit Fortsetzen genau einmal wirkt
- [ ] **F1e** Kosten- und Aufrufbudgets je Mandant und Zeitfenster
- [ ] **F1f** Halt-Schalter, der alle Läufe sofort anhält
- [ ] **F1g** Erlaubnisliste für ausgehende Ziele, Vorgabe leer
- [ ] **F2** Nachweiskette: Audit mit Hash-Verkettung, geschlüsselte Commitments, löschbare Nutzdaten, externer Anker

### Arbeit

- [ ] **FW1** Aufgabenmodule auf fachlichen Schnitt bringen, je unter 600 Zeilen
- [ ] **FW2** `gateway/queue_security.py` von 46 % auf mindestens 85 %, optimistische Nebenläufigkeit über Versionsspalte
- [ ] **FW3** Arbeiter und Übergabe umziehen
- [ ] **FW4** Zeitplan speichert Argument-Commitments statt roher Argumente

### Wissen und Lernen

- [ ] **F3** Gedächtnis: bi-temporale Fakten auf FTS5, Goldset mit Recall-Messung vor jedem Umbau
- [ ] **F3b** Deutscher Normalisierer vor der Indizierung (FTS5 hat keinen deutschen Stemmer)
- [ ] **F5** Lernschleife: Vorschlag, Wirkungsmessung per Replay, Übernahme nur durch Menschen
- [ ] **F5b** Replay-Prüfstand vollenden (`security/replay_harness.py` 62 %, `replay_diff.py` 85 %)
- [ ] **F5c** Lektionen mit Herkunft, Verfall, Treffer- und Fehlzählern

### Fähigkeiten

- [ ] **F4** MCP-Server gegen Revision 2026-07-28 prüfen: Prüfmatrix über alle MUSS-Aussagen
- [ ] **F4b** MCP-Client härten: `iss` prüfen, Pinning bei jedem Aufruf, `sampling` von Fremdservern fail closed ablehnen
- [ ] **F6** Modellauswahl mit Ausweichpfad, Zeitgrenzen, Kostenzuordnung je Lauf

### Oberfläche

- [ ] **F7a** CLI entflechten, `__main__.py` unter die Zeilengrenze bringen, mindestens 70 % Abdeckung
- [ ] **F7b** Server-Sent Events statt WebSocket — **vorher klären, ob die Weboberfläche die Rückrichtung braucht**
- [ ] **F7c** Freigabeweg Ende zu Ende: Anfrage erreicht den Menschen, Entscheidung kehrt zurück
- [ ] **F7d** Kennzeichnung maschineller Herkunft in allen Kanälen

### Veröffentlichung und Lieferkette

- [ ] **V1** Lizenz entscheiden — **das Repo ist öffentlich und hat keine; damit sind alle Rechte vorbehalten**
- [ ] **V2** Vollständige Git-Historie auf Geheimnisse prüfen, Treffer rotieren statt löschen
- [ ] **V3** `SECURITY.md` und privaten Meldeweg anlegen
- [ ] **V4** GitHub-Aktionen auf Commit-Hash festlegen statt auf Tag
- [ ] **V5** Abdeckungstor je Zone in der Integration erzwingen
- [ ] **V6** Verteilungsname klären (`botboy` ist auf PyPI fremdbelegt)
- [ ] **V7** Markenprüfung
- [ ] **V8** `CHANGELOG.md` fortschreiben
- [ ] **V9** macOS in die Prüfmatrix, FTS5-Erkennung beim Start

### Messungen

- [ ] **M1** Zweigabdeckung für `kernel/`, nicht nur Anweisungen
- [ ] **M2** Mutationsprüfung der Tests für Entscheidung, Freigabe, Nachweis (Ziel 90 % getötete Mutanten)
- [ ] **M3** Suite unter Windows mit Python 3.10 lokal
- [ ] **M4** Vollständige Historie auf Geheimnisse
- [ ] **M5** Vollaufzählung der Effektsequenzen gegen einen bösartigen Doppelgänger
- [ ] **M6** Goldset für das Gedächtnis
- [ ] **M7** Falschablehnungsrate vor und nach F1
- [ ] **M8** Dauer der Kettenprüfung bei realistischer Protokollgröße

### Kleinkram aus dem Aufräumen

- [ ] `resources.bundled_assets_dir()` umbenennen (sechs Aufrufstellen, zwei Tests) — gehört zu R7
- [ ] `pyyaml` aus `install_requires` in eine Zusatzgruppe verschieben; die drei Aufrufstellen müssen den Fehlerfall sauber behandeln
- [ ] `starlette` wird in `tests/test_websocket_tenant_integration.py` direkt importiert, ist aber nirgends deklariert (kommt transitiv über FastAPI) — entfällt mit R5

---

## B — Mechanische Übernahme aus BOTBOY_V2_V7_ROADMAP.md

**134 Einträge, Zustand ungepflegt.** Vor der Bearbeitung jeden Punkt gegen den
Code prüfen: mehrere sind erkennbar umgesetzt, zum Beispiel strukturierte
Beobachtbarkeit, Mandantentrennung und Teile der Härtung des Gateways.

### Major Capability Tracks  (60)
- [ ] Remote worker execution model with secure registration and capability discovery
- [ ] Durable task queue and worker leases across hosts
- [ ] Artifact store abstraction for local and remote execution outputs
- [ ] Stronger auth: roles, scoped tokens, machine principals, policy-bound API keys
- [ ] Structured observability: metrics, logs, traces, service health, event taxonomy
- [ ] Versioned skill and plugin packaging with compatibility metadata
- [ ] Runtime upgrade and migration workflow for config, task store, and bundles
- [ ] Environment profiles: local, team, production
- [ ] Control center ops surfaces for workers, queues, incidents, upgrades, and drift
- [ ] Hard acceptance suite for install, gateway parity, security, and operator workflows
- [ ] Mission graph / workflow IR for decomposition, planning, checkpoints, and rollback
- [ ] Policy engine for delegation, merge policy, budget limits, confidence thresholds, and escalation
- [ ] Planner-critic-executor orchestration loops with bounded retries
- [ ] Memory stratification: session memory, project memory, skill memory, operational memory
- [ ] Knowledge graph / dependency graph linking tasks, artifacts, decisions, users, and skills
- [ ] Cost and latency optimization engine for routing decisions
- [ ] Replay and simulation harness for orchestration strategy testing
- [ ] Skill composition model with typed inputs/outputs and contract validation
- [ ] Delegation advisor becomes execution policy engine
- [ ] Intelligent operator review queues with prioritized intervention points
- [ ] Autonomy envelopes: allowed action ranges, escalation thresholds, human hold points
- [ ] Formal policy runtime with deny/allow/require-review semantics
- [ ] Budget, risk, and confidence accounting for multi-step missions
- [ ] Sandboxed execution tiers by risk class
- [ ] Incident model: pause, quarantine, rollback, recover, explain
- [ ] Rich approval workflows: per action, per mission, per domain, per org policy
- [ ] Compliance trails and immutable decision logs
- [ ] Multi-mission scheduler with deadlines, SLAs, and priority classes
- [ ] Goal-state evaluation: done, partially done, blocked, unsafe, degraded
- [ ] Recovery playbooks and supervised self-healing for known classes of failure
- [ ] Multi-tenant org model with teams, projects, environments, quotas, and cost centers
- [ ] Shared capability fabric: skill registry, execution profiles, policy packs, mission templates
- [ ] Cross-project knowledge and memory federation with boundaries
- [ ] Governance inheritance: org, team, project, mission
- [ ] Fleet management for workers, sandboxes, artifact backends, and policy bundles
- [ ] Marketplace or internal exchange for approved skills and workflows
- [ ] Billing, quotas, and usage accounting surfaces
- [ ] Enterprise integrations: ticketing, knowledge, secrets, storage, messaging, identity
- [ ] Reliability engineering features: redundancy, rate governance, failover, maintenance windows
- [ ] Operator command center for organizations, not just instances
- [ ] Digital twin for workflows, resources, queues, policies, and failure modes
- [ ] Scenario simulator for mission plans, staffing, outages, dependency degradation, and budget shifts
- [ ] Strategy optimizer for cost, latency, completion quality, and intervention load
- [ ] Causal diagnostics and bottleneck analysis
- [ ] Safety scoring for proposed autonomous actions or policy changes
- [ ] Continuous policy tuning using replay and bounded optimization
- [ ] Mission portfolio planning across competing priorities
- [ ] Recommendation engine for skill investments, worker placement, and policy changes
- [ ] Capacity planning surfaces with what-if analysis
- [ ] Simulation-backed change approval workflow
- [ ] Unified mission kernel: identity, policy, execution, memory, artifacts, simulation, audit
- [ ] Universal capability graph spanning skills, tools, services, data domains, and policies
- [ ] Domain-specific mission languages and compiled execution plans
- [ ] Portable execution substrate for local, private cloud, and regulated environments
- [ ] First-class governance operating plane with formal controls and review automation
- [ ] Closed-loop improvement pipeline for approved skills, templates, and policies
- [ ] Cross-domain operator copilots for planning, incident response, and architecture changes
- [ ] Mission design studio with live validation, policy linting, simulation preview, and deployment gates
- [ ] Federated memory and knowledge with explicit jurisdiction and trust semantics
- [ ] Platform APIs for third-party mission apps and domain-specific operator consoles

### V2 UX Requirements  (4)
- [ ] one-screen incident understanding
- [ ] remote worker health and lease visibility
- [ ] install, upgrade, and environment profile flows without shell expertise
- [ ] plain-language explanations for auth, skill compatibility, and artifact failures

### V3 UX Requirements  (4)
- [ ] show route choice, not just route result
- [ ] visible merge policy and delegation reasoning
- [ ] intervention queue sorted by impact, urgency, and confidence
- [ ] trace replay understandable by non-developers

### V4 UX Requirements  (4)
- [ ] autonomy envelope visible before mission launch
- [ ] explicit approval design with safe defaults
- [ ] mission rollback and quarantine must be one action away
- [ ] policy/risk/cost state summarized without losing drill-down depth

### V5 UX Requirements  (4)
- [ ] org, team, and project boundaries always obvious
- [ ] policy inheritance visible and inspectable
- [ ] cross-tenant surfaces impossible to misread
- [ ] billing, usage, and reliability shown as operational levers, not finance-only reports

### V6 UX Requirements  (4)
- [ ] simulations distinguish real vs predicted vs synthetic data clearly
- [ ] recommendations include rationale and confidence
- [ ] what-if analysis feels like choosing scenarios, not operating a statistics tool
- [ ] optimization suggestions never auto-hide risk

### V7 UX Requirements  (4)
- [ ] one shell can represent outcome, workflow, system, policy, and simulation without fragmentation
- [ ] mode-switching is projection-switching, not context loss
- [ ] complex system edits are previewable as mission diffs and policy diffs
- [ ] domain apps can sit on the same kernel without visual incoherence

### V2  (10)
- [ ] remote workers
- [ ] durable multi-host tasking
- [ ] artifact abstraction
- [ ] stronger auth and principals
- [ ] structured observability
- [ ] versioned skills/plugins
- [ ] migrations and upgrade tooling
- [ ] deployment profiles
- [ ] ops control center
- [ ] hard acceptance suite

### V3  (9)
- [ ] workflow graph
- [ ] policy engine
- [ ] planner-critic-executor loops
- [ ] layered memory
- [ ] knowledge graph
- [ ] cost/latency routing
- [ ] replay/simulation harness
- [ ] typed skill contracts
- [ ] intelligent intervention queue

### V4  (9)
- [ ] autonomy envelopes
- [ ] formal policy runtime
- [ ] budget/risk accounting
- [ ] sandbox tiers
- [ ] incident model
- [ ] approval workflows
- [ ] compliance trails
- [ ] SLA scheduler
- [ ] recovery playbooks

### V5  (7)
- [ ] org/team/project tenancy
- [ ] governance inheritance
- [ ] shared registries and templates
- [ ] fleet management
- [ ] enterprise integrations
- [ ] billing and quotas
- [ ] organization command center

### V6  (7)
- [ ] digital twin
- [ ] scenario simulator
- [ ] strategy optimizer
- [ ] causal diagnostics
- [ ] policy tuning via replay
- [ ] capacity planning
- [ ] simulation-backed approvals

### V7  (8)
- [ ] mission kernel unification
- [ ] capability graph
- [ ] mission language
- [ ] mission studio
- [ ] federated operating plane
- [ ] domain copilots
- [ ] external platform APIs
- [ ] final BotBoy OS shell

<!-- gesamt 134 -->
