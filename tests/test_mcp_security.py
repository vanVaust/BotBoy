from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import botboy_mcp_server as mcp


class MCPSecurityTest(unittest.TestCase):
    def test_surface_sets_are_disjoint_and_cover_all_tools(self) -> None:
        self.assertFalse(mcp._MCP_WRITE_TOOLS.intersection(mcp._MCP_READ_TOOLS))
        self.assertEqual(mcp._MCP_WRITE_TOOLS.union(mcp._MCP_READ_TOOLS), mcp._MCP_TOOL_NAMES)

    def test_tools_with_contracts_use_canonical_mcp_tool_names(self) -> None:
        tools = mcp._tools_with_contracts()
        by_name = {tool["name"]: tool for tool in tools}

        self.assertEqual(set(by_name), mcp._MCP_TOOL_NAMES)
        for name, item in by_name.items():
            contract = item.get("botboyContract") or {}
            metadata = contract.get("metadata") or {}
            self.assertEqual(contract.get("name"), name)
            self.assertEqual(metadata.get("tool_name"), name)
            self.assertEqual(bool(metadata.get("tool_write")), name in mcp._MCP_WRITE_TOOLS)

    def test_approval_required_tools_are_derived_from_contract_catalog(self) -> None:
        catalog = mcp._mcp_contract_catalog()
        expected = {name for name, contract in catalog.items() if contract.get("needs_approval")}
        self.assertEqual(mcp._approval_required_tools(), expected)
        self.assertIn("botboy_weather", expected)
        self.assertNotIn("botboy_system_info", expected)

    def test_approval_aliases_do_not_bypass_contract_gate(self) -> None:
        with patch("botboy_mcp_server._get_bot", side_effect=AssertionError("bot should not initialize")) as get_bot:
            result = mcp._handle_tool("botboy_execute", {"command": "status", "allow": True})

        self.assertTrue(result["isError"])
        self.assertIn("Approval required", result["content"][0]["text"])
        get_bot.assert_not_called()

    def test_risky_tool_requires_approval_before_bot_initialization(self) -> None:
        with patch("botboy_mcp_server._get_bot", side_effect=AssertionError("bot should not initialize")) as get_bot:
            result = mcp._handle_tool("botboy_schedule_add", {"name": "nightly", "schedule": "in 1h"})

        self.assertTrue(result["isError"])
        self.assertIn("Approval required", result["content"][0]["text"])
        get_bot.assert_not_called()

    def test_universal_execute_requires_approval_before_bot_initialization(self) -> None:
        with patch("botboy_mcp_server._get_bot", side_effect=AssertionError("bot should not initialize")) as get_bot:
            result = mcp._handle_tool("botboy_execute", {"command": "status"})

        self.assertTrue(result["isError"])
        self.assertIn("Approval required", result["content"][0]["text"])
        get_bot.assert_not_called()

    def test_contract_required_tools_publish_approval_argument(self) -> None:
        tools = mcp._tools_with_contracts()
        by_name = {tool["name"]: tool for tool in tools}
        self.assertIn("approval", by_name["botboy_execute"]["inputSchema"]["properties"])
        self.assertIn("approval", by_name["botboy_weather"]["inputSchema"]["properties"])
        self.assertNotIn("approval", by_name["botboy_system_info"]["inputSchema"]["properties"])

    def test_http_remote_bind_requires_token(self) -> None:
        with patch.dict(
            os.environ,
            {
                "BOTBOY_MCP_HTTP_TOKEN": "",
                "BOTBOY_ALLOW_INSECURE_REMOTE": "",
                "BOTBOY_MCP_ALLOW_INSECURE_REMOTE": "",
            },
        ):
            with self.assertRaisesRegex(RuntimeError, "auth disabled"):
                mcp.MCPHTTPServer(host="0.0.0.0", port=8766)

    def test_http_remote_bind_allows_configured_token(self) -> None:
        with patch.dict(
            os.environ,
            {
                "BOTBOY_MCP_HTTP_TOKEN": "token-123",
                "BOTBOY_ALLOW_INSECURE_REMOTE": "",
                "BOTBOY_MCP_ALLOW_INSECURE_REMOTE": "",
            },
        ):
            server = mcp.MCPHTTPServer(host="0.0.0.0", port=8766)

        self.assertEqual(server.token, "token-123")


if __name__ == "__main__":
    unittest.main()
