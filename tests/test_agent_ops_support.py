from __future__ import annotations

import unittest

from botboy.agent_ops_support import (
    handle_a2a,
    handle_agent_skill_library,
    handle_agent_skill_route,
    handle_contracts,
    handle_handoff_suggest,
)


class _FakeSkillEntry:
    def __init__(self, name: str) -> None:
        self.name = name
        self.category = "ops"
        self.phase = "review"
        self.summary = f"summary-{name}"
        self.portfolio_tier = "tier-1"
        self.portfolio_bucket = "core"

    def resolved_path(self) -> str:
        return f"/tmp/{self.name}"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "category": self.category,
            "phase": self.phase,
            "summary": self.summary,
            "path": self.resolved_path(),
        }


class _FakeSkillLibrary:
    def __init__(self) -> None:
        self.entry = _FakeSkillEntry("router_split")

    def stats(self) -> dict:
        return {"count": 1}

    def get(self, query: str):
        return self.entry if query == "router_split" else None

    def suggest(self, query: str, limit: int = 5):
        return [self.entry][:limit]


class _FakeSkills:
    def list_contracts(self):
        return [
            {
                "name": "planner",
                "version": "1.0",
                "security_level": "standard",
                "runtime_tier": "local",
                "needs_approval": True,
                "trusted": True,
                "allow_network": False,
                "allow_filesystem": True,
                "triggers": ["plan"],
                "capabilities": ["routing"],
            }
        ]

    def get_contract_for_skill(self, query: str):
        if query == "planner":
            return self.list_contracts()[0]
        return None

    def get_contract_for_command(self, query: str):
        return None


class _FakeFit:
    def __init__(self, worker_id: str) -> None:
        self.worker_id = worker_id
        self.score = 0.9
        self.matched_capabilities = ["routing"]

    def to_dict(self) -> dict:
        return {
            "worker_id": self.worker_id,
            "score": self.score,
            "matched_capabilities": self.matched_capabilities,
        }


class _FakePlanStep:
    def __init__(self, worker_id: str, action: str) -> None:
        self.worker_id = worker_id
        self.action = action


class _FakeAdvice:
    chosen_worker = "reviewer"
    rationale = "best match"
    ranked_workers = [_FakeFit("reviewer")]
    plan = [_FakePlanStep("reviewer", "check contracts")]

    def to_dict(self) -> dict:
        return {"chosen_worker": self.chosen_worker}


class _FakeAdvisor:
    def recommend_for_task(self, query: str):
        return _FakeAdvice()


class _FakeSuggestedAdapter:
    adapter_id = "planner"

    def to_dict(self) -> dict:
        return {"adapter_id": self.adapter_id}


class _FakeResponse:
    ok = True
    status = "ok"

    def to_dict(self) -> dict:
        return {"ok": self.ok, "status": self.status}


class _FakeRegistry:
    def stats(self):
        return {"total_adapters": 1, "adapters": [{"adapter_id": "planner", "capabilities": ["routing"], "max_payload_bytes": 1024}]}

    def rank_adapters(self, query: str):
        return [_FakeFit("planner")]

    def suggest_adapter(self, query: str):
        return _FakeSuggestedAdapter()

    def dispatch(self, adapter_id: str, payload: dict, *, sender: str, principal: str, task_id: str):
        return _FakeResponse()


class _FakeBot:
    def __init__(self) -> None:
        self.skills = _FakeSkills()
        self.agent_skill_library = _FakeSkillLibrary()
        self.delegation_advisor = _FakeAdvisor()
        self.a2a_pilot = _FakeRegistry()


class AgentOpsSupportTest(unittest.TestCase):
    def test_handle_contracts_audit(self) -> None:
        result = handle_contracts(_FakeBot(), "contracts audit")
        self.assertTrue(result["success"])
        self.assertIn("Contract audit:", result["output"])

    def test_handle_agent_skill_library_lookup(self) -> None:
        result = handle_agent_skill_library(_FakeBot(), "skilllib router_split")
        self.assertTrue(result["success"])
        self.assertIn("router_split", result["output"])

    def test_handle_agent_skill_route(self) -> None:
        result = handle_agent_skill_route(_FakeBot(), "skillroute routing")
        self.assertTrue(result["success"])
        self.assertIn("routing", result["output"].lower())

    def test_handle_handoff_suggest(self) -> None:
        result = handle_handoff_suggest(_FakeBot(), "handoff suggest split the router")
        self.assertTrue(result["success"])
        self.assertIn("Delegation advice for:", result["output"])

    def test_handle_a2a_dispatch(self) -> None:
        result = handle_a2a(_FakeBot(), 'a2a dispatch planner {"task_id":"t-1"}', principal="tester")
        self.assertTrue(result["success"])
        self.assertIn("A2A dispatch via planner", result["output"])


if __name__ == "__main__":
    unittest.main()
