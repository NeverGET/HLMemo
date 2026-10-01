"""D-118: write-time supersession — ``items[].updates`` of ``memory.write`` (consult 74).

An item that corrects a memory the writer read (a memory.query/drilldown hit) carries
``updates: [{item, expected_version?, old_span, mode, replacement?}]``. The writer is the owner's
authenticated device, so this is an OWNER action: no librarian autonomy, no question, no TTL; the
observer librarian still only labels (D-074).

Semantics (the new memory is ALWAYS written; an update never fails the call):

* ``item`` is the clue the writer saw (``v<version_id>[.<ordinal>]``): it fixes the logical item
  AND the expected version. An ``expected_version`` contradicting it is a rejection. An integer
  logical id with ``expected_version`` is the spec-literal form (the hlm CLI).
* **revise**: ``old_span`` (verbatim, unique, on word boundaries, not the whole memory) of the target
  is replaced by ``replacement``, which must occur verbatim in the CARRYING item's body (no other
  batch item counts). Omitted: the whole body, only when it is one statement and fits the D-110
  length ratio. Applied as a ``version_revise`` record (``actor.revise_build``; B-real's record,
  ported minimally with this feature).
* **supersede**: the target is closed (``actor.close_record``; the survivors keep their source and
  code_refs like a write-path survivor) and a ``supersedes`` link from the carrying item is added.
* **historical records** (D-113: ``revise.revisable``; episodes, session notes and decision/ADR rows
  whatever the configured kinds): link-only in BOTH modes, never revised or closed.

Order inside the write transaction (consult 74; J/D-095): ``parse`` (pure) → ``resolve`` BEFORE
any lock (§4.4 (a) visibility; a hidden or unknown target is ``not_found`` and never locked) →
the batch's ONE sorted item lock (with ``resolve``'s ids) → ``validate`` (visibility again, batch
conflicts over EVERY id the batch mutates, capability, the D-083 policy with the project rows
``FOR SHARE``, the version, the kinds, the guards) → the write's clock → ``materialize`` (the
future-dated carrier check, the cut, the records) → event id → ``finalize`` (the carrying item's
ids, the tags ``write_event_id`` + ``update``). The records are applied by ``actor.apply_mutations``
live and on replay (``resolved.updates[].mutations``), reversible by ``reversal.revert_write_update``.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection

from hlmemo.auth.context import AuthContext, Role
from hlmemo.core.temporal import fmt_ts
from hlmemo.db import write_queries as q

CLUE_RE = re.compile(r"^v([0-9]+)(?:\.[0-9]+)?$")

#: reason → (error code, hint). ``linked`` reasons carry no code.
REASONS: dict[str, tuple[str | None, str]] = {
    "bad_clue": ("E_INVALID_ARG", "item must be a clue from memory.query results, e.g. v123"),
    "expected_version_mismatch": (
        "E_INVALID_ARG",
        "expected_version contradicts the clue: pass the clue only",
    ),
    "expected_version_required": ("E_INVALID_ARG", "an integer item needs expected_version: prefer the clue"),
    "not_found": ("E_NOT_FOUND", "no memory with this clue is visible here: run memory.query again"),
    "project_card": ("E_INVALID_ARG", "update the project card with memory.call_the_day card_update"),
    "batch_conflict": ("E_INVALID_ARG", "another item or update of this write changes that memory"),
    "item_closed": ("E_INVALID_ARG", "an item with close or valid_to cannot update other memories"),
    "forbidden_project": ("E_FORBIDDEN_PROJECT", "no write grant on every project of that memory"),
    "policy_excluded": ("E_FORBIDDEN_PROJECT", "a project policy forbids this cross-project update"),
    "version_conflict": ("E_VERSION_CONFLICT", "that memory changed: drill down current_clue and retry"),
    "not_current": ("E_VERSION_CONFLICT", "that memory is no longer current"),
    "not_open": ("E_VERSION_CONFLICT", "that memory has a fixed end date: it cannot be updated this way"),
    "duplicate_link": (
        "E_INVALID_ARG",
        "this item already links supersedes to that memory: drop that link or the update",
    ),
    "span_not_found": ("E_INVALID_ARG", "old_span is not in that memory: copy it verbatim"),
    "span_not_unique": ("E_INVALID_ARG", "old_span occurs more than once in that memory: quote more words"),
    "span_word_boundary": ("E_INVALID_ARG", "old_span must start and end at word boundaries"),
    "span_whole": ("E_INVALID_ARG", "old_span is (almost) the whole memory: use mode supersede"),
    "target_not_nfc": ("E_INVALID_ARG", "that memory cannot be span-revised: use mode supersede"),
    "replacement_required": (
        "E_INVALID_ARG",
        "pass replacement: the new sentence copied verbatim from this item's body",
    ),
    "replacement_not_in_body": ("E_INVALID_ARG", "replacement must occur verbatim in this item's body"),
    "replacement_same": ("E_INVALID_ARG", "replacement equals old_span"),
    "length_ratio": ("E_INVALID_ARG", "replacement is too long: at most 3x old_span and 1000 characters"),
    "replacement_visibility": (
        "E_INVALID_ARG",
        "this item is visible more narrowly than that memory: widen it or use supersede",
    ),
    "future_valid_from": ("E_INVALID_ARG", "this item starts in the future: send the update once it applies"),
    "cut_outside_validity": ("E_INVALID_ARG", "that memory is not valid yet at this item's start"),
    "historical_kind": (None, "a historical record keeps its text: only a supersedes link was added"),
    "decision_record": (None, "a decision record keeps its text: only a supersedes link was added"),
}
_GUARD_REASON = {
    "old_body_nfc": "target_not_nfc",
    "span_word_boundary": "span_word_boundary",
    "span_not_whole": "span_whole",
    "replacement_found": "replacement_not_in_body",
    "replacement_differs": "replacement_same",
    "length_ratio": "length_ratio",
    "replacement_visibility": "replacement_visibility",
}
_PESSIMISTIC_ID = 10**15


def endpoint_visible(ctx: AuthContext, home_id: int, row: Any) -> bool:
    """§4.4 (a) for the write's home project (the rule of ``write_service._endpoint_visible``)."""
    return (
        home_id in row.project_ids and ctx.has(home_id, Role.READ) and row.device_scope in ctx.scope_values()
    )


def parse_item(item: int | str, expected_version: int | None) -> tuple[int | None, int | None, str | None]:
    """``(logical id or None, expected version, rejection reason)`` of an update's ``item``: a clue
    fixes the version (its logical id is resolved later); an integer is a logical id that needs
    ``expected_version``."""
    if isinstance(item, str):
        m = CLUE_RE.match(item)
        if m is None or int(m.group(1)) < 1:
            return None, None, "bad_clue"
        vid = int(m.group(1))
        if expected_version is not None and expected_version != vid:
            return None, None, "expected_version_mismatch"
        return None, vid, None
    if item < 1:
        return None, None, "bad_clue"
    if expected_version is None:
        return None, None, "expected_version_required"
    return int(item), int(expected_version), None


def revise_guards(
    *,
    old_body: str,
    old_span: str,
    replacement: str | None,
    carrier_body: str,
    old_projects: Any,
    old_scope: str,
    new_projects: Any,
    new_scope: str,
) -> tuple[Any, str | None]:
    """The D-110 guards of a write-time revise (``revise.WRITE_GUARDS``), pure: ``(Check, None)``
    when every one holds, else ``(None, reason)``. The replacement is searched ONLY in the carrying
    item's body; an omitted one is the whole body, only when that is one statement and passes the
    length ratio (else ``replacement_required``)."""
    from hlmemo.librarian import revise as rv

    implied = replacement is None
    if implied:
        if rv.statement_count(carrier_body) > 1:
            return None, "replacement_required"
        replacement = carrier_body.strip()
    chk = rv.check(
        old_body=old_body,
        old_span=old_span,
        replacement=replacement,
        new_body=carrier_body,
        old_projects=old_projects,
        old_scope=old_scope,
        new_projects=new_projects,
        new_scope=new_scope,
    )
    failed = chk.failed_of(rv.WRITE_GUARDS)
    if not failed:
        return chk, None
    first = failed[0]
    if first == "old_span_unique":
        n = len(rv.occurrences(old_body, rv.nfc(old_span)))
        return None, "span_not_found" if n == 0 else "span_not_unique"
    if implied and first == "length_ratio":
        return None, "replacement_required"
    return None, _GUARD_REASON[first]


def span_occurs(body: str, old_span: str) -> bool:
    """Supersede / link-only grounding: the quote occurs in the target (NFC, byte-exact)."""
    from hlmemo.librarian import revise as rv

    return bool(rv.occurrences(rv.nfc(body), rv.nfc(old_span)))


@dataclass(slots=True)
class Update:
    index: int  # the carrying item
    k: int  # its position in the item's updates
    spec: Any  # write_models.UpdateSpec
    logical_id: int | None = None
    vid: int | None = None  # the expected version
    status: str = "pending"  # pending | applied | linked | rejected
    reason: str | None = None
    current_version_id: int | None = None
    rows: list[Any] = field(default_factory=list)  # every current segment of the target item
    #: review 76 #3: the segment VALID AT THE WRITE CLOCK (what a query shows now), never simply the
    #: greatest version id (a finite backdated correction can outrank the open survivor)
    target: Any = None
    touched: set[int] = field(default_factory=set)
    action: str | None = None  # revise | close | link
    chk: Any = None
    records: list[dict[str, Any]] = field(default_factory=list)

    @property
    def pending(self) -> bool:
        return self.status == "pending"

    def reject(self, reason: str, *, current: int | None = None) -> None:
        self.status, self.reason, self.current_version_id = "rejected", reason, current


def valid_at(rows: list[Any], at: datetime) -> Any:
    """The segment of ``rows`` (one item's current rows: non-overlapping valid time) valid at
    ``at``, or ``None``."""
    return next((r for r in rows if r.valid_from <= at and (r.valid_to is None or at < r.valid_to)), None)


class WriteUpdates:
    """Every ``items[].updates`` entry of one write batch (see the module doc for the order)."""

    def __init__(self, items: list[Any]) -> None:
        self.updates = [Update(i, k, u) for i, it in enumerate(items) for k, u in enumerate(it.updates or [])]

    def __bool__(self) -> bool:
        return bool(self.updates)

    def _pending(self) -> list[Update]:
        return [u for u in self.updates if u.pending]

    # ------------------------------------------------------------------ before any lock
    async def resolve(self, conn: AsyncConnection, ctx: AuthContext, home_id: int) -> list[int]:
        """Parse every ``item`` and resolve its target with §4.4 (a) BEFORE any lock: a hidden or
        unknown target is ``not_found`` and never locked (no existence disclosure through a
        contended key). Returns the logical ids to add to the batch's single sorted lock."""
        for u in self.updates:
            lid, vid, reason = parse_item(u.spec.item, u.spec.expected_version)
            if reason is not None:
                u.reject(reason)
                continue
            u.vid = vid
            if lid is None:
                v = await q.get_version(conn, int(vid))  # type: ignore[arg-type]
                if v is None or not endpoint_visible(ctx, home_id, v):
                    u.reject("not_found")
                    continue
                u.logical_id = v.logical_id
            else:
                rows = await q.current_versions(conn, lid)
                if not any(endpoint_visible(ctx, home_id, r) for r in rows):
                    u.reject("not_found")
                    continue
                u.logical_id = lid
        return sorted({int(u.logical_id) for u in self._pending() if u.logical_id is not None})

    # ------------------------------------------------------------------ after the item locks
    async def validate(self, conn: AsyncConnection, ctx: AuthContext, home: Any, plans: list[Any]) -> None:
        """Per update, in order (consult 74): visibility on the CURRENT rows; not the project
        card; no batch conflict (the target is changed by an item of the batch or by another
        update: the full set of ids the WHOLE batch mutates, so A↔B chains are refused); the
        carrying item is open; write on every project of the target; the D-083 policy (the project
        rows of every touched set read ``FOR SHARE`` in one sorted call); the head is the expected,
        open, active version; the kinds rule (historical → link-only); the guards. Nothing here
        mutates; ``materialize`` runs after the write's clock."""
        from hlmemo.librarian import revise as rv
        from hlmemo.librarian.candidates import relation_allowed

        home_id = home.project_id
        mutators: Counter[int] = Counter()
        for p in plans:
            if p.logical_id is not None and (p.is_revision or p.is_card):
                mutators[int(p.logical_id)] += 1
        for u in self._pending():
            mutators[int(u.logical_id)] += 1  # type: ignore[arg-type]
        for u in self._pending():
            carrier = plans[u.index]
            u.rows = await q.current_versions(conn, int(u.logical_id))  # type: ignore[arg-type]
            if not u.rows or not all(endpoint_visible(ctx, home_id, r) for r in u.rows):
                u.reject("not_found")
            elif u.logical_id == home.card_logical_id:
                u.reject("project_card")
            elif mutators[int(u.logical_id)] > 1:  # type: ignore[arg-type]
                u.reject("batch_conflict")
            elif carrier.item.close or carrier.item.valid_to is not None:
                u.reject("item_closed")
            else:
                target = {int(pid) for r in u.rows for pid in r.project_ids}
                if not all(ctx.has(pid, Role.WRITE) for pid in sorted(target)):
                    u.reject("forbidden_project")
                u.touched = target | {int(pid) for pid in carrier.project_ids}
        cross = sorted({pid for u in self._pending() if len(u.touched) > 1 for pid in u.touched})
        if cross:
            from hlmemo.db import librarian_queries as lq

            excluded = await lq.cross_project_excluded(conn, cross, lock=True)
            for u in self._pending():
                if len(u.touched) > 1 and not relation_allowed(u.touched, excluded):
                    u.reject("policy_excluded")
        kinds = rv.revise_kinds()
        # review 76 #3: the target is the segment valid NOW (the item locks are held, so no segment
        # of a locked item changes before the write's own clock; ``materialize`` re-checks it there)
        clock = await q.clock_now(conn)
        for u in self._pending():
            carrier = plans[u.index]
            head = valid_at(u.rows, clock)
            if head is None:
                u.reject("not_current")
                continue
            if head.version_id != u.vid:
                u.reject("version_conflict", current=head.version_id)
                continue
            if head.status != "active":
                u.reject("not_current", current=head.version_id)
                continue
            if head.valid_to is not None:  # a fixed end: no open head to revise or close
                u.reject("not_open")
                continue
            u.target = head
            historical = rv.revisable(head.kind, head.body, head.source, kinds)
            if historical is not None or u.spec.mode == "supersede":
                if not span_occurs(head.body, u.spec.old_span):
                    u.reject("span_not_found")
                    continue
                u.action = "link" if historical is not None else "close"
                if historical is not None:
                    u.reason = historical
                continue
            chk, reason = revise_guards(
                old_body=head.body,
                old_span=u.spec.old_span,
                replacement=u.spec.replacement,
                carrier_body=carrier.item.body,
                old_projects=head.project_ids,
                old_scope=head.device_scope,
                new_projects=carrier.project_ids,
                new_scope=carrier.item.device_scope,
            )
            if chk is None:
                u.reject(reason or "span_not_found")
                continue
            u.action, u.chk = "revise", chk

    def has_pending(self) -> bool:
        return bool(self._pending())

    # ------------------------------------------------------------------ after the write's clock
    async def materialize(
        self, conn: AsyncConnection, home: Any, plans: list[Any], now: datetime
    ) -> list[datetime]:
        """The records of every update still pending, at the write's clock ``now`` (taken after
        every lock). A carrying item whose ``valid_from`` lies in the future gets no mutating
        update (consult 74: no gap before it starts, no early activation).

        Review 76 #1: the cut is chosen against the TARGETED open head alone (``actor.revise_cut``
        for both modes): the carrying item's ``valid_from`` when it lies inside that
        head's validity (``head.valid_from < vf ≤ now``), else ``now``. So a mutation affects the
        target row and nothing else — never an earlier segment the quoted head did not represent
        (possibly a historical kind); every affected row is checked before anything is recorded.
        Review 76 #2/#4: an update whose ``supersedes`` edge would collide with one the carrying
        item declares or already holds (any overlap) is rejected (``duplicate_link``), so every
        applied update owns exactly the rows and links its revert undoes. Returns the recorded_at
        of every row and link the records supersede (for the write's T)."""
        from hlmemo.core.temporal import overlaps
        from hlmemo.librarian import actor
        from hlmemo.librarian import revise as rv
        from hlmemo.librarian.errors import AuthorityLost, RevisionRefused

        kinds = rv.revise_kinds()
        recorded: list[datetime] = []
        for u in self._pending():
            carrier = plans[u.index]
            head = u.target
            vf = carrier.interval.start
            assessed = {str(head.logical_id): head.version_id}
            if valid_at(u.rows, now) is not head:  # still the segment valid at the write's clock
                u.reject("not_current", current=head.version_id)
                continue
            if u.action in ("revise", "close") and vf > now:
                u.reject("future_valid_from")
                continue
            cut, rule = actor.revise_cut({"cut": "effective_date", "valid_from": fmt_ts(vf)}, head, now)
            if u.action in ("revise", "close"):
                affected = [r for r in u.rows if overlaps(r.valid_from, r.valid_to, cut, None)]
                if [r.version_id for r in affected] != [head.version_id] or any(
                    rv.revisable(r.kind, r.body, r.source, kinds) is not None for r in affected
                ):
                    u.reject("not_current", current=head.version_id)  # defensive: cannot happen
                    continue
            link_from = fmt_ts(cut) if u.action == "close" else fmt_ts(vf)
            if u.action in ("close", "link") and await self._link_conflict(
                conn, carrier, int(head.logical_id), link_from
            ):
                u.reject("duplicate_link")
                continue
            if u.action == "revise":
                try:
                    rec, rec_at = await actor.revise_build(
                        conn,
                        head,
                        u.chk,
                        cut=cut,
                        rule=rule,
                        from_logical_id=None,  # the carrying item's ids: ``finalize``
                        from_version_id=None,
                        assessed=assessed,
                        by="writer",
                    )
                except RevisionRefused:
                    u.reject("cut_outside_validity")
                    continue
                u.records.append(rec)
                recorded.extend(rec_at)
                u.status = "applied"
                continue
            if u.action == "close":
                # the concrete cut (no v3 rule over "any segment"): only the target row is hit
                m = {
                    "op": "version_close",
                    "capability": "correct",
                    "logical_id": head.logical_id,
                    "valid_to": fmt_ts(cut),
                    "assessed": assessed,
                }
                try:
                    rec, rec_at = await actor.close_record(conn, m)
                except AuthorityLost:
                    u.reject("cut_outside_validity")
                    continue
                if rec["superseded"] != [head.version_id]:  # pragma: no cover - checked above
                    u.reject("not_current", current=head.version_id)
                    continue
                rec["cut_rule"] = rule
                # the survivor keeps the provenance (source, code_refs) like a write-path survivor
                rec["keep_source"] = True
                u.records.append(rec)
                recorded.extend(rec_at)
                u.status = "applied"
            else:
                u.status = "linked"
            u.records.append(
                {
                    "op": "link_insert",
                    "capability": "annotate",
                    "rel": "supersedes",
                    "src_logical_id": None,  # ``finalize``
                    "dst_logical_id": head.logical_id,
                    "dst_version_id": head.version_id,
                    "project_id": home.project_id,
                    "project_ids": [int(p) for p in carrier.project_ids],
                    "device_scope": carrier.item.device_scope,
                    "props": {
                        "by": "writer",
                        "mode": u.spec.mode,
                        "scope": "part" if u.action == "link" and u.spec.mode == "revise" else "whole",
                        "quote": rv.nfc(u.spec.old_span),
                    },
                    "valid_from": link_from,  # a close: where the target stops
                    "valid_to": None,
                    "assessed": {},
                }
            )
        return recorded

    @staticmethod
    async def _link_conflict(conn: AsyncConnection, carrier: Any, target: int, valid_from: str) -> bool:
        """Review 76 #2/#4: the update's ``supersedes`` edge carrier → target would collide with
        one the item declares in its own ``links`` or (a revised carrier) one it already holds
        that overlaps ``[valid_from, ∞)`` at all. Such an update is rejected rather than relying
        on an edge it does not own (a revert could not end it; a partial overlap leaves a gap)."""
        from hlmemo.core.temporal import parse_ts

        if any(ln.rel == "supersedes" and ln.target == target for ln in carrier.item.links):
            return True
        if carrier.logical_id is None:
            return False
        existing = await q.current_links_from(
            conn, int(carrier.logical_id), valid_from=parse_ts(valid_from, field="valid_from"), valid_to=None
        )
        return any(ln.dst_logical_id == target and ln.rel == "supersedes" for ln in existing)

    # ------------------------------------------------------------------ after the event id
    async def finalize(self, conn: AsyncConnection, plans: list[Any], event_id: int) -> None:
        """Fill in what needs the write's own ids (the carrying item's logical/version id, the
        link ids) and tag every record with ``write_event_id`` and ``update`` = ``[index, k]`` (the
        reversal's scope)."""
        links = [r for u in self.updates for r in u.records if r["op"] == "link_insert"]
        ids = await q.allocate_ids(conn, "links", len(links))
        for u in self.updates:
            carrier = plans[u.index]
            for rec in u.records:
                rec["write_event_id"] = event_id
                rec["update"] = [u.index, u.k]
                if rec["op"] == "version_revise":
                    rec["from_logical_id"] = carrier.logical_id
                    rec["from_version_id"] = carrier.version_id
                    rec["link"]["props"]["from_logical_id"] = carrier.logical_id
                    rec["link"]["props"]["from_version_id"] = carrier.version_id
                elif rec["op"] == "link_insert":
                    rec["src_logical_id"] = carrier.logical_id
                    rec["link_id"] = ids.pop(0)

    def records(self) -> list[dict[str, Any]]:
        return [r for u in self.updates for r in u.records]

    def embed_jobs(self) -> list[dict[str, Any]]:
        """The write path's ``{dedupe_key, version_id}`` embed jobs of the rows the updates add
        (a revised version and its survivor, a close's survivors): replayed from ``resolved.jobs``."""
        from hlmemo.core.write_service import embed_dedupe_key

        out: list[dict[str, Any]] = []
        for rec in self.records():
            if rec["op"] == "version_revise":
                rows = [rec["survivor"], rec["version"]]
            elif rec["op"] == "version_close":
                rows = list(rec["survivors"])
            else:
                continue
            out.extend(
                {"dedupe_key": embed_dedupe_key(int(r["version_id"])), "version_id": int(r["version_id"])}
                for r in rows
                if r["chunks"]
            )
        return out

    # ------------------------------------------------------------------ event + ack
    def resolved(self) -> list[dict[str, Any]]:
        out = []
        for u in self.updates:
            entry: dict[str, Any] = {
                "index": u.index,
                "update": u.k,
                "status": u.status,
                "mode": u.spec.mode,
                "mutations": u.records,
            }
            if u.status != "rejected" or u.reason not in ("not_found", "bad_clue"):
                if u.logical_id is not None:
                    entry["logical_id"] = u.logical_id
                    entry["expected_version"] = u.vid
            if u.action is not None and u.status != "rejected":
                entry["action"] = u.action
            if u.reason is not None:
                entry["reason"] = u.reason
            out.append(entry)
        return out

    def ack(self) -> list[dict[str, Any]]:
        out = []
        for u in self.updates:
            entry: dict[str, Any] = {"index": u.index, "update": u.k, "status": u.status, "mode": u.spec.mode}
            revised = [r for r in u.records if r["op"] == "version_revise"]
            if revised:
                entry["clue"] = f"v{revised[0]['version']['version_id']}"
            if u.reason is not None:
                code, hint = REASONS[u.reason]
                if code is not None and u.status == "rejected":
                    entry["code"] = code
                entry["reason"] = u.reason
                entry["hint"] = hint
            if u.current_version_id is not None:
                entry["current_clue"] = f"v{u.current_version_id}"
            out.append(entry)
        return out


def pessimistic_ack_entries(n: int, meter: Any) -> list[dict[str, Any]]:
    """``n`` ack entries no real entry can exceed (every optional field set, the longest reason and
    hint by token count, the largest ids): the write's §3 budget rule sizes the ack before any
    mutation."""
    reason = max(REASONS, key=lambda r: (meter.count_text(r), r))
    hint = max((h for _c, h in REASONS.values()), key=lambda h: (meter.count_text(h), h))
    return [
        {
            "index": 49,
            "update": 7,
            "status": "rejected",
            "mode": "supersede",
            "clue": f"v{_PESSIMISTIC_ID}",
            "code": "E_FORBIDDEN_PROJECT",
            "reason": reason,
            "hint": hint,
            "current_clue": f"v{_PESSIMISTIC_ID}",
        }
        for _ in range(n)
    ]


__all__ = [
    "CLUE_RE",
    "REASONS",
    "Update",
    "WriteUpdates",
    "endpoint_visible",
    "parse_item",
    "pessimistic_ack_entries",
    "revise_guards",
    "span_occurs",
]
