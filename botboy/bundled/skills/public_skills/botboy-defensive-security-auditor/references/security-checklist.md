# Security Checklist

Check these in order:

1. Authentication and authorization
2. Secrets and credential handling
3. Approval gating
4. Sandbox and runtime tier
5. Filesystem and path handling
6. Rate limiting and request shaping
7. Trace, history, and audit propagation

Treat any silent fallback as suspicious until proven safe.
Treat any untrusted code path as denied by default unless the code says otherwise.
