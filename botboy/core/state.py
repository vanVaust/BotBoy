"""State Abstraction Layer for transparent local/cloud parity (V6)."""
from __future__ import annotations

import abc
from typing import Any, Dict, List, Optional
import botboy.tasks as btasks

class StateProvider(abc.ABC):
    """Abstract mapping for the Enterprise Agentic Database Schema."""
    
    @abc.abstractmethod
    def read_record(self, collection: str, record_id: str) -> Optional[Dict[str, Any]]:
        pass
        
    @abc.abstractmethod
    def write_record(self, collection: str, record_id: str, payload: Dict[str, Any]) -> bool:
        pass

class LocalSqliteState(StateProvider):
    """Binds the state provider to the local botboy _SQLiteMixin backing store."""
    def __init__(self, task_store: btasks.TaskStore):
        self.store = task_store
        
    def read_record(self, collection: str, record_id: str) -> Optional[Dict[str, Any]]:
        # Hardcoded proxy to demonstrate abstract V6 state wrapper
        conn = self.store._get_conn()
        if collection == "tasks":
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (record_id,)).fetchone()
            return dict(row) if row else None
        return None

    def write_record(self, collection: str, record_id: str, payload: Dict[str, Any]) -> bool:
        """Upserts a record into the local SQLite state."""
        conn = self.store._get_conn()
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        
        try:
            if collection == "tasks":
                # Check if exists
                exists = conn.execute("SELECT task_id FROM tasks WHERE task_id = ?", (record_id,)).fetchone()
                if exists:
                    status = payload.get("status", "queued")
                    metadata = self.store._json(payload.get("metadata", {}))
                    conn.execute("UPDATE tasks SET status = ?, payload_json = ?, updated_at = ? WHERE task_id = ?", 
                                 (status, metadata, now, record_id))
                else:
                    return False # For tasks, we require the task queue structure to init first
            conn.commit()
            return True
        except Exception:
            conn.rollback()
            return False

class CloudStateSync:
    """Seamlessly synchronizes local DB mutations into a remote State API (e.g. Postgres Proxy)."""
    
    def __init__(self, remote_url: str, auth_token: str):
        self.remote_url = remote_url
        self.auth_token = auth_token
        
    async def push_state_upsert(self, collection: str, record_id: str, payload: Dict[str, Any]) -> bool:
        """Transmits state mutation to the remote Cloud Postgres DB via HTTP."""
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {self.auth_token}", "Content-Type": "application/json"}
                url = f"{self.remote_url}/api/v1/sync/{collection}/{record_id}"
                async with session.put(url, json=payload, headers=headers, timeout=5.0) as resp:
                    return resp.status in (200, 201, 204)
        except Exception as e:
            # Degrade gracefully
            return False
