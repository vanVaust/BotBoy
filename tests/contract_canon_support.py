from __future__ import annotations

import inspect
import json
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from botboy.cli_support import build_parser


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_CANON_PATH = ROOT / "botboy" / "data" / "CONTRACT_CANON.json"

_PARAM_PATTERN = re.compile(r"\{[^{}]+\}")
_DYNAMIC_ROUTE_PATTERN = re.compile(
    r'if\s+normalized\.startswith\("(?P<prefix>[^"]+)"\)'
    r'(?:\s+and\s+normalized\.endswith\("(?P<suffix>[^"]+)"\))?:\s*\n\s*'
    r'return\s+"(?P<handler>[^"]+)"',
    re.MULTILINE,
)


def load_contract_canon() -> dict[str, Any]:
    return json.loads(CONTRACT_CANON_PATH.read_text(encoding="utf-8"))


def _normalize_route_path(path: str) -> str:
    return _PARAM_PATTERN.sub("{param}", path)


def _sort_routes(routes: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(routes, key=lambda item: (item["method"], item["path"]))


def _sort_route_handlers(routes: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(routes, key=lambda item: (item["method"], item["path"], item["handler"]))


def extract_cli_commands() -> list[dict[str, Any]]:
    parser = build_parser()
    subparsers_action = next(
        action
        for action in parser._actions
        if getattr(action, "dest", None) == "command" and hasattr(action, "choices")
    )
    commands: list[dict[str, Any]] = []
    for command_name, subparser in sorted(subparsers_action.choices.items()):
        positionals: list[dict[str, Any]] = []
        options: list[list[str]] = []
        for action in subparser._actions:
            if action.dest == "help":
                continue
            if action.option_strings:
                options.append(list(action.option_strings))
            else:
                nargs = action.nargs if action.nargs is not None else 1
                positionals.append({"name": action.dest, "nargs": nargs})
        commands.append(
            {
                "name": command_name,
                "positionals": positionals,
                "options": options,
            }
        )
    return commands


def extract_mcp_tools() -> list[dict[str, Any]]:
    import botboy_mcp_server as mcp_server

    approval_required = set(mcp_server._approval_required_tools())
    return [
        {
            "name": tool["name"],
            "needs_approval": tool["name"] in approval_required,
        }
        for tool in mcp_server.TOOLS
    ]


def _release_standard_site_packages() -> Path:
    return ROOT / ".release-build-venv" / "Lib" / "site-packages"


def _load_fastapi_route_factories():
    try:
        from botboy.gateway.routes_auth import create_auth_router
        from botboy.gateway.routes_core import create_core_router
        from botboy.gateway.routes_tasks import create_task_router
        from botboy.gateway.routes_ws import create_ws_router
        return create_auth_router, create_core_router, create_task_router, create_ws_router
    except ImportError:
        site_packages = _release_standard_site_packages()
        if not site_packages.exists():
            raise unittest.SkipTest("FastAPI router modules are unavailable and no release build env exists.")
        if str(site_packages) not in sys.path:
            sys.path.insert(0, str(site_packages))
        from botboy.gateway.routes_auth import create_auth_router
        from botboy.gateway.routes_core import create_core_router
        from botboy.gateway.routes_tasks import create_task_router
        from botboy.gateway.routes_ws import create_ws_router
        return create_auth_router, create_core_router, create_task_router, create_ws_router


def _dummy_gateway_context():
    from botboy.gateway.app_context import GatewayAppContext

    async def _process_command(*args, **kwargs):
        return {"success": True, "output": "ok"}

    bot = SimpleNamespace(
        VERSION="0.0-test",
        memory=None,
        skills=None,
        cache=None,
        scheduler=None,
        history=None,
        trace_store=None,
        llm=None,
        router=None,
        archetypes=None,
        metrics=None,
        _start_time=0.0,
        get_monitoring_payload=lambda: {"monitoring": True},
        process_command=_process_command,
    )
    auth = SimpleNamespace(
        create_pair=lambda principal: SimpleNamespace(
            access_token="token",
            refresh_token="refresh",
            expires_in=3600,
            token_type="bearer",
        ),
        refresh=lambda token: None,
        verify=lambda token: None,
        extract_bearer_token=lambda header: "",
    )
    return GatewayAppContext(
        bot=bot,
        host="127.0.0.1",
        port=8765,
        web_dir=ROOT / "botboy" / "web",
        auth=auth,
        auth_enabled=False,
        rate_limiter=None,
        security=SimpleNamespace(auth_api_keys_enabled=True),
        authorize=lambda *args, **kwargs: "release.test",
        authorize_with_roles=lambda *args, **kwargs: ("release.test", ["admin"]),
        approval_context=lambda headers, payload, roles: {},
        require_admin=lambda *args, **kwargs: None,
        get_api_key_store=lambda: None,
        bootstrap_principal_store=lambda: None,
        principal_payload=lambda principal: {"username": getattr(principal, "username", "release.test")},
        task_store_or_503=lambda optional=False: None,
        task_detail_payload=lambda task_id: {"task_id": task_id},
        task_merge_payload=lambda task_id: {"task_id": task_id, "merge": True},
        task_merge_action_payload=lambda *args, **kwargs: {"success": True},
        task_list_records=lambda *args, **kwargs: ([], 0, []),
        task_metrics=lambda store: {
            "blockers": [],
            "blocked_count": 0,
            "delegated_count": 0,
            "running_count": 0,
            "queued_count": 0,
            "worker_count": 0,
            "handoff_queue_depth": 0,
            "oldest_blocked_age_s": 0,
            "oldest_running_age_s": 0,
            "by_worker": {},
            "by_blocked_kind": {},
        },
        task_workers_payload=lambda store: {"available": True, "workers": []},
        task_records_by_root=lambda store, root_task_id: [],
        direct_child_records=lambda records, task_id: [],
        decorate_task_record=lambda *args, **kwargs: {"task_id": "release.test"},
        build_task_graph=lambda *args, **kwargs: {"graph": True},
        collect_task_records=lambda store: [],
        worker_lookup=lambda: {},
        worker_id_from_owner=lambda owner: owner,
        dashboard_payload=lambda mode: {"mode": mode, "dashboard": True},
    )


def extract_fastapi_http_routes() -> list[dict[str, str]]:
    create_auth_router, create_core_router, create_task_router, _ = _load_fastapi_route_factories()
    ctx = _dummy_gateway_context()
    routes: list[dict[str, str]] = []
    for router in (create_core_router(ctx), create_task_router(ctx), create_auth_router(ctx)):
        for route in router.routes:
            for method in sorted(getattr(route, "methods", set()) or set()):
                routes.append(
                    {
                        "method": method,
                        "path": _normalize_route_path(route.path),
                    }
                )
    return _sort_routes(routes)


def extract_fastapi_websocket_routes() -> list[str]:
    _, _, _, create_ws_router = _load_fastapi_route_factories()
    ctx = _dummy_gateway_context()
    return sorted({route.path for route in create_ws_router(ctx).routes})


def _extract_dynamic_routes(function_source: str, method: str) -> list[dict[str, str]]:
    routes: list[dict[str, str]] = []
    for match in _DYNAMIC_ROUTE_PATTERN.finditer(function_source):
        prefix = match.group("prefix")
        suffix = match.group("suffix") or ""
        template = _normalize_route_path(f"{prefix}{{param}}{suffix}")
        routes.append(
            {
                "method": method,
                "path": template,
                "handler": match.group("handler"),
            }
        )
    return routes


def extract_stdlib_http_routes_with_handlers() -> list[dict[str, str]]:
    from botboy.gateway import simple_routing

    route_handlers: dict[tuple[str, str], str] = {}
    for path, handler in simple_routing.GET_EXACT_ROUTES.items():
        route_handlers[("GET", _normalize_route_path(path))] = handler
    for path, handler in simple_routing.POST_EXACT_ROUTES.items():
        route_handlers[("POST", _normalize_route_path(path))] = handler

    for function_name, method in (
        ("resolve_get_route", "GET"),
        ("resolve_post_route", "POST"),
        ("resolve_delete_route", "DELETE"),
    ):
        source = inspect.getsource(getattr(simple_routing, function_name))
        for route in _extract_dynamic_routes(source, method):
            route_handlers[(route["method"], route["path"])] = route["handler"]

    return _sort_route_handlers(
        [
            {
                "method": method,
                "path": path,
                "handler": route_handlers[(method, path)],
            }
            for method, path in route_handlers.keys()
        ]
    )


def extract_stdlib_http_routes() -> list[dict[str, str]]:
    return _sort_routes(
        [
            {"method": item["method"], "path": item["path"]}
            for item in extract_stdlib_http_routes_with_handlers()
        ]
    )
