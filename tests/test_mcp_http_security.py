from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from botboy_mcp_server import MCPHTTPServer


class MCPHTTPRemoteSecurityTest(unittest.TestCase):
    def test_http_transport_rejects_nonlocal_bind_without_token(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("BOTBOY_MCP_HTTP_TOKEN", None)
            with self.assertRaises(RuntimeError) as ctx:
                MCPHTTPServer(host="0.0.0.0", port=8766)

        self.assertIn("BotBoy remote readiness: FAIL", str(ctx.exception))
        self.assertIn("Authentication must be enabled", str(ctx.exception))

    def test_http_transport_allows_nonlocal_bind_with_token(self) -> None:
        with patch.dict(os.environ, {"BOTBOY_MCP_HTTP_TOKEN": "stable-mcp-token"}):
            server = MCPHTTPServer(host="0.0.0.0", port=8766)

        self.assertEqual(server.host, "0.0.0.0")
        self.assertEqual(server.token, "stable-mcp-token")


if __name__ == "__main__":
    unittest.main()
