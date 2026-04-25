---
name: botboy-cross-modal-mapper
description: Map BotBoy ideas, structures, and outputs across text, sound, image, symbols, and layout in safe creative and analytical ways. Use when BotBoy needs cross-modal conversion, synesthetic mapping, presentation design, or a structured way to move information between media without losing meaning.
---

# Quick Start

Map meaning, not decoration.

- Identify the source modality and the target modality.
- Keep the underlying structure stable across the conversion.
- Prefer explicit mapping rules over vague aesthetic intuition.
- Keep the output useful for analysis or communication.

# Workflow

Follow this order:

1. Read `references/modality-map.md`.
2. Identify the source and target modalities.
3. Decide what structure must survive the mapping.
4. Define the translation rules or correspondences.
5. Add a small verification that the mapping preserves intent.

# Rules

Prefer:

- traceable correspondences
- one source concept to one target representation when possible
- safe creative transformation over random remixing
- output that remains understandable to humans and agents

# Resources

- Read `references/modality-map.md` before creating a mapping.
- Read `references/translation-rules.md` when the output needs consistent conversion behavior.
- Run `scripts/modality_bridge.py <text>` to generate a simple modality map from a text prompt.
