from __future__ import annotations

import mimetypes
from pathlib import Path


def serve_static(handler, path: str) -> None:
    web_dir = getattr(handler, "web_dir", None)
    if not web_dir:
        handler._json({"error": "Not found"}, 404)
        return
    base_dir = Path(web_dir).resolve()
    requested = (base_dir / path.lstrip("/\\")).resolve()
    if requested != base_dir and base_dir not in requested.parents:
        handler._json({"error": "Not found"}, 404)
        return
    if not requested.is_file():
        handler._json({"error": "Not found"}, 404)
        return
    mime, _ = mimetypes.guess_type(str(requested))
    handler._bytes(
        requested.read_bytes(),
        content_type=mime or "application/octet-stream",
        status=200,
    )


def handle_root(handler, _params: dict) -> None:
    web_dir = getattr(handler, "web_dir", None)
    if web_dir:
        index = Path(web_dir) / "index.html"
        if index.is_file():
            handler._bytes(index.read_bytes(), content_type="text/html", status=200)
            return
    handler._json(
        {"name": "BotBoy API", "version": "0.6.0-dev", "docs": "/health", "mode": "stdlib"}
    )
