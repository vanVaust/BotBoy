"""Tenant Sandbox — Cross-Tenant Boundary Enforcement for V5."""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

class TenantSandbox:
    """
    Enforces cross-tenant isolation for disk paths, memory spaces, and database contexts.
    """
    
    def __init__(self, base_workspace_dir: str):
        from pathlib import Path
        
        self.base_dir = Path(base_workspace_dir).absolute()
        
    def resolve_org_path(self, org_id: str, relative_path: str) -> str:
        """Resolves a path ensuring it stays within the tenant's bounded workspace."""
        if not org_id or org_id == "*":
            raise PermissionError("Wildcard or empty org_id not allowed in sandboxed paths.")
            
        target = (self.base_dir / "tenants" / org_id / relative_path).resolve()
        
        # Absolute boundary check
        try:
            target.relative_to(self.base_dir / "tenants" / org_id)
        except ValueError:
            logger.critical(f"[SECURITY] Sandbox Escaped attempted by org {org_id} to path {target}")
            raise PermissionError(f"Access denied: path escapes tenant boundary for {org_id}")
            
        return str(target)

    def enforce_task_org(self, task_record: Any, accessing_org_id: str) -> bool:
        """Validates if the current accessor org can view the target task org."""
        record_org = getattr(task_record, "org_id", "default")
        
        if accessing_org_id == "system_root":
            return True
            
        if record_org != accessing_org_id:
            logger.critical(f"[SECURITY] Cross-tenant access denied: {accessing_org_id} attempted access on {record_org} task.")
            raise PermissionError("Cross-tenant task access is strictly forbidden")
            
        return True
