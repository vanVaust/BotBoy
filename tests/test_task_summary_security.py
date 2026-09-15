from dataclasses import dataclass

from botboy.gateway.app_context import (
    _TaskStoreAuthorizationProxy,
    _current_org,
    _current_principal,
    _current_roles,
)


@dataclass
class _Record:
    task_id: str
    principal: str
    org_id: str
    status: str = "completed"
    owner: str = ""
    result: dict = None
    blocked_kind: str = ""

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "principal": self.principal,
            "org_id": self.org_id,
            "status": self.status,
            "owner": self.owner,
            "result": self.result or {},
            "blocked_kind": self.blocked_kind,
        }


class _Store:
    def __init__(self):
        self.records = [
            _Record("alice-task", "alice", "tenant-a"),
            _Record("bob-task", "bob", "tenant-b"),
        ]

    def list_tasks(self, *, limit=200, offset=0, principal=None, **kwargs):
        records = [record for record in self.records if principal is None or record.principal == principal]
        return records[offset : offset + limit], len(records)

    def list_queue_leases(self, **kwargs):
        return []


def test_authenticated_summary_is_tenant_scoped():
    _current_principal.set("alice")
    _current_org.set("tenant-a")
    _current_roles.set(())
    proxy = _TaskStoreAuthorizationProxy(_Store(), auth_enabled=True)

    summary = proxy.summary()

    assert summary["total"] == 1
    assert summary["latest_task_id"] == "alice-task"
    assert all(item["principal"] == "alice" for item in summary["recent"])


def test_authenticated_worker_summary_is_tenant_scoped():
    _current_principal.set("alice")
    _current_org.set("tenant-a")
    _current_roles.set(())
    proxy = _TaskStoreAuthorizationProxy(_Store(), auth_enabled=True)

    payload = proxy.worker_summary()

    assert payload["summary"]["total"] == 1
    assert all(task["principal"] == "alice" for worker in payload["registry"] for task in worker["tasks"])
