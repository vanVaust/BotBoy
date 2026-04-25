from __future__ import annotations

import unittest

from tests.contract_canon_support import (
    extract_fastapi_http_routes,
    extract_stdlib_http_routes,
    load_contract_canon,
)


class ContractCanonParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.canon = load_contract_canon()

    def test_fastapi_and_stdlib_shared_http_surface_is_in_parity(self) -> None:
        fastapi_only = self.canon["parity"]["fastapi_only_http_routes"]
        fastapi_http = extract_fastapi_http_routes()
        fastapi_shared = [route for route in fastapi_http if route not in fastapi_only]
        stdlib_http = extract_stdlib_http_routes()

        self.assertEqual(fastapi_shared, self.canon["parity"]["shared_http_routes"])
        self.assertEqual(stdlib_http, self.canon["parity"]["shared_http_routes"])

    def test_fastapi_only_ui_routes_match_stdlib_static_assets(self) -> None:
        fastapi_only_paths = sorted(route["path"] for route in self.canon["parity"]["fastapi_only_http_routes"])
        stdlib_static_paths = sorted(self.canon["surfaces"]["stdlib"]["static_asset_paths"])
        self.assertEqual(fastapi_only_paths, stdlib_static_paths)


if __name__ == "__main__":
    unittest.main()
