# Smoke Plan

Use this escalation path:

1. validate saved payload shape
2. validate render markers in `web/index.html`
3. validate the specific cards touched by the change
4. add a browser or screenshot smoke only when payload and markup checks are no longer enough

Prefer robust operational checks over brittle pixel-matching.
If readability changes are intentional, update the test language so it still guards the right operational truth.
