from __future__ import annotations

import http.client
import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

from botboy.__main__ import BotBoy
from botboy.core.config import BotBoyConfig
from botboy.gateway.security import default_bind_host
from botboy.gateway.simple_server import SimpleHTTPServer
from botboy.tasks import TASK_STATUS_RUNNING


ROOT = Path(__file__).resolve().parents[1]


def _release_standard_site_packages() -> Path:
    return ROOT / ".release-build-venv" / "Lib" / "site-packages"


def _load_fastapi_testclient():
    try:
        from fastapi.testclient import TestClient  # type: ignore
        return TestClient
    except ImportError:
        site_packages = _release_standard_site_packages()
        if not site_packages.exists():
            raise unittest.SkipTest("FastAPI test client is unavailable and no workspace standard env exists.")
        if str(site_packages) not in sys.path:
            sys.path.insert(0, str(site_packages))
        from fastapi.testclient import TestClient  # type: ignore
        return TestClient


def _load_fastapi_gateway_factory():
    site_packages = _release_standard_site_packages()
    if site_packages.exists() and str(site_packages) not in sys.path:
        sys.path.insert(0, str(site_packages))
    from botboy.gateway.server import create_app

    return create_app


class ReleaseGatewayLiveTest(unittest.TestCase):
    def _make_bot(self) -> BotBoy:
        base_root = (ROOT / ".botboy-runtime" / "gateway-live-tests").resolve()
        base_root.mkdir(parents=True, exist_ok=True)
        temp_root = Path(tempfile.mkdtemp(prefix=f"{self._testMethodName}-", dir=base_root)).resolve()
        self.addCleanup(lambda: shutil.rmtree(temp_root, ignore_errors=True))
        config = BotBoyConfig()
        config.memory.db_path = str(temp_root / "botboy.db")
        config.history.db_path = str(temp_root / "history.db")
        config.scheduler.db_path = str(temp_root / "scheduler.db")
        config.trace.db_path = str(temp_root / "traces.db")
        config.tasks.db_path = str(temp_root / "tasks.db")
        config.tasks.artifact_root = str(temp_root / "artifacts" / "tasks")
        config.security.principal_db_path = str(temp_root / "principals.db")
        config.security.auth_api_key_store_path = str(temp_root / "api_keys.db")
        config.security.jwt_secret_file = str(temp_root / "jwt.secret")
        config.security.enable_auth = False
        config.llm.enabled = False
        bot = BotBoy(config)
        self.assertTrue(bot.initialize())
        self.addCleanup(bot.shutdown)
        return bot

    def _seed_task_store(self, bot: BotBoy) -> tuple[str, str]:
        root = bot.task_store.create_task(
            title="release gateway live root",
            principal="tester",
            request_id="req-gateway-live",
            command="status",
            status="queued",
        )
        bot.task_store.create_child_task(
            root.task_id,
            worker_id="planner",
            title="delegated child",
            principal="tester",
            request_id="req-gateway-live",
            command="status",
            status=TASK_STATUS_RUNNING,
            summary="delegated to planner",
        )
        bot.task_store.add_artifact(
            root.task_id,
            category="report",
            label="gateway-live.txt",
            content="ok\n",
            media_type="text/plain",
            suffix=".txt",
        )
        trace = bot.trace_store.create_run(
            "status",
            principal="tester",
            request_id="req-gateway-live",
            task_id=root.task_id,
        )
        bot.trace_store.finish_run(trace.run_id, status="completed", cmd_type="status", summary="gateway live trace")
        return root.task_id, trace.run_id

    def test_fastapi_gateway_live_routes(self) -> None:
        TestClient = _load_fastapi_testclient()
        create_app = _load_fastapi_gateway_factory()
        bot = self._make_bot()
        task_id, trace_run_id = self._seed_task_store(bot)
        app = create_app(bot, host=default_bind_host(), port=8765)
        with TestClient(app) as client:
            health = client.get("/health")
            status = client.get("/api/status")
            skills = client.get("/api/skills")
            memories = client.get("/api/memories")
            memory_create = client.post("/api/memories", json={"content": "release-live-memory"})
            command = client.post("/api/command", json={"command": "status"})
            traces = client.get("/api/traces")
            trace_detail = client.get(f"/api/traces/{trace_run_id}")
            tasks = client.get("/api/tasks")
            task_detail = client.get(f"/api/tasks/{task_id}")
            task_merge = client.get(f"/api/tasks/{task_id}/merge")
            task_children = client.get(f"/api/tasks/{task_id}/children")
            task_events = client.get(f"/api/tasks/{task_id}/events")
            task_artifacts = client.get(f"/api/tasks/{task_id}/artifacts")
            blockers = client.get("/api/tasks/blockers")
            workers = client.get("/api/workers")
            worker_detail = client.get("/api/workers/planner")
            worker_node_register = client.post(
                "/api/v2/workers/register",
                json={"node_id": "node-fastapi", "worker_id": "planner", "health": "unknown"},
            )
            worker_lease_acquire = client.post(
                "/api/v2/workers/leases/acquire",
                json={"queue_name": "planner.node-fastapi", "node_id": "node-fastapi", "task_id": task_id},
            )
            lease_id = worker_lease_acquire.json().get("lease", {}).get("lease_id", "")
            worker_leases = client.get("/api/v2/workers/leases")
            worker_lease_renew = client.post(
                "/api/v2/workers/leases/renew",
                json={"lease_id": lease_id},
            )
            worker_lease_release = client.post(
                "/api/v2/workers/leases/release",
                json={"lease_id": lease_id, "reason": "release-live-test"},
            )
            worker_nodes = client.get("/api/v2/workers/nodes")
            worker_node_heartbeat = client.post(
                "/api/v2/workers/heartbeat",
                json={"node_id": "node-fastapi", "status": "running", "health": "healthy", "load": 0.25},
            )
            worker_node_drain = client.post(
                "/api/v2/workers/node-fastapi/drain",
                json={"reason": "maintenance"},
            )
            history_stats = client.get("/api/history/stats")
            scheduler = client.get("/api/scheduler")
            cors = client.options(
                "/api/status",
                headers={
                    "Origin": "http://127.0.0.1:8765",
                    "Access-Control-Request-Method": "GET",
                },
            )

        self.assertEqual(health.status_code, 200, health.text)
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(skills.status_code, 200, skills.text)
        self.assertEqual(memories.status_code, 200, memories.text)
        self.assertEqual(memory_create.status_code, 200, memory_create.text)
        self.assertEqual(command.status_code, 200, command.text)
        self.assertEqual(traces.status_code, 200, traces.text)
        self.assertEqual(trace_detail.status_code, 200, trace_detail.text)
        self.assertEqual(tasks.status_code, 200, tasks.text)
        self.assertEqual(task_detail.status_code, 200, task_detail.text)
        self.assertEqual(task_merge.status_code, 200, task_merge.text)
        self.assertEqual(task_children.status_code, 200, task_children.text)
        self.assertEqual(task_events.status_code, 200, task_events.text)
        self.assertEqual(task_artifacts.status_code, 200, task_artifacts.text)
        self.assertEqual(blockers.status_code, 200, blockers.text)
        self.assertEqual(workers.status_code, 200, workers.text)
        self.assertEqual(worker_detail.status_code, 200, worker_detail.text)
        self.assertEqual(worker_node_register.status_code, 200, worker_node_register.text)
        self.assertEqual(worker_lease_acquire.status_code, 200, worker_lease_acquire.text)
        self.assertEqual(worker_leases.status_code, 200, worker_leases.text)
        self.assertEqual(worker_lease_renew.status_code, 200, worker_lease_renew.text)
        self.assertEqual(worker_lease_release.status_code, 200, worker_lease_release.text)
        self.assertEqual(worker_nodes.status_code, 200, worker_nodes.text)
        self.assertEqual(worker_node_heartbeat.status_code, 200, worker_node_heartbeat.text)
        self.assertEqual(worker_node_drain.status_code, 200, worker_node_drain.text)
        self.assertEqual(history_stats.status_code, 200, history_stats.text)
        self.assertEqual(scheduler.status_code, 200, scheduler.text)
        self.assertEqual(cors.status_code, 200, cors.text)
        self.assertEqual(health.json()["mode"], "fastapi")
        self.assertTrue(status.json()["success"])
        self.assertIn("skills", skills.json())
        self.assertIn("memories", memories.json())
        self.assertTrue(memory_create.json()["success"])
        self.assertTrue(command.json()["success"])
        self.assertIn("runs", traces.json())
        self.assertEqual(trace_detail.json()["run"]["run_id"], trace_run_id)
        self.assertTrue(tasks.json()["available"])
        self.assertEqual(task_detail.json()["task"]["task_id"], task_id)
        self.assertIn("merge", task_merge.json())
        self.assertEqual(task_children.json()["task_id"], task_id)
        self.assertEqual(task_events.json()["task_id"], task_id)
        self.assertEqual(task_artifacts.json()["task_id"], task_id)
        self.assertIn("blockers", blockers.json())
        self.assertIn("worker_count", workers.json())
        self.assertEqual(worker_detail.json()["worker"]["worker_id"], "planner")
        self.assertEqual(worker_node_register.json()["node"]["node_id"], "node-fastapi")
        self.assertTrue(worker_lease_acquire.json()["acquired"])
        self.assertGreaterEqual(worker_leases.json()["summary"]["lease_count"], 1)
        self.assertTrue(worker_lease_renew.json()["renewed"])
        self.assertEqual(worker_lease_release.json()["lease"]["lease_status"], "released")
        self.assertEqual(worker_nodes.json()["summary"]["node_count"], 1)
        self.assertEqual(worker_node_heartbeat.json()["node"]["health"], "healthy")
        self.assertEqual(worker_node_drain.json()["node"]["effective_status"], "draining")
        self.assertIn("total", history_stats.json())
        self.assertIn("tasks", scheduler.json())
        self.assertEqual(cors.headers.get("access-control-allow-origin"), "http://127.0.0.1:8765")

        with TestClient(app) as client:
            with client.websocket_connect("/ws/chat") as websocket:
                websocket.send_text("status")
                ws_message = websocket.receive_json()

        self.assertTrue(ws_message["success"])
        self.assertIn(ws_message["type"], {"result", "status"})

    def test_stdlib_gateway_live_routes(self) -> None:
        bot = self._make_bot()
        task_id, trace_run_id = self._seed_task_store(bot)
        server = SimpleHTTPServer(bot, host=default_bind_host(), port=0)
        server.start(blocking=False)
        self.addCleanup(server.stop)
        time.sleep(0.2)

        def request(
            method: str,
            path: str,
            *,
            headers: dict[str, str] | None = None,
            body: str | None = None,
        ) -> tuple[int, dict[str, str], str]:
            connection = http.client.HTTPConnection(server.host, server.port, timeout=10)
            try:
                connection.request(method, path, body=body, headers=headers or {})
                response = connection.getresponse()
                body = response.read().decode("utf-8")
                return response.status, {key.lower(): value for key, value in response.getheaders()}, body
            finally:
                connection.close()

        health_status, _, health_body = request("GET", "/health")
        status_status, _, status_body = request("GET", "/api/status")
        skills_status, _, skills_body = request("GET", "/api/skills")
        memories_status, _, memories_body = request("GET", "/api/memories")
        memory_create_status, _, memory_create_body = request(
            "POST",
            "/api/memories",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"content": "release-live-memory"}),
        )
        command_status, _, command_body = request(
            "POST",
            "/api/command",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"command": "status"}),
        )
        traces_status, _, traces_body = request("GET", "/api/traces")
        trace_detail_status, _, trace_detail_body = request("GET", f"/api/traces/{trace_run_id}")
        tasks_status, _, tasks_body = request("GET", "/api/tasks")
        task_detail_status, _, task_detail_body = request("GET", f"/api/tasks/{task_id}")
        task_merge_status, _, task_merge_body = request("GET", f"/api/tasks/{task_id}/merge")
        task_children_status, _, task_children_body = request("GET", f"/api/tasks/{task_id}/children")
        task_events_status, _, task_events_body = request("GET", f"/api/tasks/{task_id}/events")
        task_artifacts_status, _, task_artifacts_body = request("GET", f"/api/tasks/{task_id}/artifacts")
        blockers_status, _, blockers_body = request("GET", "/api/tasks/blockers")
        workers_status, _, workers_body = request("GET", "/api/workers")
        worker_detail_status, _, worker_detail_body = request("GET", "/api/workers/planner")
        worker_node_register_status, _, worker_node_register_body = request(
            "POST",
            "/api/v2/workers/register",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"node_id": "node-stdlib", "worker_id": "planner"}),
        )
        worker_lease_acquire_status, _, worker_lease_acquire_body = request(
            "POST",
            "/api/v2/workers/leases/acquire",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"queue_name": "planner.node-stdlib", "node_id": "node-stdlib", "task_id": task_id}),
        )
        worker_lease_acquire_probe = json.loads(worker_lease_acquire_body)
        stdlib_lease_id = worker_lease_acquire_probe.get("lease", {}).get("lease_id", "")
        worker_leases_status, _, worker_leases_body = request("GET", "/api/v2/workers/leases")
        worker_lease_renew_status, _, worker_lease_renew_body = request(
            "POST",
            "/api/v2/workers/leases/renew",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"lease_id": stdlib_lease_id}),
        )
        worker_lease_release_status, _, worker_lease_release_body = request(
            "POST",
            "/api/v2/workers/leases/release",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"lease_id": stdlib_lease_id, "reason": "release-live-test"}),
        )
        worker_nodes_status, _, worker_nodes_body = request("GET", "/api/v2/workers/nodes")
        worker_node_heartbeat_status, _, worker_node_heartbeat_body = request(
            "POST",
            "/api/v2/workers/heartbeat",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"node_id": "node-stdlib", "status": "running", "health": "healthy", "load": 0.1}),
        )
        worker_node_drain_status, _, worker_node_drain_body = request(
            "POST",
            "/api/v2/workers/node-stdlib/drain",
            headers={"Content-Type": "application/json"},
            body=json.dumps({"reason": "maintenance"}),
        )
        history_stats_status, _, history_stats_body = request("GET", "/api/history/stats")
        scheduler_status, _, scheduler_body = request("GET", "/api/scheduler")
        cors_status, cors_headers, _ = request(
            "OPTIONS",
            "/api/status",
            headers={
                "Origin": f"http://127.0.0.1:{server.port}",
                "Access-Control-Request-Method": "GET",
            },
        )

        health = json.loads(health_body)
        status = json.loads(status_body)
        skills = json.loads(skills_body)
        memories = json.loads(memories_body)
        memory_create = json.loads(memory_create_body)
        command = json.loads(command_body)
        traces = json.loads(traces_body)
        trace_detail = json.loads(trace_detail_body)
        tasks = json.loads(tasks_body)
        task_detail = json.loads(task_detail_body)
        task_merge = json.loads(task_merge_body)
        task_children = json.loads(task_children_body)
        task_events = json.loads(task_events_body)
        task_artifacts = json.loads(task_artifacts_body)
        blockers = json.loads(blockers_body)
        workers = json.loads(workers_body)
        worker_detail = json.loads(worker_detail_body)
        worker_node_register = json.loads(worker_node_register_body)
        worker_lease_acquire = json.loads(worker_lease_acquire_body)
        worker_leases = json.loads(worker_leases_body)
        worker_lease_renew = json.loads(worker_lease_renew_body)
        worker_lease_release = json.loads(worker_lease_release_body)
        worker_nodes = json.loads(worker_nodes_body)
        worker_node_heartbeat = json.loads(worker_node_heartbeat_body)
        worker_node_drain = json.loads(worker_node_drain_body)
        history_stats = json.loads(history_stats_body)
        scheduler = json.loads(scheduler_body)

        self.assertEqual(health_status, 200, health_body)
        self.assertEqual(status_status, 200, status_body)
        self.assertEqual(skills_status, 200, skills_body)
        self.assertEqual(memories_status, 200, memories_body)
        self.assertEqual(memory_create_status, 200, memory_create_body)
        self.assertEqual(command_status, 200, command_body)
        self.assertEqual(traces_status, 200, traces_body)
        self.assertEqual(trace_detail_status, 200, trace_detail_body)
        self.assertEqual(tasks_status, 200, tasks_body)
        self.assertEqual(task_detail_status, 200, task_detail_body)
        self.assertEqual(task_merge_status, 200, task_merge_body)
        self.assertEqual(task_children_status, 200, task_children_body)
        self.assertEqual(task_events_status, 200, task_events_body)
        self.assertEqual(task_artifacts_status, 200, task_artifacts_body)
        self.assertEqual(blockers_status, 200, blockers_body)
        self.assertEqual(workers_status, 200, workers_body)
        self.assertEqual(worker_detail_status, 200, worker_detail_body)
        self.assertEqual(worker_node_register_status, 200, worker_node_register_body)
        self.assertEqual(worker_lease_acquire_status, 200, worker_lease_acquire_body)
        self.assertEqual(worker_leases_status, 200, worker_leases_body)
        self.assertEqual(worker_lease_renew_status, 200, worker_lease_renew_body)
        self.assertEqual(worker_lease_release_status, 200, worker_lease_release_body)
        self.assertEqual(worker_nodes_status, 200, worker_nodes_body)
        self.assertEqual(worker_node_heartbeat_status, 200, worker_node_heartbeat_body)
        self.assertEqual(worker_node_drain_status, 200, worker_node_drain_body)
        self.assertEqual(history_stats_status, 200, history_stats_body)
        self.assertEqual(scheduler_status, 200, scheduler_body)
        self.assertEqual(cors_status, 200)
        self.assertEqual(health["mode"], "stdlib")
        self.assertTrue(status["success"])
        self.assertIn("skills", skills)
        self.assertIn("memories", memories)
        self.assertTrue(memory_create["success"])
        self.assertTrue(command["success"])
        self.assertIn("runs", traces)
        self.assertEqual(trace_detail["run"]["run_id"], trace_run_id)
        self.assertTrue(tasks["available"])
        self.assertEqual(task_detail["task"]["task_id"], task_id)
        self.assertIn("merge", task_merge)
        self.assertEqual(task_children["task_id"], task_id)
        self.assertEqual(task_events["task_id"], task_id)
        self.assertEqual(task_artifacts["task_id"], task_id)
        self.assertIn("blockers", blockers)
        self.assertIn("worker_count", workers)
        self.assertEqual(worker_detail["worker"]["worker_id"], "planner")
        self.assertEqual(worker_node_register["node"]["node_id"], "node-stdlib")
        self.assertTrue(worker_lease_acquire["acquired"])
        self.assertGreaterEqual(worker_leases["summary"]["lease_count"], 1)
        self.assertTrue(worker_lease_renew["renewed"])
        self.assertEqual(worker_lease_release["lease"]["lease_status"], "released")
        self.assertEqual(worker_nodes["summary"]["node_count"], 1)
        self.assertEqual(worker_node_heartbeat["node"]["health"], "healthy")
        self.assertEqual(worker_node_drain["node"]["effective_status"], "draining")
        self.assertIn("total", history_stats)
        self.assertIn("tasks", scheduler)
        self.assertEqual(cors_headers.get("access-control-allow-origin"), f"http://127.0.0.1:{server.port}")
