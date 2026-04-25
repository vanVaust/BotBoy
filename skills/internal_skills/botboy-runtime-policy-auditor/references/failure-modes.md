# Failure Modes

Look for these failure modes first:

1. untrusted code enters `inprocess`
2. approval-required work is executable without explicit approval
3. Docker or isolation absence causes a silent unsafe fallback
4. the command advertises one risk profile but executes with another
5. tests assert implementation details instead of security outcomes

When you fix a failure mode, add or update a regression in:

- `tests/test_runtime_policy.py`
- `tests/test_skill_contracts.py`
- `tests/test_integration.py`
