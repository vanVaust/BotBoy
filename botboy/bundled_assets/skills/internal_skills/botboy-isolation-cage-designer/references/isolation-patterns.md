# Isolation Patterns

Use these patterns:

1. deny by default
2. explicit allowlist for network and filesystem access
3. stronger isolation for higher-risk work
4. no silent fallback when an isolation tier is missing
5. separate trusted local execution from untrusted execution paths

The goal is not maximal restriction.
The goal is the smallest safe execution cage that still lets the task succeed.
