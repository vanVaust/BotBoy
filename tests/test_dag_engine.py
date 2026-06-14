from __future__ import annotations

import asyncio
import unittest

from botboy.core.dag_engine import DAGNode, MassEscalationEngine
from botboy.tasks import TaskStore


class DagEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.store = TaskStore(db_path=":memory:", artifact_root="")
        self.addCleanup(self.store.close)

    def test_dag_node_init(self) -> None:
        node = DAGNode("t1")
        self.assertEqual(node.task_id, "t1")
        self.assertEqual(len(node.dependencies), 0)
        self.assertEqual(len(node.dependents), 0)
        self.assertEqual(node.status, "queued")

    def test_dag_engine_build_dag(self) -> None:
        engine = MassEscalationEngine(self.store)
        root = self.store.create_task(title="Root", command="cmd", principal="admin")
        child1 = self.store.create_task(
            parent_task_id=root.task_id,
            root_task_id=root.task_id,
            title="Child 1",
            command="cmd1",
            principal="admin",
        )
        child2 = self.store.create_task(
            parent_task_id=root.task_id,
            root_task_id=root.task_id,
            title="Child 2",
            command="cmd2",
            principal="admin",
        )

        dag = engine.build_dag(root.task_id)

        self.assertEqual(len(dag), 3)
        self.assertIn(root.task_id, dag)
        self.assertIn(child1.task_id, dag)
        self.assertIn(child2.task_id, dag)
        self.assertIn(child1.task_id, dag[root.task_id].dependencies)
        self.assertIn(child2.task_id, dag[root.task_id].dependencies)
        self.assertIn(root.task_id, dag[child1.task_id].dependents)
        self.assertIn(root.task_id, dag[child2.task_id].dependents)

    def test_dag_engine_get_executable_fringe(self) -> None:
        engine = MassEscalationEngine(self.store)
        root = self.store.create_task(title="Root", command="cmd", principal="admin")
        child1 = self.store.create_task(
            parent_task_id=root.task_id,
            root_task_id=root.task_id,
            title="Child 1",
            command="cmd1",
            principal="admin",
        )

        dag = engine.build_dag(root.task_id)
        self.assertEqual(engine.get_executable_fringe(dag), [child1.task_id])

        self.store.update_status(task_id=child1.task_id, status="completed", principal="admin")
        dag = engine.build_dag(root.task_id)
        self.assertEqual(engine.get_executable_fringe(dag), [root.task_id])

    def test_dag_engine_execute_fringe(self) -> None:
        engine = MassEscalationEngine(self.store)
        root = self.store.create_task(title="Root", command="cmd", principal="admin")
        child1 = self.store.create_task(
            parent_task_id=root.task_id,
            root_task_id=root.task_id,
            title="Child 1",
            command="cmd1",
            principal="admin",
        )

        scheduled = asyncio.run(engine.execute_fringe(root.task_id))

        self.assertEqual(scheduled, 1)
        self.assertEqual(self.store.get_task(child1.task_id).status, "running")


if __name__ == "__main__":
    unittest.main()
