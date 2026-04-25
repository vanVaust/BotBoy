# Parity Matrix

Use this file when both gateways should behave the same:

- auth success and failure shape
- refresh behavior
- command execution protection
- status and dashboard access rules
- principal CRUD authorization
- rate-limit enforcement
- trace and request ID propagation

If a parity gap is intentional, document the reason in the code change or test update.
If it is not intentional, fix the product or narrow the promise.
