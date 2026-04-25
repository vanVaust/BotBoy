from __future__ import annotations

import unittest
from pathlib import Path

from botboy.gateway.simple_static_handlers import handle_root, serve_static


ROOT = Path(__file__).resolve().parents[1]


class _FakeStaticHandler:
    def __init__(self, web_dir: Path | None):
        self.web_dir = str(web_dir) if web_dir else None
        self.bytes_calls: list[dict[str, object]] = []
        self.json_calls: list[dict[str, object]] = []

    def _bytes(self, body: bytes, *, content_type: str, status: int = 200, headers=None) -> None:
        self.bytes_calls.append(
            {
                "body": body,
                "content_type": content_type,
                "status": status,
                "headers": headers,
            }
        )

    def _json(self, data: dict, status: int = 200) -> None:
        self.json_calls.append({"data": data, "status": status})


class SimpleStaticHandlersTest(unittest.TestCase):
    def _make_web_root(self) -> Path:
        root = (ROOT / ".botboy-runtime" / "simple-static-handlers" / self._testMethodName).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def test_handle_root_serves_index_html(self) -> None:
        web_root = self._make_web_root()
        index = web_root / "index.html"
        index.write_text("<html>botboy</html>", encoding="utf-8")
        handler = _FakeStaticHandler(web_root)

        handle_root(handler, {})

        self.assertEqual(len(handler.bytes_calls), 1)
        self.assertEqual(handler.bytes_calls[0]["content_type"], "text/html")
        self.assertEqual(handler.bytes_calls[0]["status"], 200)
        self.assertEqual(handler.json_calls, [])

    def test_handle_root_falls_back_to_api_descriptor(self) -> None:
        handler = _FakeStaticHandler(None)

        handle_root(handler, {})

        self.assertEqual(len(handler.json_calls), 1)
        self.assertEqual(handler.json_calls[0]["status"], 200)
        self.assertEqual(handler.bytes_calls, [])

    def test_serve_static_rejects_path_escape(self) -> None:
        web_root = self._make_web_root()
        handler = _FakeStaticHandler(web_root)

        serve_static(handler, "/../secret.txt")

        self.assertEqual(len(handler.json_calls), 1)
        self.assertEqual(handler.json_calls[0]["status"], 404)
        self.assertEqual(handler.bytes_calls, [])

    def test_serve_static_serves_file_with_mime_type(self) -> None:
        web_root = self._make_web_root()
        asset = web_root / "app.js"
        asset.write_text("console.log('botboy');", encoding="utf-8")
        handler = _FakeStaticHandler(web_root)

        serve_static(handler, "/app.js")

        self.assertEqual(len(handler.bytes_calls), 1)
        self.assertEqual(handler.bytes_calls[0]["status"], 200)
        self.assertEqual(handler.bytes_calls[0]["content_type"], "text/javascript")
