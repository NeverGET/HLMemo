"""Memory Map L2 summaries (D-136; report §D L2, the "summarising" layer): task ``map_summary``.

The librarian process keeps ``memory_map_summaries`` (migration 0009) current: one short LLM summary
(1–3 sentences) per source file or topic cluster of every project, the key being the Memory Map's
own grouping (``core.memory_map.locate``). It is a rebuildable CACHE, never an event projection:
no event and no job row is written (jobs are event projections, replay re-creates them), and
deleting rows only makes this task write them again. ``memory.ask`` works without it.

A cycle (``MapSummarizer.cycle``, every ``HLM_MAP_SUMMARY_EVERY_S``, in the background of the
librarian loop) scans only when something was written since the last scan, when groups were left for
a later cycle, or when a waiting group (debounce, or a failure's back-off; review 79 T4) is due:

1. **Members.** The summarisable items: current, active, non-card, ``device_scope = 'all'`` (never a
   class- or device-scoped item) and every project of the item with ``policy.librarian`` not
   ``off``; grouped per (project, source key), an item joining a project's group only when the D-083
   isolation allows it (``memory_map.isolation_ok``: an item co-owned with a
   ``librarian_cross_project = exclude`` project is summarised nowhere; review 79 T1). ``digest`` =
   sha256 of the sorted member ids.
2. **Stale + debounced.** A group is due when its digest differs from the cached one AND that same
   digest has been observed unchanged for ``HLM_MAP_SUMMARY_DEBOUNCE_S`` (a burst of writes or an
   import is summarised once, after it settles). A failed refresh backs off exponentially
   (5 min doubling, at most 6 h) and keeps the last good summary. At most
   ``HLM_MAP_SUMMARY_PER_CYCLE`` groups per cycle, new sources first, then the largest.
3. **Privacy.** The system gate (the member rule above, re-read in a fresh short transaction) runs
   before EVERY provider attempt (the provider's ``precheck``); a change aborts the group.
4. **Provider.** Task ``map_summary`` (per-task fallback ``HLM_FALLBACK_PROFILE__MAP_SUMMARY``, else
   ``HLM_FALLBACK_PROFILE``), spend guard + ledger, a stable lineage per (group, digest) under the
   job call ceiling. The input is the source name and its items (title + clipped text), sampled in
   a spread order across the whole source up to ``MAX_INPUT_CHARS``; the output is redacted.

Shown to a caller only when every member is in the caller's view (``memory_map.load_summaries``).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection

from hlmemo.core import memory_map as mm
from hlmemo.db.librarian_queries import cross_project_excluded
from hlmemo.librarian.errors import (
    BudgetDeferred,
    JobCallCapExceeded,
    LibrarianError,
    LlmDisabled,
    PrivacyDenied,
    ProviderUnavailable,
    SchemaFail,
)
from hlmemo.librarian.events import NS_LIBRARIAN
from hlmemo.librarian.prompts import load_task
from hlmemo.librarian.provider import Provider
from hlmemo.librarian.redact import Redactor

log = logging.getLogger("hlmemo.librarian.map_summary")

TASK = "map_summary"
MAX_INPUT_CHARS = 16_000
ITEM_CHARS = 1_600
SUMMARY_MAX_CHARS = 400
BACKOFF_BASE_S = 300.0
BACKOFF_MAX_S = 21_600.0
EMPTY_DIGEST = mm.members_digest([])

ConnFactory = Callable[[], Awaitable[AsyncConnection]]

_MEMBERS_SQL = """
SELECT mv.version_id, mv.project_ids, mv.title, mv.kind, mv.source->>'system', mv.source->>'path'
  FROM memory_versions mv
 WHERE mv.status = 'active' AND mv.superseded_at = 'infinity' AND mv.valid_to = 'infinity'
   AND mv.valid_from <= now() AND mv.device_scope = 'all' AND mv.kind <> 'project_card'
 ORDER BY mv.version_id
"""


@dataclass(slots=True)
class Group:
    project_id: int
    key: str
    name: str
    members: list[int]
    digest: str
    cached: bool  # a row exists (a summary or a failure record)


def _allowed(pids: list[int], policies: dict[int, str | None]) -> bool:
    return bool(pids) and all(p in policies and policies[p] != "off" for p in pids)


async def current_groups(conn: AsyncConnection) -> dict[tuple[int, str], Group]:
    """Every summarisable (project, source key) group with its members NOW (module step 1)."""
    cur = await conn.execute(_MEMBERS_SQL, prepare=False)
    rows = await cur.fetchall()
    touched = {int(p) for r in rows for p in r[1]}
    policies = await mm.project_policies(conn, touched)
    excluded = await cross_project_excluded(conn, touched)
    groups: dict[tuple[int, str], Group] = {}
    for vid, pids, title, kind, system, path in rows:
        pids = [int(p) for p in pids]
        if not _allowed(pids, policies):
            continue
        item = mm.ViewItem(int(vid), str(title), str(kind), system, path, 0, pids)
        loc = mm.locate(item)
        for pid in pids:
            if not mm.isolation_ok(pids, pid, excluded):
                continue
            g = groups.get((pid, loc.key))
            if g is None:
                g = groups[(pid, loc.key)] = Group(pid, loc.key, loc.name, [], "", False)
            g.members.append(int(vid))
    for g in groups.values():
        g.members.sort()
        g.digest = mm.members_digest(g.members)
    return groups


async def member_gate(conn: AsyncConnection, members: list[int], project_id: int) -> bool:
    """The system privacy gate over the members of ``project_id``'s group (module step 3), in the
    caller's transaction: the member rule and the D-083 isolation, re-read NOW."""
    cur = await conn.execute(
        "SELECT version_id, project_ids, device_scope, status,"
        " superseded_at = 'infinity' AND valid_to = 'infinity', kind"
        " FROM memory_versions WHERE version_id = ANY(%s)",
        (members,),
        prepare=False,
    )
    rows = await cur.fetchall()
    if len(rows) != len(set(members)):
        return False
    touched = {int(p) for r in rows for p in r[1]} | {project_id}
    policies = await mm.project_policies(conn, touched)
    excluded = await cross_project_excluded(conn, touched)
    return all(
        r[2] == "all"
        and r[3] == "active"
        and r[4]
        and r[5] != "project_card"
        and _allowed(list(r[1]), policies)
        and mm.isolation_ok([int(p) for p in r[1]], project_id, excluded)
        for r in rows
    )


def spread_sample(items: list[tuple[str, str]], budget: int = MAX_INPUT_CHARS) -> list[dict[str, str]]:
    """``(title, text)`` items in a spread order across the whole source, each clipped, until the
    character budget (then restored to document order)."""
    per = max(300, min(ITEM_CHARS, budget // max(1, len(items))))
    picked: list[int] = []
    used = 0
    for i in mm.spread(list(range(len(items)))):
        title, text = items[i]
        cost = len(title) + min(len(text), per) + 20
        if picked and used + cost > budget:
            break
        picked.append(i)
        used += cost
    out = []
    for i in sorted(picked):
        title, text = items[i]
        text = " ".join(text.split())
        out.append({"title": title, "text": text if len(text) <= per else text[:per] + " …"})
    return out


def user_message(
    name: str, key: str, items: list[dict[str, str]], total: int, redactor: Redactor | None = None
) -> str:
    """Review 79 T2: every text value is redacted BEFORE the JSON serialisation (an escaped quote
    hides an assignment from the redactor); the provider still redacts the whole message."""
    payload = {"source": name, "key": key, "items_total": total, "items": items}
    payload = (redactor or Redactor()).value(payload)
    return f"JOB: {TASK}\nINPUT: {json.dumps(payload, ensure_ascii=False)}"


def _validate(obj: dict[str, Any]) -> str | None:
    s = obj.get("summary")
    if not isinstance(s, str) or not s.strip():
        return "summary missing"
    return None


class MapSummarizer:
    def __init__(self, settings: Any, *, provider: Provider, connect: ConnFactory) -> None:
        self.settings = settings
        self.provider = provider
        self.connect = connect
        self.spec = load_task(TASK)
        self._last_marker: int | None = None
        self._pending = False  # due groups were left for a later cycle (or a refresh failed)
        #: the earliest time (``due``'s clock) a waiting group becomes due: a debounce ending or a
        #: failure's back-off expiring triggers a scan on its own, with no new event (review 79 T4)
        self._wake_at: float | None = None
        self._seen: dict[tuple[int, str], tuple[str, float]] = {}  # group -> (digest, first seen)
        self.written = 0
        self.failed = 0

    async def _marker(self, conn: AsyncConnection) -> int:
        cur = await conn.execute("SELECT COALESCE(max(event_id), 0) FROM events")
        return int((await cur.fetchone())[0])

    async def due(self, *, now: float | None = None) -> list[Group]:
        """The groups to refresh this cycle (module step 2); ``[]`` when nothing changed."""
        now = time.monotonic() if now is None else now
        async with await self.connect() as conn:
            marker = await self._marker(conn)
            waking = self._wake_at is not None and now >= self._wake_at
            if marker == self._last_marker and not self._pending and not waking:
                await conn.commit()
                return []
            groups = await current_groups(conn)
            cur = await conn.execute(
                "SELECT project_id, source_key, digest, status, failures,"
                " EXTRACT(EPOCH FROM (now() - updated_at))::float8 FROM memory_map_summaries"
            )
            cache = {(int(r[0]), str(r[1])): r[2:] for r in await cur.fetchall()}
            # sources that no longer have members: their rows are dropped (a cache, rebuildable)
            gone = [k for k in cache if k not in groups]
            for pid, key in gone:
                await conn.execute(
                    "DELETE FROM memory_map_summaries WHERE project_id = %s AND source_key = %s", (pid, key)
                )
            await conn.commit()
        self._last_marker = marker
        debounce = float(self.settings.map_summary_debounce_s)
        due: list[Group] = []
        wakes: list[float] = []
        for k, g in groups.items():
            row = cache.get(k)
            g.cached = row is not None
            if row is not None and row[0] == g.digest:
                self._seen.pop(k, None)
                continue
            if row is not None and row[1] == "failed":
                backoff = min(BACKOFF_BASE_S * 2 ** max(0, int(row[2]) - 1), BACKOFF_MAX_S)
                if float(row[3]) < backoff:
                    wakes.append(now + backoff - float(row[3]))
                    continue
            seen = self._seen.get(k)
            if seen is None or seen[0] != g.digest:
                self._seen[k] = (g.digest, now)
                if debounce > 0:
                    wakes.append(now + debounce)
                    continue
            elif now - seen[1] < debounce:
                wakes.append(seen[1] + debounce)
                continue
            due.append(g)
        for k in [k for k in self._seen if k not in groups]:
            del self._seen[k]
        due.sort(key=lambda g: (g.cached, -len(g.members), g.project_id, g.key))
        limit = int(self.settings.map_summary_per_cycle)
        self._pending = len(due) > limit
        self._wake_at = min(wakes) if wakes else None
        return due[:limit]

    async def cycle(self, *, now: float | None = None) -> int:
        """One cycle; returns the number of summaries written. Stops early on a budget refusal or
        a provider outage (the next cycle tries again)."""
        written = 0
        for g in await self.due(now=now):
            try:
                if await self.refresh(g):
                    written += 1
            except (BudgetDeferred, ProviderUnavailable, LlmDisabled) as exc:
                log.info("map_summary: cycle stopped (%s)", type(exc).__name__)
                self._pending = True
                break
        return written

    async def refresh(self, g: Group) -> bool:
        """Summarise one group and upsert its row; False when it was skipped or failed."""
        async with await self.connect() as conn:
            if not await member_gate(conn, g.members, g.project_id):
                await conn.commit()
                self._pending = True
                return False
            cur = await conn.execute(
                "SELECT version_id, title, body FROM memory_versions WHERE version_id = ANY(%s)",
                (g.members,),
                prepare=False,
            )
            rows = {int(r[0]): (str(r[1]), str(r[2])) for r in await cur.fetchall()}
            await conn.commit()
        items = spread_sample([rows[v] for v in g.members if v in rows])
        user = user_message(g.name, g.key, items, len(g.members), self.provider.redactor)

        async def precheck() -> None:  # before EVERY attempt (retries and the fallback included)
            async with await self.connect() as c:
                ok = await member_gate(c, g.members, g.project_id)
                await c.commit()
            if not ok:
                raise PrivacyDenied("E_PRIVACY_DENIED")

        lineage = str(uuid.uuid5(NS_LIBRARIAN, f"map_summary:{g.project_id}:{g.key}:{g.digest}"))
        try:
            res = await self.provider.complete(
                self.spec, user, validate=_validate, precheck=precheck, lineage=lineage
            )
        except PrivacyDenied:
            self._pending = True
            return False
        except (SchemaFail, JobCallCapExceeded) as exc:
            await self._failed(g, type(exc).__name__)
            return False
        except (BudgetDeferred, ProviderUnavailable, LlmDisabled):
            raise
        except LibrarianError as exc:
            await self._failed(g, type(exc).__name__)
            return False
        text = " ".join(self.provider.redactor.text(str(res.output.get("summary") or "")).split())
        if len(text) > SUMMARY_MAX_CHARS:
            text = text[: SUMMARY_MAX_CHARS - 1] + "…"
        async with await self.connect() as conn:
            await conn.execute(
                """
                INSERT INTO memory_map_summaries (project_id, source_key, digest, member_ids, summary,
                                                  status, failures, profile, prompt_version, updated_at)
                VALUES (%s, %s, %s, %s, %s, 'ok', 0, %s, %s, now())
                ON CONFLICT (project_id, source_key) DO UPDATE SET
                  digest = EXCLUDED.digest, member_ids = EXCLUDED.member_ids, summary = EXCLUDED.summary,
                  status = 'ok', failures = 0, profile = EXCLUDED.profile,
                  prompt_version = EXCLUDED.prompt_version, updated_at = now()
                """,
                (g.project_id, g.key, g.digest, g.members, text, res.profile, res.prompt_version),
            )
            await conn.commit()
        self._seen.pop((g.project_id, g.key), None)
        self.written += 1
        return True

    async def _failed(self, g: Group, why: str) -> None:
        """Keep the last good summary; record the failure (exponential back-off). The next cycle
        re-reads the cache (``_pending``), which arms the back-off's wake time (review 79 T4)."""
        log.warning("map_summary: %s for project %s source %s", why, g.project_id, g.key)
        self.failed += 1
        self._pending = True
        async with await self.connect() as conn:
            await conn.execute(
                """
                INSERT INTO memory_map_summaries (project_id, source_key, digest, member_ids, summary,
                                                  status, failures, updated_at)
                VALUES (%s, %s, %s, '{}', NULL, 'failed', 1, now())
                ON CONFLICT (project_id, source_key) DO UPDATE SET
                  status = 'failed', updated_at = now(),
                  failures = LEAST(memory_map_summaries.failures + 1, 30)
                """,
                (g.project_id, g.key, EMPTY_DIGEST),
            )
            await conn.commit()


async def run_cycle_safely(summarizer: MapSummarizer) -> int:
    """A background cycle that never takes the librarian loop down."""
    try:
        return await summarizer.cycle()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        log.error("map_summary cycle failed: %s: %s", type(exc).__name__, exc)
        return 0


__all__ = [
    "TASK",
    "Group",
    "MapSummarizer",
    "current_groups",
    "member_gate",
    "run_cycle_safely",
    "spread_sample",
    "user_message",
]
