from __future__ import annotations

import unittest

from tests.contract_canon_support import (
    ROOT,
    extract_cli_commands,
    extract_fastapi_http_routes,
    extract_fastapi_websocket_routes,
    extract_mcp_tools,
    extract_stdlib_http_routes,
    extract_stdlib_http_routes_with_handlers,
    load_contract_canon,
)


class ContractCanonDriftTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.canon = load_contract_canon()

    def test_cli_surface_matches_contract_canon(self) -> None:
        self.assertEqual(
            extract_cli_commands(),
            self.canon["surfaces"]["cli"]["commands"],
        )

    def test_mcp_surface_matches_contract_canon(self) -> None:
        self.assertEqual(
            extract_mcp_tools(),
            self.canon["surfaces"]["mcp"]["tools"],
        )

    def test_fastapi_surface_matches_contract_canon(self) -> None:
        self.assertEqual(
            extract_fastapi_http_routes(),
            self.canon["surfaces"]["fastapi"]["http_routes"],
        )
        self.assertEqual(
            extract_fastapi_websocket_routes(),
            self.canon["surfaces"]["fastapi"]["websocket_routes"],
        )

    def test_stdlib_surface_matches_contract_canon(self) -> None:
        self.assertEqual(
            extract_stdlib_http_routes(),
            self.canon["surfaces"]["stdlib"]["http_routes"],
        )
        self.assertEqual(
            extract_stdlib_http_routes_with_handlers(),
            self.canon["surfaces"]["stdlib"]["route_handlers"],
        )

    def test_stdlib_static_assets_from_canon_exist(self) -> None:
        for asset_path in self.canon["surfaces"]["stdlib"]["static_asset_paths"]:
            asset = ROOT / "botboy" / "web" / asset_path.lstrip("/")
            self.assertTrue(asset.is_file(), str(asset))


if __name__ == "__main__":
    unittest.main()
