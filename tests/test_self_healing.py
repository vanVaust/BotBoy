import pytest
from datetime import datetime, timezone, timedelta
from botboy.tasks import TaskStore
from botboy.core.self_healing import SelfHealingEngine

@pytest.fixture
def store():
    task_store = TaskStore(db_path=":memory:", artifact_root="")
    yield task_store
    task_store.close()

def test_self_healing_scan_for_stale_tasks(store):
    engine = SelfHealingEngine(store)

    # Create running task
    task1 = store.create_task(title="Task 1", command="cmd1", principal="admin")
    store.update_status(task_id=task1.task_id, status="running", principal="admin")

    # The task updated_at is currently new. scan_for_stale_tasks shouldn't find it
    stale = engine.scan_for_stale_tasks(timeout_seconds=60)
    assert len(stale) == 0

    # Backdate the updated_at column
    old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
    conn = store._get_conn()
    conn.execute("UPDATE tasks SET updated_at = ? WHERE task_id = ?", (old_time, task1.task_id))
    conn.commit()

    # Now it should be stale
    stale = engine.scan_for_stale_tasks(timeout_seconds=60)
    assert len(stale) == 1
    assert stale[0]["task_id"] == task1.task_id

def test_self_healing_execute_auto_retry(store):
    engine = SelfHealingEngine(store)

    # Create task with a payload
    task1 = store.create_task(title="Task 1", command="cmd1", principal="admin", payload={"foo": "bar"})
    store.update_task(task_id=task1.task_id, status="running", delegated_to_worker="w-1")

    # Execute retry (first attempt)
    success = engine.execute_auto_retry(task1.task_id, max_retries=3)
    assert success is True

    # Check status and payload
    task = store.get_task(task1.task_id)
    assert task.status == "queued"
    assert task.delegated_to_worker == ""
    assert task.payload.get("_retry_count") == 1

    # Update status back to running for next retry
    store.update_task(task_id=task1.task_id, status="running", delegated_to_worker="w-1")

    # Retry again (second attempt)
    success = engine.execute_auto_retry(task1.task_id, max_retries=3)
    assert success is True
    assert store.get_task(task1.task_id).payload.get("_retry_count") == 2

    # Update status to running again
    store.update_task(task_id=task1.task_id, status="running", delegated_to_worker="w-1")

    # Retry again (third attempt)
    success = engine.execute_auto_retry(task1.task_id, max_retries=3)
    assert success is True
    assert store.get_task(task1.task_id).payload.get("_retry_count") == 3

    # Update status to running again
    store.update_task(task_id=task1.task_id, status="running", delegated_to_worker="w-1")

    # Retry again (fourth attempt - should fail because limit is 3)
    success = engine.execute_auto_retry(task1.task_id, max_retries=3)
    assert success is False
    assert store.get_task(task1.task_id).status == "failed"

def test_self_healing_discover_alternative_capabilities():
    engine = SelfHealingEngine(None)

    caps = ["web_search", "python_execute", "unknown_capability"]
    discovered = engine.discover_alternative_capabilities(caps)

    assert "duckduckgo_search" in discovered
    assert "sandbox_python" in discovered
    assert "unknown_capability" in discovered
