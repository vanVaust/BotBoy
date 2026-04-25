# BotBoy Top-12 Skill Build Plan

## Ziel

Die naechsten 12 priorisierten Skills als echte Skillordner in den Finalbaum bringen und sauber in Registry, Portfolio und Loader integrieren.

## Ziel-Skills

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

## Ownership

### Paket A - Contracts, Approval, Observability, Replay

- `botboy-schema-contract-guardian`
- `botboy-approval-gate-designer`
- `botboy-observability-signal-fuser`
- `botboy-e2e-replay-smith`

### Paket B - Release, Incident, Network, Secret

- `botboy-release-hardening-rig`
- `botboy-incident-triage-operator`
- `botboy-network-resilience-mapper`
- `botboy-secret-surface-minimizer`

### Paket C - Auth, Decomposition, Handoff, Queue

- `botboy-auth-boundary-auditor`
- `botboy-task-decomposition-engine`
- `botboy-handoff-state-normalizer`
- `botboy-queue-durability-engine`

## Integrationsarbeit

- Skillordner unter `skills/internal_skills/`
- `agents/openai.yaml` pro Skill
- knappe `references/` pro Skill
- kleine deterministische `scripts/` nur wo sinnvoll
- Registry auf 37 Skills erweitern
- Portfolio-Dokument auf neuen Implementierungsstand anheben
- Loader-Smokes und Skill-Smokes verifizieren

## Risiken

- Skill-Wildwuchs: gleiche Sprache und gleiche Ordnerstruktur erzwingen
- doppelte Trigger: Trigger bewusst disjunkt halten
- uferlose Referenzen: nur kleine, operative Referenzen anlegen
- zu viele Scripts: nur wiederverwendbare oder deterministische Hilfen bauen

## Erfolgsbedingungen

- 12 neue Skills im Finalbaum
- 37 Skills im Registry-Summary
- rekursiver Loader findet alle Skills
- jedes Skillpaket hat konsistente `SKILL.md`-, `agents/`-, `references/`- und ggf. `scripts/`-Artefakte
