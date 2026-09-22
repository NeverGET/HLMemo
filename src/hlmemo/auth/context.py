"""AuthContext: the server-derived identity of a request (PHASE0-SPEC §2, D-023).

Derived ONLY from the bearer token inside the request transaction; never from client payload.
Shared contract between server/auth (producer), core/write_service and core/retrieval (consumers).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Role(StrEnum):
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"


_ORDER = {Role.READ: 0, Role.WRITE: 1, Role.ADMIN: 2}


@dataclass(frozen=True, slots=True)
class AuthContext:
    device_id: int
    device_class: str  # personal | work | server | ci | other
    is_admin: bool  # device 1 only; bypasses project grants, NOT device_scope
    token_generation: int
    grants: dict[int, Role] = field(default_factory=dict)  # project_id -> role
    client: str = "unknown/0"  # e.g. "claude-code/2.1.278"

    def role_for(self, project_id: int) -> Role | None:
        if self.is_admin:
            return Role.ADMIN
        return self.grants.get(project_id)

    def has(self, project_id: int, required: Role) -> bool:
        r = self.role_for(project_id)
        return r is not None and _ORDER[r] >= _ORDER[required]

    def scope_values(self) -> tuple[str, str, str]:
        """The device_scope values visible to this device: ('all', 'class:<c>', 'device:<id>')."""
        return ("all", f"class:{self.device_class}", f"device:{self.device_id}")
