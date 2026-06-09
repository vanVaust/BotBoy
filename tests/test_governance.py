import pytest
from botboy.tasks import TaskStore
from botboy.security.governance import GovernanceEngine, AutonomyEnvelope

@pytest.fixture
def store():
    task_store = TaskStore(db_path=":memory:", artifact_root="")
    yield task_store
    task_store.close()

def test_governance_envelope_management(store):
    engine = GovernanceEngine(store)

    # Test default envelope
    env = engine.get_envelope("org-1")
    assert isinstance(env, AutonomyEnvelope)
    assert env.org_id == "org-1"
    assert env.max_budget_dollars == 10.0

    # Test setting envelope
    custom_env = AutonomyEnvelope(org_id="org-1", max_budget_dollars=50.0)
    engine.set_envelope(custom_env)
    assert engine.get_envelope("org-1").max_budget_dollars == 50.0

def test_governance_enforce_pre_execution_budget(store):
    engine = GovernanceEngine(store)
    env = AutonomyEnvelope(org_id="org-1", max_budget_dollars=10.0, current_spend_dollars=12.0)
    engine.set_envelope(env)

    # Budget exhausted
    assert engine.enforce_pre_execution("org-1", "some_skill", "low") is False

def test_governance_enforce_pre_execution_forbidden_skills(store):
    engine = GovernanceEngine(store)
    env = AutonomyEnvelope(org_id="org-1", forbidden_skills=["dangerous_skill"])
    engine.set_envelope(env)

    # Forbidden skill
    assert engine.enforce_pre_execution("org-1", "dangerous_skill", "low") is False
    # Allowed skill
    assert engine.enforce_pre_execution("org-1", "safe_skill", "low") is True

def test_governance_enforce_pre_execution_risk_level(store):
    engine = GovernanceEngine(store)
    env = AutonomyEnvelope(org_id="org-1", allowed_risk_level="medium")
    engine.set_envelope(env)

    # Risk too high
    assert engine.enforce_pre_execution("org-1", "some_skill", "high") is False
    # Risk allowed
    assert engine.enforce_pre_execution("org-1", "some_skill", "low") is True

def test_governance_enforce_pre_execution_parallel_tasks(store):
    engine = GovernanceEngine(store)
    env = AutonomyEnvelope(org_id="org-1", max_parallel_tasks=2)
    engine.set_envelope(env)

    # Create 2 running tasks in the store
    task1 = store.create_task(title="Task 1", command="cmd1", principal="admin", org_id="org-1")
    store.update_status(task_id=task1.task_id, status="running", principal="admin")
    
    task2 = store.create_task(title="Task 2", command="cmd2", principal="admin", org_id="org-1")
    store.update_status(task_id=task2.task_id, status="running", principal="admin")

    # Limit reached
    assert engine.enforce_pre_execution("org-1", "some_skill", "low") is False

    # Complete one task
    store.update_status(task_id=task1.task_id, status="completed", principal="admin")
    # Now allowed
    assert engine.enforce_pre_execution("org-1", "some_skill", "low") is True

def test_governance_incident_playbook_pause(store):
    engine = GovernanceEngine(store)

    task1 = store.create_task(title="Task 1", command="cmd1", principal="admin", root_task_id="root-1")
    store.update_status(task_id=task1.task_id, status="running", principal="admin")
    
    task2 = store.create_task(title="Task 2", command="cmd2", principal="admin", root_task_id="root-1")
    store.update_status(task_id=task2.task_id, status="queued", principal="admin")

    paused = engine.incident_playbook_pause("root-1", reason="Testing pause")
    assert paused == 2

    # Check status
    assert store.get_task(task1.task_id).status == "blocked"
    assert store.get_task(task2.task_id).status == "blocked"

def test_governance_incident_playbook_quarantine(store):
    engine = GovernanceEngine(store)

    # Register worker node (use a valid worker_id from profile like 'reviewer')
    store.register_worker_node(worker_id="reviewer", node_id="n-1")
    
    task1 = store.create_task(title="Task 1", command="cmd1", principal="admin")
    # Manually set task as delegated/running under worker
    store.update_task(task_id=task1.task_id, status="running", delegated_to_worker="reviewer")

    reassigned = engine.incident_playbook_quarantine("reviewer", reason="Compromised")
    assert reassigned == 1

    # Check worker status in db
    worker = store.list_worker_nodes(worker_id="reviewer")[0]
    assert worker["node_status"] == "quarantined"
    assert worker["drain_state"] == "draining"

    # Check task was requeued
    task = store.get_task(task1.task_id)
    assert task.status == "queued"
    assert task.delegated_to_worker == ""

def test_governance_incident_playbook_rollback(store):
    engine = GovernanceEngine(store)

    task1 = store.create_task(title="Task 1", command="cmd1", principal="admin")
    store.update_task(task_id=task1.task_id, status="failed", delegated_to_worker="reviewer")

    success = engine.incident_playbook_rollback(task1.task_id)
    assert success is True

    # Check task was reset
    task = store.get_task(task1.task_id)
    assert task.status == "queued"
    assert task.delegated_to_worker == ""
