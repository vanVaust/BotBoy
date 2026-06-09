import pytest
from botboy.tasks import TaskStore
from botboy.core.dag_engine import MassEscalationEngine, DAGNode

@pytest.fixture
def store():
    task_store = TaskStore(db_path=":memory:", artifact_root="")
    yield task_store
    task_store.close()

def test_dag_node_init():
    node = DAGNode("t1")
    assert node.task_id == "t1"
    assert len(node.dependencies) == 0
    assert len(node.dependents) == 0
    assert node.status == "queued"

def test_dag_engine_build_dag(store):
    engine = MassEscalationEngine(store)

    # Setup a tree: root -> child1, child2
    root = store.create_task(title="Root", command="cmd", principal="admin")
    child1 = store.create_task(parent_task_id=root.task_id, root_task_id=root.task_id, title="Child 1", command="cmd1", principal="admin")
    child2 = store.create_task(parent_task_id=root.task_id, root_task_id=root.task_id, title="Child 2", command="cmd2", principal="admin")

    dag = engine.build_dag(root.task_id)
    assert len(dag) == 3
    assert root.task_id in dag
    assert child1.task_id in dag
    assert child2.task_id in dag

    # The parent (root) should depend on the children completing
    assert child1.task_id in dag[root.task_id].dependencies
    assert child2.task_id in dag[root.task_id].dependencies

    # The children should have the parent as a dependent
    assert root.task_id in dag[child1.task_id].dependents
    assert root.task_id in dag[child2.task_id].dependents

def test_dag_engine_get_executable_fringe(store):
    engine = MassEscalationEngine(store)

    # Setup parent and child task
    root = store.create_task(title="Root", command="cmd", principal="admin")
    child1 = store.create_task(parent_task_id=root.task_id, root_task_id=root.task_id, title="Child 1", command="cmd1", principal="admin")

    # Both queued. Fringe should only contain the child
    dag = engine.build_dag(root.task_id)
    fringe = engine.get_executable_fringe(dag)
    assert fringe == [child1.task_id]

    # If child is completed, fringe should contain the parent (root)
    store.update_status(task_id=child1.task_id, status="completed", principal="admin")
    dag = engine.build_dag(root.task_id)
    fringe = engine.get_executable_fringe(dag)
    assert fringe == [root.task_id]

def test_dag_engine_execute_fringe(store):
    engine = MassEscalationEngine(store)

    # Create root and child task
    root = store.create_task(title="Root", command="cmd", principal="admin")
    child1 = store.create_task(parent_task_id=root.task_id, root_task_id=root.task_id, title="Child 1", command="cmd1", principal="admin")

    # Execute fringe
    scheduled = asyncio_run(engine.execute_fringe(root.task_id))
    assert scheduled == 1

    # Child should be transitioned from queued to running
    assert store.get_task(child1.task_id).status == "running"

def asyncio_run(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)
