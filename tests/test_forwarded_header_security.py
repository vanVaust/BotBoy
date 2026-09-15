import asyncio
import os
import unittest

import botboy.gateway.security  # noqa: F401
from fastapi.middleware.cors import CORSMiddleware


class ForwardedHeaderSecurityTests(unittest.TestCase):
    def _captured_headers(self, *, peer: str, trusted: str = "") -> list[tuple[bytes, bytes]]:
        previous = os.environ.get("BOTBOY_TRUSTED_PROXIES")
        try:
            if trusted:
                os.environ["BOTBOY_TRUSTED_PROXIES"] = trusted
            else:
                os.environ.pop("BOTBOY_TRUSTED_PROXIES", None)

            captured: list[tuple[bytes, bytes]] = []

            async def app(scope, receive, send):
                captured.extend(scope.get("headers", []))

            middleware = CORSMiddleware(app=app, allow_origins=[])
            scope = {
                "type": "http",
                "method": "GET",
                "headers": [
                    (b"host", b"localhost"),
                    (b"x-forwarded-for", b"203.0.113.99"),
                    (b"x-real-ip", b"203.0.113.99"),
                    (b"forwarded", b"for=203.0.113.99"),
                ],
                "client": (peer, 12345),
            }
            asyncio.run(middleware(scope, lambda: None, lambda message: None))
            return captured
        finally:
            if previous is None:
                os.environ.pop("BOTBOY_TRUSTED_PROXIES", None)
            else:
                os.environ["BOTBOY_TRUSTED_PROXIES"] = previous

    def test_untrusted_peer_cannot_supply_forwarded_identity(self):
        headers = self._captured_headers(peer="198.51.100.20")
        names = {name for name, _ in headers}
        self.assertNotIn(b"x-forwarded-for", names)
        self.assertNotIn(b"x-real-ip", names)
        self.assertNotIn(b"forwarded", names)
        self.assertIn(b"host", names)

    def test_configured_proxy_may_supply_forwarded_identity(self):
        headers = self._captured_headers(peer="127.0.0.1", trusted="127.0.0.1/32")
        names = {name for name, _ in headers}
        self.assertIn(b"x-forwarded-for", names)
        self.assertIn(b"x-real-ip", names)
        self.assertIn(b"forwarded", names)


if __name__ == "__main__":
    unittest.main()
