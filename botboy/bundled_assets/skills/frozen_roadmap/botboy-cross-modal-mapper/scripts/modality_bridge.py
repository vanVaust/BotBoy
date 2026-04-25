#!/usr/bin/env python3
"""Generate a simple cross-modal map from text."""

from __future__ import annotations

import json
import sys


def classify(text: str) -> dict:
    lowered = text.lower()
    modalities = []
    if any(word in lowered for word in ("image", "visual", "diagram", "color")):
        modalities.append("image")
    if any(word in lowered for word in ("sound", "audio", "voice", "rhythm")):
        modalities.append("sound")
    if any(word in lowered for word in ("layout", "grid", "structure", "spacing")):
        modalities.append("layout")
    if any(word in lowered for word in ("symbol", "icon", "glyph", "mark")):
        modalities.append("symbol")
    if not modalities:
        modalities.append("text")
    return {
        "input": text,
        "modalities": modalities,
        "mapping_hint": "preserve structure and emphasis",
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: modality_bridge.py <text>", file=sys.stderr)
        return 2
    print(json.dumps(classify(" ".join(argv[1:])), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
