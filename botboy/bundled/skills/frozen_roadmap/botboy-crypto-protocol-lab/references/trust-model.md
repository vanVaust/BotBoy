# Trust Model

Treat cryptography as a trust-boundary problem first.

- Identify the principal that owns each credential or token.
- Define the smallest possible scope for each secret.
- Define where replay protection lives.
- Define rotation, revocation, and expiry behavior up front.
- Prefer authenticated transport and explicit message integrity over implicit trust.

Do not add a new protocol primitive when an existing BotBoy mechanism already establishes the boundary.
