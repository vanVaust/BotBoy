from __future__ import annotations

import unittest

from botboy.security.governance import AutonomyEnvelope, GovernanceEngine
from botboy.tasks import TaskStore


class GovernanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = TaskStore(db_path=":memory:", artifact_root="")
        self.addCleanup(self.store.close)

    def test_governance_envelope_management(self) -> None:
        engine = GovernanceEngine(self.store)

        env = engine.get_envelope("org-1")
        self.assertIsInstance(env, AutonomyEnvelope)
        self.assertEqual(env.org_id, "org-1")
        self.assertEqual(env.max_budget_dollars, 10.0)

        engine.set_envelope(AutonomyEnvelope(org_id="org-1", max_budget_dollars=50.0))
        self.assertEqual(engine.get_envelope("org-1").max_budget_dollars, 50.0)

    def test_governance_enforce_pre_execution_budget(self) -> None:
        engine = GovernanceEngine(self.store)
        engine.set_envelope(
            AutonomyEnvelope(org_id="org-1", max_budget_dollars=10.0, current_spend_dollars=12.0)
        )

        self.assertFalse(engine.enforce_pre_execution("org-1", "some_skill", "low"))

    def test_governance_enforce_pre_execution_forbidden_skills(self) -> None:
        engine = GovernanceEngine(self.store)
        engine.set_envelope(AutonomyEnvelope(org_id="org-1", forbidden_skills=["dangerous_skill"]))

        self.assertFalse(engine.enforce_pre_execution("org-1", "dangerous_skill", "low"))
        self.assertTrue(engine.enforce_pre_execution("org-1", "safe_skill", "low"))

    def test_governance_enforce_pre_execution_risk_level(self) -> None:
        engine = GovernanceEngine(self.store)
        engine.set_envelope(AutonomyEnvelope(org_id="org-1", allowed_risk_level="medium"))

        self.assertFalse(engine.enforce_pre_execution("org-1", "some_skill", "high"))
        self.assertTrue(engine.enforce_pre_execution("org-1", "some_skill", "low"))

    def test_governance_enforce_pre_execution_parallel_tasks(self) -> None:
        engine = GovernanceEngine(self.store)
        engine.set_envelope(AutonomyEnvelope(org_id="org-1", max_parallel_tasks=2))

        task1 = self.store.create_task(title="Task 1", command="cmd1", principal="admin", org_id="org-1")
        self.store.update_status(task_id=task1.task_id, status="running", principal="admin")
        task2 = self.store.create_task(title="Task 2", command="cmd2", principal="admin", org_id="org-1")
        self.store.update_status(task_id=task2.task_id, status="running", principal="admin")

        self.assertFalse(engine.enforce_pre_execution("org-1", "some_skill", "low"))

        self.store.update_status(task_id=task1.task_id, status="completed", principal="admin")
        self.assertTrue(engine.enforce_pre_execution("org-1", "some_skill", "low"))

    def test_governance_incident_playbook_pause(self) -> None:
        engine = GovernanceEngine(self.store)
        task1 = self.store.create_task(title="Task 1", command="cmd1", principal="admin", root_task_id="root-1")
        self.store.update_status(task_id=task1.task_id, status="running", principal="admin")
        task2 = self.store.create_task(title="Task 2", command="cmd2", principal="admin", root_task_id="root-1")
        self.store.update_status(task_id=task2.task_id, status="queued", principal="admin")

        paused = engine.incident_playbook_pause("root-1", reason="Testing pause")

        self.assertEqual(paused, 2)
        self.assertEqual(self.store.get_task(task1.task_id).status, "blocked")
        self.assertEqual(self.store.get_task(task2.task_id).status, "blocked")

    def test_governance_incident_playbook_quarantine(self) -> None:
        engine = GovernanceEngine(self.store)
        self.store.register_worker_node(worker_id="reviewer", node_id="n-1")
        task1 = self.store.create_task(title="Task 1", command="cmd1", principal="admin")
        self.store.update_task(task_id=task1.task_id, status="running", delegated_to_worker="reviewer")

        reassigned = engine.incident_playbook_quarantine("reviewer", reason="Compromised")

        self.assertEqual(reassigned, 1)
        worker = self.store.list_worker_nodes(worker_id="reviewer")[0]
        self.assertEqual(worker["node_status"], "quarantined")
        self.assertEqual(worker["drain_state"], "draining")
        task = self.store.get_task(task1.task_id)
        self.assertEqual(task.status, "queued")
        self.assertEqual(task.delegated_to_worker, "")

    def test_governance_incident_playbook_rollback(self) -> None:
        engine = GovernanceEngine(self.store)
        task1 = self.store.create_task(title="Task 1", command="cmd1", principal="admin")
        self.store.update_task(task_id=task1.task_id, status="failed", delegated_to_worker="reviewer")

        self.assertTrue(engine.incident_playbook_rollback(task1.task_id))
        task = self.store.get_task(task1.task_id)
        self.assertEqual(task.status, "queued")
        self.assertEqual(task.delegated_to_worker, "")


if __name__ == "__main__":
    unittest.main()
