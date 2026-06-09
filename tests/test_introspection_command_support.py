from __future__ import annotations

import unittest

from botboy.introspection_command_support import (
    handle_archetypes,
    handle_reflection_archive,
    handle_workers,
)


class _FakeWorker:
    worker_id = "reviewer"

    def to_dict(self) -> dict:
        return {
            "worker_id": "reviewer",
            "display_name": "Reviewer",
            "role": "reviewer",
            "approval_profile": "strict",
            "max_concurrency": 2,
            "capabilities": ["review", "merge"],
        }


class _FakeReflectionEntry:
    def __init__(self, entry_id: str = "entry-1") -> None:
        self.entry_id = entry_id
        self.task_id = "task-1"
        self.outcome = "completed"
        self.author = "tester"
        self.summary = "done"
        self.reflection = "ship it"

    def to_dict(self) -> dict:
        return {
            "entry_id": self.entry_id,
            "task_id": self.task_id,
            "outcome": self.outcome,
            "author": self.author,
            "summary": self.summary,
            "reflection": self.reflection,
        }


class _FakeReflectionArchive:
    def stats(self) -> dict:
        return {
            "total_entries": 1,
            "by_outcome": {"completed": 1},
            "top_tags": {"task_reflection": 1},
            "latest_entry": {"entry_id": "entry-1"},
        }

    def list_entries(self, task_id: str = "", limit: int = 10):
        return [_FakeReflectionEntry()]

    def latest_for_task(self, task_id: str):
        return _FakeReflectionEntry()

    def archive_context(self, **kwargs):
        return _FakeReflectionEntry("entry-2")


class _FakeTraceStore:
    def get_run(self, run_id: str):
        return {"run_id": run_id}


class _FakeTaskRecord:
    def __init__(self) -> None:
        self.status = "completed"
        self.summary = "done"
        self.title = "title"
        self.owner = "worker:reviewer"
        self.command = "status"
        self.principal = "tester"
        self.run_id = "run-1"


class _FakeTaskStore:
    def __init__(self) -> None:
        self.node = {
            "node_id": "node-1",
            "worker_id": "reviewer",
            "queue_name": "reviewer.node-1",
            "effective_status": "ready",
            "last_heartbeat_at": "2026-01-01T00:00:00+00:00",
            "health": "unknown",
            "draining": False,
        }

    def get_task(self, task_id: str):
        return _FakeTaskRecord() if task_id == "task-1" else None

    def list_worker_nodes(self):
        return [dict(self.node)]

    def list_execution_queues(self):
        return [{"queue_name": self.node["queue_name"], "worker_id": self.node["worker_id"]}]

    def worker_node_summary(self):
        return {"node_count": 1, "healthy_count": 1, "draining_count": 0}

    def register_worker_node(self, **kwargs):
        self.node.update(
            {
                "node_id": kwargs["node_id"],
                "worker_id": kwargs["worker_id"],
                "queue_name": f"{kwargs['worker_id']}.{kwargs['node_id']}",
                "effective_status": "ready",
            }
        )
        return dict(self.node)

    def heartbeat_worker_node(self, node_id: str, **kwargs):
        if node_id != self.node["node_id"]:
            return None
        self.node["health"] = kwargs.get("health", "healthy") or "healthy"
        self.node["effective_status"] = kwargs.get("node_status", "ready") or "ready"
        self.node["last_heartbeat_at"] = "2026-01-02T00:00:00+00:00"
        return dict(self.node)

    def drain_worker_node(self, node_id: str, **kwargs):
        if node_id != self.node["node_id"]:
            return None
        self.node["effective_status"] = "draining"
        self.node["draining"] = True
        return dict(self.node)


class _FakeMatchArchetype:
    value = "builder"


class _FakeMatch:
    archetype = _FakeMatchArchetype()
    confidence = 0.9
    matched_via = "keyword"


class _FakeProfile:
    primary_channels = ["think", "act"]


class _FakeArchetypes:
    def stats(self) -> dict:
        return {"total_commands_routed": 1, "archetypes": [{"archetype": "builder", "use_count": 1, "success_rate": 1.0}]}

    def match_intent(self, query: str):
        return _FakeMatch()

    def profile_for(self, archetype):
        return _FakeProfile()


class _FakeRouter:
    def stats(self) -> dict:
        return {"message_counts": {"think": 2, "act": 1}}


class _FakeBot:
    def __init__(self) -> None:
        self.workers = {"reviewer": _FakeWorker()}
        self.reflection_archive = _FakeReflectionArchive()
        self.trace_store = _FakeTraceStore()
        self.task_store = _FakeTaskStore()
        self.archetypes = _FakeArchetypes()
        self.router = _FakeRouter()

    def get_worker_payload(self) -> dict:
        return {
            "workers": [
                {
                    "worker_id": "reviewer",
                    "role": "reviewer",
                    "active_tasks": 1,
                    "delegated_tasks": 2,
                    "capabilities": ["review", "merge"],
                }
            ]
        }


class IntrospectionCommandSupportTest(unittest.TestCase):
    def test_handle_workers_list(self) -> None:
        result = handle_workers(_FakeBot(), "worker list")
        self.assertTrue(result["success"])
        self.assertIn("Workers (1)", result["output"])

    def test_handle_worker_node_commands(self) -> None:
        bot = _FakeBot()
        register = handle_workers(bot, "worker node register node-2 reviewer")
        listing = handle_workers(bot, "worker node list")
        heartbeat = handle_workers(bot, "worker node heartbeat node-2 running")
        drain = handle_workers(bot, "worker node drain node-2 maintenance")
        self.assertTrue(register["success"])
        self.assertIn("node-2", register["output"])
        self.assertTrue(listing["success"])
        self.assertIn("Worker nodes", listing["output"])
        self.assertTrue(heartbeat["success"])
        self.assertIn("heartbeat", heartbeat["output"].lower())
        self.assertTrue(drain["success"])
        self.assertIn("draining", drain["output"].lower())

    def test_handle_reflection_archive_stats(self) -> None:
        result = handle_reflection_archive(_FakeBot(), "reflect stats", principal="tester")
        self.assertTrue(result["success"])
        self.assertIn("Reflection archive:", result["output"])

    def test_handle_reflection_archive_record(self) -> None:
        result = handle_reflection_archive(
            _FakeBot(),
            "reflect record task-1 keep-merges-coherent",
            principal="tester",
        )
        self.assertTrue(result["success"])
        self.assertIn("entry=entry-2", result["output"])

    def test_handle_archetypes_match(self) -> None:
        result = handle_archetypes(_FakeBot(), "archetypes match split the runtime")
        self.assertTrue(result["success"])
        self.assertIn("Intent match for:", result["output"])


if __name__ == "__main__":
    unittest.main()
