"""WebSocket uses the same live credentials and execution boundary as HTTP."""
import unittest

from starlette.websockets import WebSocketDisconnect
from tests import test_gateway_identity_integration as fixture


class WebSocketTenantIntegrationTest(unittest.TestCase):
    setUp = fixture.GatewayIdentityIntegrationTest.setUp
    login = fixture.GatewayIdentityIntegrationTest.login
    headers = staticmethod(fixture.GatewayIdentityIntegrationTest.headers)

    def test_socket_command_binds_login_tenant_and_rejects_unscoped_commands(self):
        login = self.login()
        with self.client.websocket_connect("/ws/chat", headers=self.headers(login)) as socket:
            socket.send_json({"command": "calculate 7 * 8"})
            result = socket.receive_json()
            self.assertTrue(result.get("success"), result)
            tasks, total = self.bot.task_store.list_tasks(principal="alice")
            self.assertGreater(total, 0)
            self.assertTrue(all(task.org_id == "tenant-a" for task in tasks))
            socket.send_json({"command": "history"})
            result = socket.receive_json()
            self.assertFalse(result.get("success"), result)

    def test_password_reset_closes_existing_socket_on_next_message(self):
        login = self.login()
        with self.client.websocket_connect("/ws/chat", headers=self.headers(login)) as socket:
            socket.send_json({"command": "calculate 1 + 1"})
            self.assertTrue(socket.receive_json().get("success"))
            self.store.upsert_principal("alice", "changed-password", "admin", "tenant-a")
            socket.send_json({"command": "calculate 2 + 2"})
            with self.assertRaises(WebSocketDisconnect) as raised:
                socket.receive_json()
            self.assertEqual(raised.exception.code, 1008)

    def test_revoked_api_key_closes_existing_socket_on_next_message(self):
        key_store = self.app.state.bot_api_key_store()
        key = key_store.generate(role="admin", org_id="tenant-a")
        login = self.login(api_key=key.full_key)
        with self.client.websocket_connect("/ws/chat", headers=self.headers(login)) as socket:
            socket.send_json({"command": "calculate 1 + 1"})
            self.assertTrue(socket.receive_json().get("success"))
            key_store.revoke(key.key_id)
            socket.send_json({"command": "calculate 2 + 2"})
            with self.assertRaises(WebSocketDisconnect) as raised:
                socket.receive_json()
            self.assertEqual(raised.exception.code, 1008)
