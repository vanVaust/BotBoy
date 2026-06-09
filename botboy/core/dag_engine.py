"""Mass Escalation Engine — Directed Acyclic Graph (DAG) High-Parallelism Fan-outs (V7)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Set
import botboy.tasks as btasks

logger = logging.getLogger(__name__)

class DAGNode:
    def __init__(self, task_id: str):
        self.task_id = task_id
        self.dependencies: Set[str] = set()
        self.dependents: Set[str] = set()
        self.status = "queued"

class MassEscalationEngine:
    """
    V7 Executor mapping the Task tree into a massive DAG.
    Optimizes for thousands of parallel fan-outs bounded only by the Governance Engine.
    """
    
    def __init__(self, task_store: btasks.TaskStore):
        self.store = task_store
        
    def build_dag(self, root_task_id: str) -> Dict[str, DAGNode]:
        """Constructs a memory DAG from the SQLite task tree."""
        # Simple recursion flattening
        records = botboy_tasks_records_by_root(self.store, root_task_id)
        nodes: Dict[str, DAGNode] = {}
        
        for rec in records:
            nodes[rec.task_id] = DAGNode(rec.task_id)
            nodes[rec.task_id].status = rec.status
            
        for rec in records:
            node = nodes[rec.task_id]
            if rec.parent_task_id and rec.parent_task_id in nodes:
                # The parent depends on the child completing in our hierarchy model
                parent_node = nodes[rec.parent_task_id]
                parent_node.dependencies.add(rec.task_id)
                node.dependents.add(rec.parent_task_id)
                
        return nodes

    def get_executable_fringe(self, dag: Dict[str, DAGNode]) -> List[str]:
        """Returns all queued tasks that have zero un-completed dependencies."""
        fringe = []
        for task_id, node in dag.items():
            if node.status != "queued":
                continue
                
            # Check if all dependencies are 'completed'
            ready = True
            for dep_id in node.dependencies:
                dep_node = dag.get(dep_id)
                if not dep_node or dep_node.status != "completed":
                    ready = False
                    break
            
            if ready:
                fringe.append(task_id)
                
        return fringe

    async def execute_fringe(self, root_task_id: str) -> int:
        """
        Calculates the execution fringe and instantly fans out execution leases.
        Returns the number of tasks successfully scheduled for massive parallel execution.
        """
        def _execute_sync():
            dag = self.build_dag(root_task_id)
            fringe = self.get_executable_fringe(dag)
            
            if not fringe:
                logger.debug(f"[MASS ESCALATION] Execution fringe empty for root {root_task_id}.")
                return 0
                
            logger.info(f"[MASS ESCALATION] Fanning out {len(fringe)} tasks in extreme parallel via DAG.")
            
            conn = self.store._get_conn()
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            
            scheduled = 0
            for task_id in fringe:
                # We flag them as "dispatching" before workers scoop them via leasing
                res = conn.execute(
                    "UPDATE tasks SET status = 'running', updated_at = ? WHERE task_id = ? AND status = 'queued'",
                    (now, task_id)
                )
                scheduled += res.rowcount
                
            conn.commit()
            return scheduled
            
        return await asyncio.to_thread(_execute_sync)

# Helper mimicking existing tree flattening
def botboy_tasks_records_by_root(store: btasks.TaskStore, root_task_id: str) -> List[Any]:
    rows = store._fetchall(
        "SELECT * FROM tasks WHERE root_task_id = ? ORDER BY created_at ASC",
        (root_task_id,),
    )
    return [btasks.TaskStore._row_to_task(row) for row in rows]
