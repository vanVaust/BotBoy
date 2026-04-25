# Eval Loop

Use this loop for every BotBoy quality gap:

1. Reproduce the bug or behavior gap.
2. Decide whether the gap belongs in the manifest, the replay seed, or both.
3. Add the smallest durable artifact that expresses the gap.
4. Run the eval baseline.
5. Save or inspect a report artifact when comparison matters.
6. Keep the case after the fix lands.

Map artifacts onto the current repo:

- manifest: `tests/evals/wave_1_manifest.json`
- replay seed: `tests/evals/replays/wave_1_seed.jsonl`
- runner: `botboy/evals.py`
- CLI entry: `botboy evals`
