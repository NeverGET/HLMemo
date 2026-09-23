"""Default-deny privacy gate: what may enter a provider prompt (D-016 deviation, W2a; Sol 35 #1).

Evaluated in its own short transaction immediately before EVERY provider call, for every item the
prompt will contain. An item is sent only if ALL hold:

* the job's triggering device is trusted NOW (not revoked, not pending, not expired);
* the item is current and active;
* its ``device_scope`` is not ``device:*`` (hard-pinned: device-scoped content is never sent) and
  is visible to the triggering device (``all`` or its own class);
* for EVERY project in the item's ``project_ids``: ``projects.policy.librarian`` is not ``off``,
  the device holds a current grant (read or better; the admin device counts as reading all), and
  the project is in the job's enqueue-time ``question`` capability set.

Nothing here is configurable. Enqueue-time capabilities are an upper bound, never authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection

DEVICE_NOT_TRUSTED = "device_not_trusted"
POLICY_OFF = "policy_off"
DEVICE_SCOPED = "device_scoped"
NO_READ_GRANT = "no_read_grant"
NOT_IN_CAPABILITIES = "not_in_capabilities"
NOT_CURRENT = "not_current"
SCOPE_NOT_VISIBLE = "scope_not_visible"


@dataclass(slots=True)
class Item:
    version_id: int
    logical_id: int
    project_id: int
    project_ids: list[int]
    device_scope: str
    kind: str
    status: str
    title: str
    body: str
    valid_from: datetime
    current: bool
    pinned: bool


@dataclass(slots=True)
class Verdict:
    device_ok: bool
    denied: dict[int, str] = field(default_factory=dict)  # version_id -> reason

    def allowed(self, version_id: int) -> bool:
        return self.device_ok and version_id not in self.denied


_ITEM_SQL = """
SELECT version_id, logical_id, project_id, project_ids, device_scope, kind, status, title, body,
       valid_from, superseded_at = 'infinity' AND valid_to = 'infinity', pinned
  FROM memory_versions WHERE version_id = ANY(%s)
"""


async def load_items(conn: AsyncConnection, version_ids: list[int]) -> dict[int, Item]:
    if not version_ids:
        return {}
    cur = await conn.execute(_ITEM_SQL, (list(version_ids),))
    out = {}
    for r in await cur.fetchall():
        out[int(r[0])] = Item(int(r[0]), int(r[1]), int(r[2]), [int(x) for x in r[3]], *r[4:])
    return out


async def check(conn: AsyncConnection, capabilities: dict[str, Any], items: list[Item]) -> Verdict:
    """The gate for ``items`` (call it in a fresh transaction right before the provider call)."""
    device_id = int(capabilities.get("trigger_device_id") or 0)
    # expires_at arrives with 0005 (W0a); read it through to_jsonb so both schemas work.
    cur = await conn.execute(
        """
        SELECT d.status, d.class, d.is_admin,
               COALESCE((to_jsonb(d)->>'expires_at')::timestamptz > now(), true)
          FROM devices d WHERE d.device_id = %s
        """,
        (device_id,),
    )
    row = await cur.fetchone()
    if row is None or row[0] != "trusted" or not row[3]:
        return Verdict(False, {it.version_id: DEVICE_NOT_TRUSTED for it in items})
    _status, device_class, is_admin, _ = row
    cur = await conn.execute(
        "SELECT project_id FROM device_project_grants WHERE device_id = %s AND revoked_at IS NULL",
        (device_id,),
    )
    granted = {int(r[0]) for r in await cur.fetchall()}
    wanted = sorted({p for it in items for p in it.project_ids})
    cur = await conn.execute(
        "SELECT project_id, policy->>'librarian' FROM projects WHERE project_id = ANY(%s)", (wanted,)
    )
    policy = {int(pid): pol for pid, pol in await cur.fetchall()}
    capable = {int(p) for p in capabilities.get("question") or []}
    visible = {"all", f"class:{device_class}"}
    verdict = Verdict(True)
    for it in items:
        reason = None
        if not it.current or it.status != "active":
            reason = NOT_CURRENT
        elif it.device_scope.startswith("device:"):
            reason = DEVICE_SCOPED
        elif it.device_scope not in visible:
            reason = SCOPE_NOT_VISIBLE
        else:
            for pid in it.project_ids:
                if policy.get(pid) == "off" or pid not in policy:
                    reason = POLICY_OFF
                    break
                if not is_admin and pid not in granted:
                    reason = NO_READ_GRANT
                    break
                if pid not in capable:
                    reason = NOT_IN_CAPABILITIES
                    break
        if reason:
            verdict.denied[it.version_id] = reason
    return verdict


async def gate(
    conn_factory: Any, capabilities: dict[str, Any], version_ids: list[int]
) -> tuple[Verdict, dict[int, Item]]:
    """Fresh short transaction: reload the items and evaluate the gate."""
    async with await conn_factory() as conn:
        items = await load_items(conn, version_ids)
        missing = [v for v in version_ids if v not in items]
        verdict = await check(conn, capabilities, list(items.values()))
        for v in missing:
            verdict.denied[v] = NOT_CURRENT
        await conn.commit()
    return verdict, items


__all__ = [
    "DEVICE_NOT_TRUSTED",
    "DEVICE_SCOPED",
    "NOT_CURRENT",
    "NOT_IN_CAPABILITIES",
    "NO_READ_GRANT",
    "POLICY_OFF",
    "SCOPE_NOT_VISIBLE",
    "Item",
    "Verdict",
    "check",
    "gate",
    "load_items",
]
