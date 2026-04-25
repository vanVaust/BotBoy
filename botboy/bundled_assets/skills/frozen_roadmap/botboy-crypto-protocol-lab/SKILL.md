---
name: botboy-crypto-protocol-lab
description: Design, review, and harden safe cryptographic protocol and trust workflows for BotBoy, including secret handling, token flows, replay protection, key rotation, and message integrity. Use when improving BotBoy auth or service-to-service trust, auditing protocol surfaces, or turning a security requirement into a safe, testable crypto design.
---

# Quick Start

- Start from the trust model and threat boundaries.
- Map secrets, tokens, signatures, and replay risks before implementation.
- Prefer review, hardening, and regression design over new primitives.
- Keep the protocol small enough to test end to end.

# Workflow

1. Read `references/trust-model.md`.
2. Read `references/botboy-touchpoints.md`.
3. Identify the BotBoy component that owns the trust boundary.
4. Specify the minimum safe protocol behavior.
5. Add or adjust tests for integrity, rotation, or replay behavior.

# Rules

- Use existing platform primitives first.
- Fail closed when trust material is missing or inconsistent.
- Prefer explicit scope and rotation over hidden long-lived credentials.

# Resources

- Read `references/trust-model.md` for the protocol checklist.
- Read `references/botboy-touchpoints.md` to map the crypto concern onto the repo.
- Run `scripts/crypto_surface_map.py <repo-root>` to summarize likely crypto-sensitive files.
