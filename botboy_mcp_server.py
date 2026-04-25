"""
BotBoy MCP Server — Model Context Protocol interface for BotBoy v0.6.0-dev

Exposes the full BotBoy AI agent framework as MCP tools, enabling seamless
integration with Claude Code, Cursor, Windsurf, and all MCP-compatible clients.

Architecture:
  Transport:  stdio (local) / HTTP with --http flag (remote)
  Protocol:   MCP 2024-11-05
  Framework:  Python stdlib (no external MCP SDK required for core operation)
              Optional: mcp[cli] package for enhanced tooling

Tools exposed (15 total):
  MEMORY    botboy_memory_store, botboy_memory_search, botboy_memory_list,
            botboy_memory_delete, botboy_memory_stats
  SKILLS    botboy_calculate, botboy_hash, botboy_convert, botboy_weather,
            botboy_web_search, botboy_base64, botboy_system_info
  AGENT     botboy_execute, botboy_status, botboy_archetype_match
  SCHEDULER botboy_schedule_add, botboy_schedule_list
  HISTORY   botboy_history, botboy_history_stats

Usage:
  # Local stdio (Claude Code / MCP config):
  python botboy_mcp_server.py

  # HTTP mode (remote server):
  python botboy_mcp_server.py --http --port 8766

  # Claude Code mcp_settings.json:
  {
    "mcpServers": {
      "botboy": {
        "command": "python",
        "args": ["/path/to/botboy_mcp_server.py"],
        "env": {"BOTBOY_MEMORY_DB": "/path/to/botboy.db"}
      }
    }
  }
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from functools import lru_cache
import json
import os
import secrets
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add BotBoy to path
sys.path.insert(0, str(Path(__file__).parent))

from botboy.__main__ import BotBoy
from botboy.core.config import BotBoyConfig
from botboy.gateway.security import default_bind_host, origin_allowed, validate_safe_bind
from botboy.resources import default_skills_dir, example_skills_dir
from botboy.skills.manager import SkillManager


# ── BotBoy singleton ──────────────────────────────────────────────────────────

_bot: Optional[BotBoy] = None


def _mcp_state_dir() -> Path:
    root = os.getenv("BOTBOY_MCP_HOME")
    if root:
        path = Path(root)
    else:
        path = Path.home() / ".botboy-mcp"
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        fallback = Path(__file__).resolve().parent / ".botboy-mcp-runtime"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _build_mcp_config() -> BotBoyConfig:
    config = BotBoyConfig.load()
    state_dir = _mcp_state_dir()
    shipped_skills = default_skills_dir()
    examples_dir = example_skills_dir()

    config.memory.db_path = str(state_dir / "botboy.db")
    config.history.db_path = str(state_dir / "history.db")
    config.scheduler.db_path = str(state_dir / "scheduler.db")
    config.trace.db_path = str(state_dir / "traces.db")
    config.security.principal_db_path = str(state_dir / "principals.db")
    config.security.auth_api_key_store_path = str(state_dir / "api_keys.db")
    if shipped_skills.exists():
        config.skills.directory = str(shipped_skills)
    elif examples_dir.exists():
        config.skills.directory = str(examples_dir)
    return config


def _get_bot() -> BotBoy:
    global _bot
    if _bot is None or not _bot._initialized:
        config = _build_mcp_config()
        _bot = BotBoy(config)
        if not _bot.initialize():
            raise RuntimeError("BotBoy initialisation failed")
    return _bot


def _run(coro) -> Any:
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(1) as pool:
                return pool.submit(asyncio.run, coro).result(timeout=30)
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS: List[Dict] = [
    # ── Memory ────────────────────────────────────────────────────────────────
    {
        "name": "botboy_memory_store",
        "description": (
            "Store a persistent memory in BotBoy's SQLite FTS5 knowledge base. "
            "Memories survive across sessions and are full-text searchable. "
            "Use for facts, observations, decisions, and any information that "
            "should be retrievable later. Returns the memory ID."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The text content to store as a memory."
                },
                "metadata": {
                    "type": "object",
                    "description": "Optional key-value metadata tags (e.g. {\"category\": \"decision\"}).",
                    "default": {}
                }
            },
            "required": ["content"]
        }
    },
    {
        "name": "botboy_memory_search",
        "description": (
            "Search BotBoy's memory using FTS5 BM25 full-text search with "
            "optional temporal decay (HybridMemory). Returns ranked results "
            "with IDs, content, and timestamps. Use to retrieve stored facts, "
            "context, or previous decisions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Full-text search query (supports BM25 operators)."
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return (1-50).",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 50
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "botboy_memory_list",
        "description": (
            "List all stored memories with pagination. Use to browse or audit "
            "the knowledge base. Returns memories ordered by recency."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of memories per page.",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 100
                },
                "offset": {
                    "type": "integer",
                    "description": "Pagination offset.",
                    "default": 0,
                    "minimum": 0
                }
            }
        }
    },
    {
        "name": "botboy_memory_delete",
        "description": (
            "Delete memories matching a search query. Searches for matching "
            "memories and removes up to 10 results. Returns deletion count."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Query to match memories for deletion."
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "botboy_memory_stats",
        "description": "Return statistics about the memory database: total count, oldest/newest timestamps, and database size.",
        "inputSchema": {"type": "object", "properties": {}}
    },

    # ── Calculation & data skills ─────────────────────────────────────────────
    {
        "name": "botboy_calculate",
        "description": (
            "Evaluate mathematical expressions using a safe AST-based calculator "
            "(no eval()). Supports arithmetic, trigonometry (sin, cos, tan, asin, "
            "acos, atan), logarithms (log, log2, log10), sqrt, pi, e, tau, abs, "
            "round, min, max, floor, ceil. Returns the numeric result."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Mathematical expression to evaluate (e.g. 'sqrt(144) * pi')."
                }
            },
            "required": ["expression"]
        }
    },
    {
        "name": "botboy_hash",
        "description": (
            "Compute cryptographic hashes of text. Supports md5, sha1, sha224, "
            "sha256 (default), sha384, sha512. Returns the hex digest."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to hash."
                },
                "algorithm": {
                    "type": "string",
                    "description": "Hash algorithm.",
                    "enum": ["md5", "sha1", "sha224", "sha256", "sha384", "sha512"],
                    "default": "sha256"
                }
            },
            "required": ["text"]
        }
    },
    {
        "name": "botboy_convert",
        "description": (
            "Convert between units: length (km↔mi↔m↔cm↔mm↔ft↔in), "
            "weight (kg↔g↔lb↔oz), temperature (C↔F↔K). "
            "Returns the converted value with unit labels."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "value": {
                    "type": "number",
                    "description": "Numeric value to convert."
                },
                "from_unit": {
                    "type": "string",
                    "description": "Source unit (e.g. 'km', 'C', 'kg')."
                },
                "to_unit": {
                    "type": "string",
                    "description": "Target unit (e.g. 'mi', 'F', 'lb')."
                }
            },
            "required": ["value", "from_unit", "to_unit"]
        }
    },
    {
        "name": "botboy_base64",
        "description": "Encode or decode Base64 strings. Supports standard Base64.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "data": {"type": "string", "description": "Input string."},
                "mode": {
                    "type": "string",
                    "enum": ["encode", "decode"],
                    "description": "Operation to perform.",
                    "default": "encode"
                }
            },
            "required": ["data"]
        }
    },
    {
        "name": "botboy_weather",
        "description": (
            "Fetch current weather for any city using wttr.in (no API key required). "
            "Returns temperature, conditions, and wind in a compact format."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "City name (e.g. 'Berlin', 'New York', 'Tokyo')."
                }
            },
            "required": ["city"]
        }
    },
    {
        "name": "botboy_web_search",
        "description": (
            "Search the web using DuckDuckGo Instant Answer API and return a "
            "concise summary. No API key required. Best for factual questions, "
            "definitions, and quick lookups."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query."
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "botboy_system_info",
        "description": "Return current system information: OS, Python version, hostname, PID, memory usage.",
        "inputSchema": {"type": "object", "properties": {}}
    },

    # ── Agent & meta ──────────────────────────────────────────────────────────
    {
        "name": "botboy_execute",
        "description": (
            "Execute any BotBoy command as if typed in the CLI. This is the "
            "universal interface — use for any command not covered by specialized "
            "tools (e.g. 'skills', 'performance', 'history stats', 'schedule list'). "
            "Returns the command result with success flag, output text, and type."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Full BotBoy command string (e.g. 'skills', 'performance', 'archetypes stats')."
                },
                "approval": {
                    "type": "boolean",
                    "description": "Explicitly approve commands that require elevated BotBoy runtime policy.",
                    "default": False
                }
            },
            "required": ["command"]
        }
    },
    {
        "name": "botboy_status",
        "description": "Return a full system health report: all component statuses, cache stats, memory count, channel messages, archetype routing stats.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "botboy_archetype_match",
        "description": (
            "Classify a natural-language query into one of five behavioral archetypes: "
            "INFORM (status/display), ORGANIZE (memory/schedule), COMMUNICATE (LLM/chat), "
            "CREATE (calculate/transform), ANALYZE (research/compare). "
            "Returns archetype, confidence, and matched sense channels."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural-language query to classify."
                }
            },
            "required": ["query"]
        }
    },

    # ── Scheduler ─────────────────────────────────────────────────────────────
    {
        "name": "botboy_schedule_add",
        "description": (
            "Schedule a task to run at a specified time. Supports cron expressions "
            "(\"0 9 * * mon-fri\"), relative times (\"in 30m\", \"in 2h\", \"in 1d\"), "
            "and ISO datetime strings."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Descriptive task name."},
                "schedule": {
                    "type": "string",
                    "description": "Cron expression, relative time ('in 30m'), or ISO datetime."
                },
                "task_type": {
                    "type": "string",
                    "description": "Handler type (use 'generic' for default).",
                    "default": "generic"
                },
                "payload": {
                    "type": "object",
                    "description": "Optional data passed to the task handler.",
                    "default": {}
                }
            },
            "required": ["name", "schedule"]
        }
    },
    {
        "name": "botboy_schedule_list",
        "description": "List all active scheduled tasks with their next run times, run counts, and IDs.",
        "inputSchema": {"type": "object", "properties": {}}
    },

    # ── History ───────────────────────────────────────────────────────────────
    {
        "name": "botboy_history",
        "description": (
            "Retrieve recent command execution history with timing and success status. "
            "Filterable by command type and success flag."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of records to return.",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 200
                },
                "success_only": {
                    "type": "boolean",
                    "description": "If true, return only successful commands.",
                    "default": False
                },
                "cmd_type": {
                    "type": "string",
                    "description": "Filter by command type (e.g. 'calculate', 'memory')."
                }
            }
        }
    },
    {
        "name": "botboy_history_stats",
        "description": "Return aggregated history statistics: total commands, success rate, average latency, and per-type breakdown.",
        "inputSchema": {"type": "object", "properties": {}}
    },
]


_MCP_TOOL_SKILL_MAP: Dict[str, str] = {
    "botboy_calculate": "calculator",
    "botboy_hash": "hash_text",
    "botboy_convert": "convert",
    "botboy_base64": "base64_op",
    "botboy_weather": "weather",
    "botboy_web_search": "web_search",
    "botboy_system_info": "system_info",
}

_MCP_BRIDGE_CONTRACTS: Dict[str, Dict[str, Any]] = {
    "botboy_memory_store": {
        "security_level": "BEGINNER",
        "runtime_tier": "inprocess",
        "capabilities": ["memory", "write"],
        "needs_approval": False,
        "trusted": True,
    },
    "botboy_memory_search": {
        "security_level": "BEGINNER",
        "runtime_tier": "inprocess",
        "capabilities": ["memory", "search"],
        "needs_approval": False,
        "trusted": True,
    },
    "botboy_memory_list": {
        "security_level": "BEGINNER",
        "runtime_tier": "inprocess",
        "capabilities": ["memory", "audit"],
        "needs_approval": False,
        "trusted": True,
    },
    "botboy_memory_delete": {
        "security_level": "INTERMEDIATE",
        "runtime_tier": "subprocess",
        "capabilities": ["memory", "delete"],
        "needs_approval": True,
        "trusted": False,
    },
    "botboy_memory_stats": {
        "security_level": "BEGINNER",
        "runtime_tier": "inprocess",
        "capabilities": ["memory", "stats"],
        "needs_approval": False,
        "trusted": True,
    },
    "botboy_execute": {
        "security_level": "INTERMEDIATE",
        "runtime_tier": "subprocess",
        "capabilities": ["agent-routing", "command-execution"],
        "needs_approval": True,
        "trusted": False,
    },
    "botboy_status": {
        "security_level": "BEGINNER",
        "runtime_tier": "inprocess",
        "capabilities": ["status"],
        "needs_approval": False,
        "trusted": True,
    },
    "botboy_archetype_match": {
        "security_level": "BEGINNER",
        "runtime_tier": "inprocess",
        "capabilities": ["classification"],
        "needs_approval": False,
        "trusted": True,
    },
    "botboy_schedule_add": {
        "security_level": "INTERMEDIATE",
        "runtime_tier": "subprocess",
        "capabilities": ["scheduler", "write"],
        "needs_approval": True,
        "trusted": False,
    },
    "botboy_schedule_list": {
        "security_level": "BEGINNER",
        "runtime_tier": "inprocess",
        "capabilities": ["scheduler", "read"],
        "needs_approval": False,
        "trusted": True,
    },
    "botboy_history": {
        "security_level": "BEGINNER",
        "runtime_tier": "inprocess",
        "capabilities": ["audit", "read"],
        "needs_approval": False,
        "trusted": True,
    },
    "botboy_history_stats": {
        "security_level": "BEGINNER",
        "runtime_tier": "inprocess",
        "capabilities": ["audit", "stats"],
        "needs_approval": False,
        "trusted": True,
    },
}

_MCP_TOOL_NAMES = frozenset(tool["name"] for tool in TOOLS)
_MCP_WRITE_TOOLS = frozenset(
    {
        "botboy_memory_store",
        "botboy_memory_delete",
        "botboy_execute",
        "botboy_schedule_add",
    }
)
_MCP_READ_TOOLS = frozenset(_MCP_TOOL_NAMES - _MCP_WRITE_TOOLS)


def _validate_surface_sets() -> None:
    overlap = _MCP_WRITE_TOOLS.intersection(_MCP_READ_TOOLS)
    if overlap:
        raise RuntimeError(
            "MCP surface misconfigured: write/read sets overlap for "
            + ", ".join(sorted(overlap))
        )
    covered = _MCP_WRITE_TOOLS.union(_MCP_READ_TOOLS)
    missing = _MCP_TOOL_NAMES - covered
    unknown = covered - _MCP_TOOL_NAMES
    if missing or unknown:
        details: List[str] = []
        if missing:
            details.append(f"missing={sorted(missing)}")
        if unknown:
            details.append(f"unknown={sorted(unknown)}")
        raise RuntimeError(
            "MCP surface misconfigured: write/read partition drift (" + ", ".join(details) + ")"
        )


_validate_surface_sets()


def _approval_granted(args: dict) -> bool:
    value = args.get("approval") if isinstance(args, dict) else None
    if isinstance(value, dict):
        value = value.get("granted", value.get("approved"))
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "allow", "approved"}
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, (list, tuple, set)):
        return len(value) > 0
    return False


def _bridge_contract(tool: Dict[str, Any], *, security_level: str, runtime_tier: str, capabilities: List[str],
                     needs_approval: bool, trusted: bool, allow_network: bool = False,
                     allow_filesystem: bool = False) -> Dict[str, Any]:
    return {
        "name": tool["name"],
        "version": "mcp-bridge",
        "description": tool.get("description", ""),
        "triggers": [],
        "security_level": security_level,
        "runtime_tier": runtime_tier,
        "input_schema": dict(tool.get("inputSchema", {})),
        "output_schema": {},
        "capabilities": list(capabilities),
        "needs_approval": needs_approval,
        "allow_network": allow_network,
        "allow_filesystem": allow_filesystem,
        "trusted": trusted,
        "source_path": f"mcp:{tool['name']}",
        "metadata": {"bridge": True, "tool_name": tool["name"]},
    }


def _with_approval_argument(schema: Dict[str, Any]) -> Dict[str, Any]:
    patched = deepcopy(schema if isinstance(schema, dict) else {})
    if patched.get("type") != "object":
        return patched
    properties = dict(patched.get("properties") or {})
    if "approval" not in properties:
        properties["approval"] = {
            "type": "boolean",
            "description": "Explicit approval for this MCP tool call.",
            "default": False,
        }
    patched["properties"] = properties
    return patched


def _canonical_contract_for_tool(
    tool: Dict[str, Any],
    *,
    contract: Dict[str, Any],
    source: str,
) -> Dict[str, Any]:
    canonical = deepcopy(contract)
    metadata = dict(canonical.get("metadata") or {})
    metadata["bridge"] = source != "skill"
    metadata["tool_name"] = tool["name"]
    metadata["tool_write"] = tool["name"] in _MCP_WRITE_TOOLS
    metadata["contract_source"] = source
    canonical["metadata"] = metadata
    canonical["name"] = tool["name"]
    canonical["description"] = str(canonical.get("description") or tool.get("description", ""))
    canonical["version"] = str(canonical.get("version") or ("builtin" if source == "skill" else "mcp-bridge"))
    canonical["triggers"] = list(canonical.get("triggers") or [])
    canonical["security_level"] = str(canonical.get("security_level") or "INTERMEDIATE")
    canonical["runtime_tier"] = str(canonical.get("runtime_tier") or "subprocess")
    canonical["input_schema"] = deepcopy(canonical.get("input_schema") or tool.get("inputSchema") or {})
    canonical["output_schema"] = deepcopy(canonical.get("output_schema") or {})
    canonical["capabilities"] = list(canonical.get("capabilities") or [])
    canonical["needs_approval"] = bool(canonical.get("needs_approval", False))
    canonical["allow_network"] = bool(canonical.get("allow_network", False))
    canonical["allow_filesystem"] = bool(canonical.get("allow_filesystem", False))
    canonical["trusted"] = bool(canonical.get("trusted", False))
    canonical["source_path"] = str(canonical.get("source_path") or f"mcp:{tool['name']}")
    return canonical


@lru_cache(maxsize=1)
def _mcp_contract_catalog() -> Dict[str, Dict[str, Any]]:
    skill_manager = SkillManager(auto_discover=False)
    skill_contracts = {contract["name"]: contract for contract in skill_manager.list_contracts()}
    catalog: Dict[str, Dict[str, Any]] = {}
    for tool in TOOLS:
        tool_name = tool["name"]
        skill_name = _MCP_TOOL_SKILL_MAP.get(tool_name)
        contract = skill_contracts.get(skill_name) if skill_name else None
        source = "skill" if contract is not None else ""
        if contract is None:
            bridge = _MCP_BRIDGE_CONTRACTS.get(tool_name)
            if bridge is not None:
                contract = _bridge_contract(tool, **bridge)
                source = "bridge"
        if contract is None:
            raise RuntimeError(f"No MCP contract mapping found for tool '{tool_name}'")
        catalog[tool_name] = _canonical_contract_for_tool(tool, contract=contract, source=source)
    return catalog


@lru_cache(maxsize=1)
def _approval_required_tools() -> frozenset[str]:
    catalog = _mcp_contract_catalog()
    return frozenset(
        name
        for name, contract in catalog.items()
        if bool(contract.get("needs_approval"))
    )


def _tools_with_contracts() -> List[Dict[str, Any]]:
    catalog = _mcp_contract_catalog()
    approval_required = _approval_required_tools()
    enriched: List[Dict[str, Any]] = []
    for tool in TOOLS:
        tool_name = tool["name"]
        item = deepcopy(tool)
        item["annotations"] = {"readOnlyHint": tool_name in _MCP_READ_TOOLS}
        if tool_name in approval_required:
            item["inputSchema"] = _with_approval_argument(item.get("inputSchema", {}))
        contract = catalog.get(tool_name)
        if contract:
            item["botboyContract"] = deepcopy(contract)
        enriched.append(item)
    return enriched


# ── Tool handlers ─────────────────────────────────────────────────────────────

def _ok(content: str) -> dict:
    return {"content": [{"type": "text", "text": content}], "isError": False}


def _err(msg: str) -> dict:
    return {"content": [{"type": "text", "text": f"Error: {msg}"}], "isError": True}


def _handle_tool(name: str, args: dict) -> dict:
    try:
        args = args if isinstance(args, dict) else {}
        if name not in _MCP_TOOL_NAMES:
            return _err(f"Unknown tool: {name}")

        if name in _approval_required_tools() and not _approval_granted(args):
            return _err(f"Approval required for MCP tool: {name}")

        bot = _get_bot()

        # ── Memory tools ──────────────────────────────────────────────────────
        if name == "botboy_memory_store":
            content  = args["content"]
            metadata = args.get("metadata")
            mem_id   = bot.memory.store(content, metadata)
            return _ok(json.dumps({"id": mem_id, "content": content, "success": True}))

        if name == "botboy_memory_search":
            query   = args["query"]
            limit   = min(int(args.get("limit", 5)), 50)
            results = bot.memory.search(query, limit=limit)
            return _ok(json.dumps({
                "results": [r.to_dict() for r in results],
                "count":   len(results),
                "query":   query,
            }))

        if name == "botboy_memory_list":
            limit   = min(int(args.get("limit", 20)), 100)
            offset  = int(args.get("offset", 0))
            mems    = bot.memory.list_all(limit=limit, offset=offset)
            stats   = bot.memory.get_stats()
            return _ok(json.dumps({
                "memories": [m.to_dict() for m in mems],
                "total":    stats["total"],
                "limit":    limit,
                "offset":   offset,
            }))

        if name == "botboy_memory_delete":
            query   = args["query"]
            matches = bot.memory.search(query, limit=10)
            for m in matches:
                bot.memory.delete(m.id)
            return _ok(json.dumps({"deleted": len(matches), "query": query}))

        if name == "botboy_memory_stats":
            stats = bot.memory.get_stats()
            return _ok(json.dumps(stats))

        # ── Calculation skills ────────────────────────────────────────────────
        if name == "botboy_calculate":
            r = _run(bot.skills.execute_builtin("calculator",
                     f"calculate {args['expression']}"))
            return _ok(json.dumps(r))

        if name == "botboy_hash":
            algo = args.get("algorithm", "sha256")
            text = args["text"]
            r    = _run(bot.skills.execute_builtin("hash_text", f"hash {algo} {text}"))
            return _ok(json.dumps(r))

        if name == "botboy_convert":
            v    = args["value"]
            frm  = args["from_unit"]
            to   = args["to_unit"]
            r    = _run(bot.skills.execute_builtin("convert", f"convert {v} {frm} {to}"))
            return _ok(json.dumps(r))

        if name == "botboy_base64":
            mode = args.get("mode", "encode")
            data = args["data"]
            r    = _run(bot.skills.execute_builtin("base64_op", f"base64 {mode} {data}"))
            return _ok(json.dumps(r))

        if name == "botboy_weather":
            city = args["city"]
            r    = _run(bot.skills.execute_builtin("weather", f"weather {city}"))
            return _ok(json.dumps(r))

        if name == "botboy_web_search":
            query = args["query"]
            r     = _run(bot.skills.execute_builtin("web_search", f"web search {query}"))
            return _ok(json.dumps(r))

        if name == "botboy_system_info":
            r = _run(bot.skills.execute_builtin("system_info", "system info"))
            return _ok(json.dumps(r))

        # ── Agent & meta ──────────────────────────────────────────────────────
        if name == "botboy_execute":
            approved = _approval_granted(args)
            r = _run(
                bot.process_command(
                    args["command"],
                    principal="mcp",
                    approval_context={
                        "granted": approved,
                        "explicit": approved,
                        "reason": "mcp_argument" if approved else "none",
                        "source": "mcp",
                    },
                )
            )
            return _ok(json.dumps(r))

        if name == "botboy_status":
            r = _run(bot.process_command("status"))
            return _ok(json.dumps(r))

        if name == "botboy_archetype_match":
            query = args["query"]
            match = bot.archetypes.match_intent(query)
            profile = bot.archetypes.profile_for(match.archetype)
            return _ok(json.dumps({
                "archetype":    match.archetype.value,
                "confidence":   round(match.confidence, 3),
                "matched_via":  match.matched_via,
                "channels":     profile.primary_channels if profile else [],
                "skill_hint":   match.skill_hint,
            }))

        # ── Scheduler ─────────────────────────────────────────────────────────
        if name == "botboy_schedule_add":
            if not bot.scheduler:
                return _err("Scheduler not enabled")
            task_id = bot.scheduler.add(
                name=args["name"],
                schedule=args["schedule"],
                task_type=args.get("task_type", "generic"),
                payload=args.get("payload"),
            )
            return _ok(json.dumps({"task_id": task_id, "name": args["name"],
                                    "schedule": args["schedule"]}))

        if name == "botboy_schedule_list":
            if not bot.scheduler:
                return _err("Scheduler not enabled")
            tasks = bot.scheduler.list_tasks()
            return _ok(json.dumps({
                "tasks": [t.to_dict() for t in tasks],
                "count": len(tasks),
            }))

        # ── History ───────────────────────────────────────────────────────────
        if name == "botboy_history":
            if not bot.history:
                return _err("History not enabled")
            limit        = min(int(args.get("limit", 20)), 200)
            success_only = args.get("success_only", False)
            cmd_type     = args.get("cmd_type")
            records, total = bot.history.list(
                limit=limit,
                success=True if success_only else None,
                cmd_type=cmd_type,
            )
            return _ok(json.dumps({
                "records": [r.to_dict() for r in records],
                "total":   total,
            }))

        if name == "botboy_history_stats":
            if not bot.history:
                return _err("History not enabled")
            return _ok(json.dumps(bot.history.stats()))

        return _err(f"Unknown tool: {name}")

    except Exception as e:
        return _err(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


# ── Minimal MCP protocol server (stdio) ──────────────────────────────────────

class MCPServer:
    """
    Minimal MCP 2024-11-05 stdio server implementation.
    Handles initialize, tools/list, and tools/call.
    For production use, install the `mcp` package for full protocol support.
    """

    SERVER_INFO = {
        "name":    "botboy",
        "version": "0.6.0-dev",
    }

    def __init__(self) -> None:
        self._request_id = 0

    def _send(self, obj: dict) -> None:
        line = json.dumps(obj, separators=(",", ":"))
        sys.stdout.write(line + "\n")
        sys.stdout.flush()

    def _respond(self, req_id: Any, result: dict) -> None:
        self._send({
            "jsonrpc": "2.0",
            "id":      req_id,
            "result":  result,
        })

    def _error_response(self, req_id: Any, code: int, message: str) -> None:
        self._send({
            "jsonrpc": "2.0",
            "id":      req_id,
            "error":   {"code": code, "message": message},
        })

    def handle(self, msg: dict) -> None:
        method  = msg.get("method", "")
        req_id  = msg.get("id")
        params  = msg.get("params", {})

        if method == "initialize":
            self._respond(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": self.SERVER_INFO,
            })

        elif method == "notifications/initialized":
            pass  # no response needed

        elif method == "tools/list":
            self._respond(req_id, {"tools": _tools_with_contracts()})

        elif method == "tools/call":
            name   = params.get("name", "")
            args   = params.get("arguments", {})
            result = _handle_tool(name, args)
            self._respond(req_id, result)

        elif method == "ping":
            self._respond(req_id, {})

        else:
            if req_id is not None:
                self._error_response(req_id, -32601, f"Method not found: {method}")

    def run_stdio(self) -> None:
        """Run the MCP server over stdio."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                self.handle(msg)
            except json.JSONDecodeError as e:
                sys.stderr.write(f"[BotBoy MCP] JSON parse error: {e}\n")
            except Exception as e:
                sys.stderr.write(f"[BotBoy MCP] Error: {e}\n{traceback.format_exc()}")


# ── HTTP mode (optional) ─────────────────────────────────────────────────────

class MCPHTTPServer:
    """HTTP transport for remote BotBoy MCP access."""

    def __init__(self, port: int = 8766, host: str = default_bind_host()) -> None:
        self.port  = port
        self.host  = host
        self.token = str(os.getenv("BOTBOY_MCP_HTTP_TOKEN", "")).strip()
        validate_safe_bind(self.host, auth_enabled=bool(self.token), surface="mcp")
        self._mcp  = MCPServer()

    def start(self) -> None:
        from http.server import BaseHTTPRequestHandler, HTTPServer
        mcp = self._mcp
        server_host = self.host
        server_port = self.port
        expected_token = self.token

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def _cors_headers(self):
                origin = self.headers.get("Origin") or self.headers.get("origin") or ""
                if origin_allowed(origin, host=server_host, port=server_port):
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-BotBoy-MCP-Token")

            def _token_ok(self) -> bool:
                if not expected_token:
                    return True
                bearer = self.headers.get("Authorization") or self.headers.get("authorization") or ""
                provided = ""
                if bearer.lower().startswith("bearer "):
                    provided = bearer[7:].strip()
                provided = provided or (self.headers.get("X-BotBoy-MCP-Token") or "")
                if secrets.compare_digest(provided, expected_token):
                    return True
                body = json.dumps({"error": "Missing or invalid MCP token"}).encode()
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self._cors_headers()
                self.end_headers()
                self.wfile.write(body)
                return False

            def do_OPTIONS(self):
                self.send_response(200)
                self._cors_headers()
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                if self.path not in ("/mcp", "/"):
                    self.send_response(404)
                    self._cors_headers()
                    self.end_headers()
                    return
                if not self._token_ok():
                    return
                length = int(self.headers.get("Content-Length", 0))
                body   = self.rfile.read(length) if length > 0 else b""
                try:
                    msg  = json.loads(body)
                    # Redirect stdout to capture response
                    import io
                    old_stdout = sys.stdout
                    buf = io.StringIO()
                    try:
                        sys.stdout = buf
                        mcp.handle(msg)
                    finally:
                        sys.stdout = old_stdout
                    output = buf.getvalue().strip()
                    response = output.encode() if output else b"{}"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(response)))
                    self._cors_headers()
                    self.end_headers()
                    self.wfile.write(response)
                except Exception as e:
                    err = json.dumps({"error": str(e)}).encode()
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(err)))
                    self._cors_headers()
                    self.end_headers()
                    self.wfile.write(err)

            def do_GET(self):
                if self.path == "/health":
                    if not self._token_ok():
                        return
                    body = json.dumps({
                        "status": "ok",
                        "server": "botboy-mcp",
                        "tools":  len(TOOLS),
                        "auth": bool(expected_token),
                    }).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self._cors_headers()
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self._cors_headers()
                    self.end_headers()

        httpd = HTTPServer((self.host, self.port), Handler)
        print(f"[BotBoy MCP] HTTP server on http://{self.host}:{self.port}", file=sys.stderr)
        httpd.serve_forever()


# ── Entry point ───────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="BotBoy MCP Server")
    parser.add_argument("--http",   action="store_true", help="Use HTTP transport instead of stdio")
    parser.add_argument("--host",   default=default_bind_host(), help="HTTP bind host (default: 127.0.0.1)")
    parser.add_argument("--port",   type=int, default=8766, help="HTTP port (default: 8766)")
    parser.add_argument("--list-tools", action="store_true", help="Print available tools and exit")
    args = parser.parse_args(argv)

    if args.list_tools:
        for tool in TOOLS:
            description = str(tool["description"][:60]).encode(
                sys.stdout.encoding or "utf-8",
                errors="replace",
            ).decode(sys.stdout.encoding or "utf-8", errors="replace")
            print(f"  {tool['name']:35} {description}...")
        return 0

    # Warm up BotBoy on startup
    try:
        _get_bot()
        sys.stderr.write(f"[BotBoy MCP] Initialised. {len(TOOLS)} tools available.\n")
    except Exception as e:
        sys.stderr.write(f"[BotBoy MCP] Init warning: {e}\n")

    if args.http:
        MCPHTTPServer(port=args.port, host=args.host).start()
    else:
        MCPServer().run_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
