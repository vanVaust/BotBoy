"""Self-Healing Infrastructure — Auto-Retry and Capabilities Auto-Discovery (V7)."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
import botboy.tasks as btasks

logger = logging.getLogger(__name__)

class SelfHealingEngine:
    """Monitors the runtime fabric and heals broken task allocations or discovers new paths."""
    
    def __init__(self, task_store: btasks.TaskStore):
        self.store = task_store
        
    def scan_for_stale_tasks(self, timeout_seconds: int = 3600) -> List[dict]:
        """Finds tasks stuck in 'running' state beyond their allowed timeout."""
        conn = self.store._get_conn()
        from datetime import datetime, timezone, timedelta
        
        threshold = (datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)).isoformat()
        
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status = 'running' AND updated_at < ?",
            (threshold,)
        ).fetchall()
        
        return [dict(row) for row in rows]

    def execute_auto_retry(self, task_id: str, max_retries: int = 3) -> bool:
        """Automatically requeues a failed or stale task if it hasn't exceeded its retry limit."""
        conn = self.store._get_conn()
        now = btasks.TaskStore._now() # Safe access logic
        
        task = getattr(self.store, "get_task", lambda tid: None)(task_id)
        if not task:
            return False
            
        # Normally tasks metadata holds _retry_count in V7.
        payload = task.payload if isinstance(task.payload, dict) else {}
        retries = payload.get("_retry_count", 0)
        
        if retries >= max_retries:
            logger.error(f"[SELF-HEALING] Task {task_id} exhausted max retries ({max_retries}). Marking Failed.")
            conn.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?", ("failed", now, task_id))
            conn.commit()
            return False
            
        payload["_retry_count"] = retries + 1
        
        # Pull the task back to Q, clear worker assignment so any capable node can pick it
        conn.execute(
            "UPDATE tasks SET status = ?, delegated_to_worker = '', payload_json = ?, updated_at = ? WHERE task_id = ?",
            ("queued", self.store._json(payload), now, task_id)
        )
        conn.commit()
        logger.info(f"[SELF-HEALING] Task {task_id} successfully auto-retried (Attempt {retries + 1}).")
        return True

    def discover_alternative_capabilities(self, required_capabilities: List[str]) -> List[str]:
        """
        If a task requires capabilities no active node has, 
        this discovers semantic equivalents using the V6 AgentSkillLibrary definitions.
        """
        logger.info(f"[SELF-HEALING] Discovering alternatives for {required_capabilities} (Fallback matrix activation).")
        
        # In a full cluster, this queries the AgentSkillLibrary tags.
        # Here we apply a hardened offline fallback matrix to prevent tasks getting stuck indefinitely.
        fallback_matrix = {
            "web_search": ["duckduckgo_search", "google_search", "browser_automation"],
            "python_execute": ["sandbox_python", "docker_python", "bash_execute"],
            "data_analysis": ["python_execute", "sql_query"],
            "file_read": ["bash_execute", "sandbox_python"],
            "image_gen": ["dall_e", "stable_diffusion"]
        }
        
        rescued = []
        for cap in required_capabilities:
            if cap in fallback_matrix:
                # Provide the first available alternative as a heuristic fallback
                rescued.append(fallback_matrix[cap][0])
            else:
                rescued.append(cap)
                
        return rescued
