from __future__ import annotations

from typing import Any, Callable, Optional

from fastapi import HTTPException

from botboy.gateway.app_context import current_gateway_org, current_gateway_principal, current_gateway_roles


class GatewayMergeActionError(ValueError):
    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class _AuthorizedMergeBotView:
    """Expose merge operations only after gateway task/family authorization."""

    def __init__(self, bot: Any, store: Any, task_id: str) -> None:
        self._bot = bot
        self._store = store
        self._task_id = str(task_id or "")
        self._authorize_task_family()

    def _raw_store(self) -> Any:
        return getattr(self._store, "_store", self._store)

    def _authorized(self, record: Any) -> bool:
        if record is None:
            return False
        if not bool(getattr(self._store, "_auth_enabled", False)):
            return True
        roles = {str(role).lower() for role in current_gateway_roles()}
        if "system" in roles:
            return True
        org_id = current_gateway_org()
        record_org = str(getattr(record, "org_id", "default") or "default")
        if record_org != org_id:
            return False
        if "admin" in roles:
            return True
        principal = current_gateway_principal()
        return bool(principal) and str(getattr(record, "principal", "")) == principal

    def _authorize_task_family(self) -> None:
        task = self._store.get_task(self._task_id)
        if not task or not self._authorized(task):
            raise HTTPException(status_code=404, detail="Task not found")
        raw_store = self._raw_store()
        list_children = getattr(raw_store, "list_children", None)
        if not callable(list_children):
            return
        pending = [self._task_id]
        seen: set[str] = set()
        while pending:
            parent_id = pending.pop()
            if parent_id in seen:
                continue
            seen.add(parent_id)
            try:
                children = list_children(parent_id, limit=200) or []
            except (AttributeError, TypeError, ValueError):
                children = []
            for child in children:
                if not self._authorized(child):
                    raise HTTPException(status_code=404, detail="Task not found")
                child_id = str(getattr(child, "task_id", "") or "")
                if child_id:
                    pending.append(child_id)

    def get_task_merge_payload(self, task_id: str, *, record: Any = None) -> dict[str, Any]:
        if str(task_id or "") != self._task_id:
            raise HTTPException(status_code=404, detail="Task not found")
        return self._bot.get_task_merge_payload(task_id, record=record)

    def apply_task_merge_review_action(self, task_id: str, **kwargs: Any):
        if str(task_id or "") != self._task_id:
            raise HTTPException(status_code=404, detail="Task not found")
        return self._bot.apply_task_merge_review_action(task_id, **kwargs)


def authorized_merge_bot_view(bot: Any, store: Any, task_id: str) -> _AuthorizedMergeBotView:
    return _AuthorizedMergeBotView(bot, store, task_id)


def _operation_results(items: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "key": entry["key"],
            "source": entry.get("source", ""),
            "success": True,
            "error": "",
        }
        for entry in items
    ]


def _normalize_operation_items(
    *,
    key: str,
    source: str,
    items: Optional[list[Any]],
    keys: Optional[list[Any]],
) -> list[dict[str, str]]:
    operation_items: list[dict[str, str]] = []
    for item in items or []:
        if isinstance(item, dict):
            item_key = str(item.get("key", "") or "").strip()
            item_source = str(item.get("source", "") or "").strip()
            if item_key:
                operation_items.append({"key": item_key, "source": item_source or source})
        else:
            item_key = str(item or "").strip()
            if item_key:
                operation_items.append({"key": item_key, "source": source})
    for item_key in keys or []:
        item_key_text = str(item_key or "").strip()
        if item_key_text:
            operation_items.append({"key": item_key_text, "source": source})
    if key:
        operation_items.append({"key": key, "source": source})
    return operation_items


def build_merge_action_payload(
    bot,
    *,
    store,
    task_id: str,
    action: str,
    principal: str = "anonymous",
    request_id: str = "",
    key: str = "",
    source: str = "",
    items: Optional[list[Any]] = None,
    keys: Optional[list[Any]] = None,
    preset: str = "",
    decorate_merge_payload: Callable[[dict[str, Any]], dict[str, Any]],
    task_records_by_root: Callable[[Any, str], list[Any]],
    decorate_task_record: Callable[[Any, Any, Optional[list[Any]]], dict[str, Any]],
) -> dict[str, Any]:
    if not store:
        raise GatewayMergeActionError("Task store not available", status_code=503)
    task = store.get_task(task_id)
    if not task:
        raise GatewayMergeActionError("Task not found", status_code=404)
    authorized_bot = authorized_merge_bot_view(bot, store, task_id)
    normalized_action = str(action or "").strip().lower().replace("-", "_")
    root_records = task_records_by_root(store, task.root_task_id)

    def build_response(
        refreshed,
        *,
        bulk_results: Optional[list[dict[str, Any]]] = None,
        applied_count: Optional[int] = None,
        failed_count: Optional[int] = None,
        preset_name: str = "",
    ) -> dict[str, Any]:
        merge = decorate_merge_payload(authorized_bot.get_task_merge_payload(task_id, record=refreshed))
        response = {
            "available": True,
            "task_id": task_id,
            "action": normalized_action,
            "task": decorate_task_record(store, refreshed, root_records) if refreshed else None,
            "merge": merge,
        }
        if bulk_results is not None:
            response["bulk_results"] = bulk_results
            response["applied_count"] = int(applied_count or 0)
            response["failed_count"] = int(failed_count or 0)
        if preset_name:
            response["preset"] = preset_name
        return response

    def apply_action(**action_kwargs: Any):
        try:
            return authorized_bot.apply_task_merge_review_action(task_id, **action_kwargs)
        except ValueError as exc:
            raise GatewayMergeActionError(str(exc), status_code=400) from exc

    if normalized_action in {
        "resolve", "override", "resolve_key", "clear", "clear_resolution",
        "clear_override", "reapply", "refresh", "apply_policy",
    }:
        if normalized_action in {"resolve", "override", "resolve_key", "clear", "clear_resolution", "clear_override"} and not key:
            raise GatewayMergeActionError("Missing merge key", status_code=400)
        updated = apply_action(
            action=normalized_action,
            key=key,
            source=source,
            principal=principal or task.principal,
            request_id=request_id or task.request_id or task_id,
        )
        refreshed = updated or store.get_task(task_id)
        return build_response(refreshed)

    if normalized_action in {"resolve_many", "clear_many"}:
        operation_items = _normalize_operation_items(key=key, source=source, items=items, keys=keys)
        if not operation_items:
            raise GatewayMergeActionError("Bulk merge action requires items or keys", status_code=400)
        action_kwargs: dict[str, Any] = {
            "action": normalized_action,
            "principal": principal or task.principal,
            "request_id": request_id or task.request_id or task_id,
        }
        if normalized_action == "resolve_many":
            action_kwargs["items"] = operation_items
        else:
            action_kwargs["keys"] = [entry["key"] for entry in operation_items if entry.get("key")]
        updated = apply_action(**action_kwargs)
        refreshed = store.get_task(task_id)
        results = _operation_results(operation_items)
        return build_response(updated or refreshed, bulk_results=results, applied_count=len(results), failed_count=0)

    if normalized_action == "resolve_all_by_source":
        if not source:
            raise GatewayMergeActionError("resolve_all_by_source requires a source", status_code=400)
        requested_keys = [str(item).strip() for item in (keys or []) if str(item).strip()]
        if key:
            requested_keys.append(key)
        updated = apply_action(
            action=normalized_action,
            source=source,
            keys=requested_keys or None,
            principal=principal or task.principal,
            request_id=request_id or task.request_id or task_id,
        )
        refreshed = updated or store.get_task(task_id)
        merge = decorate_merge_payload(authorized_bot.get_task_merge_payload(task_id, record=refreshed))
        resolved_keys = requested_keys or [
            str(item).strip() for item in (merge.get("configured_resolution_overrides", {}) or {}).keys() if str(item).strip()
        ]
        operation_items = [{"key": item_key, "source": source} for item_key in resolved_keys]
        return build_response(refreshed, bulk_results=_operation_results(operation_items), applied_count=len(operation_items), failed_count=0)

    if normalized_action == "apply_preset":
        preset_name = str(preset or "").strip()
        if not preset_name:
            raise GatewayMergeActionError("apply_preset requires a preset", status_code=400)
        normalized_preset = preset_name.lower().replace("-", "_")
        if normalized_preset == "clear_overrides":
            merge = decorate_merge_payload(authorized_bot.get_task_merge_payload(task_id, record=task))
            override_keys = list(dict.fromkeys([
                str(item).strip() for item in merge.get("configured_resolution_overrides", {}).keys() if str(item).strip()
            ]))
            if not override_keys:
                return build_response(task, bulk_results=[], applied_count=0, failed_count=0, preset_name=normalized_preset)
            updated = apply_action(
                action="clear_many", keys=override_keys,
                principal=principal or task.principal,
                request_id=request_id or task.request_id or task_id,
            )
            refreshed = updated or store.get_task(task_id)
            return build_response(
                refreshed,
                bulk_results=_operation_results([{"key": item_key, "source": ""} for item_key in override_keys]),
                applied_count=len(override_keys), failed_count=0, preset_name=normalized_preset,
            )
        updated = apply_action(
            action=normalized_action, preset=normalized_preset,
            principal=principal or task.principal,
            request_id=request_id or task.request_id or task_id,
        )
        refreshed = updated or store.get_task(task_id)
        return build_response(refreshed, bulk_results=[], applied_count=1, failed_count=0, preset_name=normalized_preset)

    raise GatewayMergeActionError(f"Unsupported merge action '{action}'", status_code=400)
