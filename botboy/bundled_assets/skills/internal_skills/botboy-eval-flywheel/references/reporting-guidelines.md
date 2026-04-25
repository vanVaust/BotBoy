# Reporting Guidelines

When summarizing eval output:

1. Name the manifest and seed used.
2. State total cases, passes, and failures.
3. Explain which product risk each failure represents.
4. Call out whether the failure is a missing expectation, product bug, or environment gap.
5. Prefer short diff-style observations over raw log dumps.

If you change report shape, preserve stable keys so downstream comparison stays cheap.
