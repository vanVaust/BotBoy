# Invariant Patterns

Use this model:

- `facts`: observed or accepted truths
- `rules`: if/then statements
- `required`: facts that must exist for a conclusion
- `contradictions`: pairs that cannot both be true

Keep each rule small.
Prefer explicit premises over hidden assumptions.

Example:

```json
{
  "facts": ["auth_enabled", "jwt_secret_set"],
  "rules": [
    { "if": ["auth_enabled"], "then": ["jwt_secret_set"] }
  ]
}
```

If a rule fails, report the missing premise instead of jumping straight to a conclusion.
