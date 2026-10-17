"""Compatibility adapter for callers needing an HTTP authorization error.

The execution service invokes the transport-independent gate explicitly; importing
this module no longer mutates the execution pipeline.
"""
from __future__ import annotations
from typing import Any
from fastapi import HTTPException
from botboy.security_authorization import AuthorizationError


def _require_final_authorization(service: Any, command: str, *, principal_id: str,
                                 approval_context: dict, task_ctx: Any) -> None:
    try:
        service.require_final_authorization(command, principal_id=principal_id,
                                            approval_context=approval_context, task_ctx=task_ctx)
    except (AuthorizationError, ValueError) as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def install() -> None:
    """Kept for compatibility; the core gate requires no runtime installation."""
