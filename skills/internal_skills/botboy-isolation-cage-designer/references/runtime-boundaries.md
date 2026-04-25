# Runtime Boundaries

Anchor isolation decisions to BotBoy surfaces:

- `botboy/skills/runtime.py`
- `botboy/security/sandbox.py`
- `botboy/skills/manager.py`
- `botboy/__main__.py`
- `botboy/gateway/server.py`

Check:

1. what is trusted
2. what needs I/O
3. what requires approval
4. what must fail closed if the preferred cage is unavailable
