"""Default-deny privacy gate: what may enter a provider prompt (D-016 deviation, W2a; Sol 35 #1).

Evaluated in its own short transaction immediately before EVERY provider call, for every item the
prompt will contain. An item is sent only if ALL hold:

* the job's triggering device is trusted NOW (not revoked, not pending, not expired) and, when the
  caller pins ``token_generation`` (API callers: the risk judge, synthesis), still on that generation;
* the item is current and active;
* its ``device_scope`` is not ``device:*`` (hard-pinned: device-scoped content is never sent) and
  is visible to the triggering device (``all`` or its own class);
* for EVERY project in the item's ``project_ids``: ``projects.policy.librarian`` is not ``off``,
  the device holds a current grant (read or better; the admin device counts as reading all), and
  the project is in the job's enqueue-time ``question`` capability set;
* when the caller names the project the work is ABOUT (``capabilities["isolation_home"]``, the
  asked project of ``memory.ask``; review 79 T1), the D-083 isolation holds NOW: the item's projects
  plus that project touch no ``policy.librarian_cross_project = exclude`` project, or exactly one
  (``candidates.relation_allowed``).

Nothing here is configurable. Enqueue-time capabilities are an upper bound, never authority.

Race semantics (D-062, decided): ``check`` takes the same locks, in the same order, as the
apply-time recheck (``actor.recheck``, spec §2): the shared device-access advisory lock, the device
row ``FOR SHARE`` and the device's grant rows ``FOR SHARE``. A revocation or grant removal that
COMMITS before an attempt's precheck therefore prevents that attempt (the precheck waits for an
in-progress revoke and then sees it). The precheck transaction commits and the request is sent
immediately; no DB transaction is ever held open across an LLM call. An attempt already in flight
(or in the precheck-to-send window) when a revocation commits may complete, but its result is
NEVER applied: the apply transaction re-resolves the device under the same FOR SHARE locks and
turns the job into ``authority_lost``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection

from hlmemo.auth.resolve import lock_device_access
from hlmemo.librarian.candidates import relation_allowed

DEVICE_NOT_TRUSTED = "device_not_trusted"
POLICY_OFF = "policy_off"
DEVICE_SCOPED = "device_scoped"
NO_READ_GRANT = "no_read_grant"
NOT_IN_CAPABILITIES = "not_in_capabilities"
NOT_CURRENT = "not_current"
SCOPE_NOT_VISIBLE = "scope_not_visible"
CROSS_PROJECT_ISOLATED = "cross_project_isolated"


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
#: the same columns without the body (D-136: the Memory Map gate covers every item of a project's
#: map; the verdict never reads the body)
_ITEM_SQL_NO_BODY = _ITEM_SQL.replace("title, body,", "title, ''::text,")


async def load_items(
    conn: AsyncConnection, version_ids: list[int], *, bodies: bool = True
) -> dict[int, Item]:
    if not version_ids:
        return {}
    cur = await conn.execute(_ITEM_SQL if bodies else _ITEM_SQL_NO_BODY, (list(version_ids),))
    out = {}
    for r in await cur.fetchall():
        out[int(r[0])] = Item(int(r[0]), int(r[1]), int(r[2]), [int(x) for x in r[3]], *r[4:])
    return out


async def check(conn: AsyncConnection, capabilities: dict[str, Any], items: list[Item]) -> Verdict:
    """The gate for ``items`` (call it in a fresh transaction right before the provider call)."""
    device_id = int(capabilities.get("trigger_device_id") or 0)
    # D-062 lock order = actor.recheck: advisory device-access lock, device row, grant rows.
    await lock_device_access(conn, device_id)
    # expires_at arrives with 0005 (W0a); read it through to_jsonb so both schemas work.
    cur = await conn.execute(
        """
        SELECT d.status, d.class, d.is_admin,
               COALESCE((to_jsonb(d)->>'expires_at')::timestamptz > now(), true), d.token_generation
          FROM devices d WHERE d.device_id = %s FOR SHARE
        """,
        (device_id,),
    )
    row = await cur.fetchone()
    # an API caller (risk judge, synthesis) also pins the bearer's token generation: a rotated
    # token (generation + 1) loses the authority of the request exactly like a revocation
    generation = capabilities.get("token_generation")
    if (
        row is None
        or row[0] != "trusted"
        or not row[3]
        or (generation is not None and int(row[4]) != int(generation))
    ):
        return Verdict(False, {it.version_id: DEVICE_NOT_TRUSTED for it in items})
    _status, device_class, is_admin, _, _gen = row
    cur = await conn.execute(
        "SELECT project_id FROM device_project_grants WHERE device_id = %s AND revoked_at IS NULL FOR SHARE",
        (device_id,),
    )
    granted = {int(r[0]) for r in await cur.fetchall()}
    home = capabilities.get("isolation_home")
    home = int(home) if home is not None else None
    wanted = sorted({p for it in items for p in it.project_ids} | ({home} if home is not None else set()))
    cur = await conn.execute(
        "SELECT project_id, policy->>'librarian', policy->>'librarian_cross_project' FROM projects"
        " WHERE project_id = ANY(%s)",
        (wanted,),
    )
    rows = await cur.fetchall()
    policy = {int(pid): pol for pid, pol, _cross in rows}
    excluded = {int(pid) for pid, _pol, cross in rows if cross == "exclude"}
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
            isolated = home is not None and not relation_allowed({*it.project_ids, home}, excluded)
            if reason is None and isolated:
                reason = CROSS_PROJECT_ISOLATED
        if reason:
            verdict.denied[it.version_id] = reason
    return verdict


async def gate(
    conn_factory: Any,
    capabilities: dict[str, Any],
    version_ids: list[int],
    *,
    bodies: bool = True,
    ignore_currency: bool = False,
) -> tuple[Verdict, dict[int, Item]]:
    """Fresh short transaction: reload the items and evaluate the gate under the D-062 locks;
    it commits before returning, so the caller sends the request with no transaction open.
    ``bodies=False``: the returned items carry an empty body (the verdict is the same).
    ``ignore_currency`` (D-136, text ALREADY sent): the verdict ignores whether an item is still
    current/active, so a superseded item is judged on the privacy rules alone (NOT_CURRENT must not
    mask a device-scope, policy or grant change)."""
    async with await conn_factory() as conn:
        items = await load_items(conn, version_ids, bodies=bodies)
        if ignore_currency:
            for it in items.values():
                it.current, it.status = True, "active"
        missing = [v for v in version_ids if v not in items]
        verdict = await check(conn, capabilities, list(items.values()))
        for v in missing:
            verdict.denied[v] = NOT_CURRENT
        await conn.commit()
    return verdict, items


__all__ = [
    "CROSS_PROJECT_ISOLATED",
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
