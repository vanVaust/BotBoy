"""Gateway errors without making the FastAPI adapter a base dependency."""
from __future__ import annotations

from importlib.util import find_spec
from typing import Any

if find_spec("fastapi") is not None:
    from fastapi import HTTPException
else:
    class HTTPException(Exception):
        """The same error contract for the stdlib HTTP adapter."""

        def __init__(self, status_code: int, detail: Any = None, headers: dict | None = None):
            self.status_code = status_code
            self.detail = detail
            self.headers = headers
            super().__init__(str(detail))
