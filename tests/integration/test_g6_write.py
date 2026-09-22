"""G6 durability — write service (PHASE0-SPEC §1.1, §3, §7): events, idempotency, conflicts,
bi-temporal corrections, replay from events, call_the_day.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from hlmemo.core import MODEL_ID
from hlmemo.core.errors import ToolError
from hlmemo.core.temporal import ONE_US, parse_ts
from hlmemo.core.write_service import call_the_day, default_deps, write
from hlmemo.db import write_queries as q
from hlmemo.db.replay import rebuild_projections
from tests.integration._write_fixtures import (
    MAIN,
    OTHER,
    World,
    count,
    dump_projections,
    event_payload,
    head_at,
    item,
    seed_world,
    write_req,
)

pytestmark = pytest.mark.integration

D0 = datetime(2026, 9, 1, tzinfo=UTC)
DAY = timedelta(days=1)
LONG_BODY = " ".join(f"Satz {i}: der Dienst svc-qx7 liest APP_DB_DSN und meldet E4193." for i in range(120))


@pytest.fixture(scope="session")
def deps():
    return default_deps()


@pytest.fixture
async def world(connect) -> World:
    async with await connect() as conn:
        return await seed_world(conn)


def _err(exc_info) -> ToolError:
    return exc_info.value


# --------------------------------------------------------------------------- create
async def test_initial_create_persists_event_versions_chunks_links_jobs(connect, world, deps) -> None:
    req = write_req(
        MAIN,
        [
            item("Erster Fakt", LONG_BODY, tags=["a"], importance=7),
            item(
                "Zweiter Fakt",
                "kurzer Text über svc-qx7",
                links=[{"rel": "relates_to", "target": "$0"}, {"rel": "derived_from", "target": "$0"}],
            ),
        ],
        token_budget=1000,
    )
    async with await connect() as conn:
        res = await write(conn, world.ctx_a, req, deps=deps)
        await conn.commit()
        assert res.replayed is False and res.request_id == req["request_id"]
        assert [v.index for v in res.versions] == [0, 1]
        assert res.budget.limit == 1000 and 0 < res.budget.used <= 1000
        v0, v1 = res.versions
        assert v0.chunk_count > 1 and v1.chunk_count == 1
        assert v1.version_id > v0.version_id and v1.logical_id > v0.logical_id

        cur = await conn.execute(
            "SELECT kind, payload, payload_sha256, result, projection_version FROM events"
            " WHERE request_id = %s",
            (req["request_id"],),
        )
        kind, payload, sha, result, pv = await cur.fetchone()
        assert kind == "write" and pv == 1
        assert set(payload) == {"request", "resolved"}
        # The persisted request is verbatim; resolved.write stores validated values for replay.
        canon = json.dumps(req, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        assert sha == hashlib.sha256(canon.encode()).hexdigest()
        assert payload["request"] == req
        assert payload["resolved"]["hash_version"] == 2
        assert payload["resolved"]["write"]["items"][0]["device_scope"] == "all"
        r = payload["resolved"]
        assert {
            "recorded_at",
            "occurred_at",
            "projection_version",
            "normalizer_version",
            "chunker",
            "meter",
            "embedder",
            "items",
            "superseded_links",
            "jobs",
        } <= set(r)
        assert r["chunker"]["tokenizer_sha256"] and r["embedder"]["model"] == MODEL_ID
        assert result["versions"][0]["version_id"] == v0.version_id and result["replayed"] is False

        T = parse_ts(r["recorded_at"])
        cur = await conn.execute(
            "SELECT version_id, logical_id, project_ids, recorded_at, superseded_at = 'infinity',"
            " valid_to = 'infinity', token_count, supersedes_version_id"
            " FROM memory_versions ORDER BY version_id"
        )
        rows = await cur.fetchall()
        assert [x[0] for x in rows] == [v0.version_id, v1.version_id]
        for _vid, _lid, pids, rec, current, open_end, tok, sup in rows:
            assert pids == [world.main_id] and rec == T and current and open_end and tok > 0 and sup is None

        cur = await conn.execute(
            "SELECT ordinal, char_start, char_end, text, e5_tokens FROM chunks"
            " WHERE version_id = %s ORDER BY ordinal",
            (v0.version_id,),
        )
        chunks = await cur.fetchall()
        assert len(chunks) == v0.chunk_count
        for i, (ordinal, cs, ce, text, e5) in enumerate(chunks):
            assert ordinal == i and LONG_BODY[cs:ce] == text and 0 < e5 <= 400
        assert [c["chunk_id"] for c in r["items"][0]["chunks"]] == sorted(
            c["chunk_id"] for c in r["items"][0]["chunks"]
        )

        cur = await conn.execute(
            "SELECT rel, src_logical_id, dst_logical_id, dst_version_id, recorded_at FROM links"
            " ORDER BY link_id"
        )
        links = await cur.fetchall()
        assert links == [
            ("relates_to", v1.logical_id, v0.logical_id, None, T),
            ("derived_from", v1.logical_id, v0.logical_id, v0.version_id, T),
        ]

        cur = await conn.execute("SELECT kind, dedupe_key, status, payload FROM jobs ORDER BY dedupe_key")
        jobs = await cur.fetchall()
        assert sorted(j[1] for j in jobs) == sorted(
            f"embed:{v.version_id}:{MODEL_ID}@614241f6" for v in res.versions
        )
        assert all(j[0] == "embed" and j[2] == "queued" and j[3]["version_id"] for j in jobs)
        assert [j["dedupe_key"] for j in r["jobs"]] == sorted(j[1] for j in jobs)


# --------------------------------------------------------------------------- idempotency
async def test_duplicate_request_id_single_effect(connect, world, deps) -> None:
    req = write_req(MAIN, [item("Idem", "einmal schreiben")])
    async with await connect() as conn:
        first = await write(conn, world.ctx_a, req, deps=deps)
        await conn.commit()
        second = await write(conn, world.ctx_a, req, deps=deps)
        await conn.commit()
        assert second.replayed is True and first.replayed is False
        assert second.versions == first.versions and second.request_id == first.request_id
        assert await count(conn, "events") == 1
        assert await count(conn, "memory_versions") == 1
        assert await count(conn, "jobs") == 1
        # same UUID from another device on a project it may write is an independent request (§3)
        other = dict(req, project=OTHER)
        third = await write(conn, world.ctx_b, other, deps=deps)
        await conn.commit()
        assert third.replayed is False and await count(conn, "events") == 2


async def test_request_id_conflict_on_different_payload(connect, world, deps) -> None:
    req = write_req(MAIN, [item("Idem", "einmal schreiben")])
    async with await connect() as conn:
        await write(conn, world.ctx_a, req, deps=deps)
        await conn.commit()
        changed = dict(req, items=[item("Idem", "anderer Text")])
        with pytest.raises(ToolError) as ei:
            await write(conn, world.ctx_a, changed, deps=deps)
        await conn.rollback()
        assert _err(ei).code == "E_REQUEST_ID_CONFLICT"
        assert await count(conn, "events") == 1 and await count(conn, "memory_versions") == 1


@pytest.mark.parametrize("kind", ["write", "call_the_day"])
@pytest.mark.parametrize(
    "event_style", ["legacy", "legacy_versioned", "raw_unversioned", "raw_unversioned_coerced"]
)
async def test_retry_uses_stored_hash_version(connect, world, deps, monkeypatch, kind, event_style):
    """An upgrade must preserve both legacy normalized retries and round-8 raw distinctions."""
    req = {"project": MAIN, "request_id": str(uuid.uuid4()), "client": "pytest/0"}
    if kind == "write":
        req["items"] = [{"kind": "fact", "title": "t", "body": "b"}]
        legacy = dict(
            req,
            items=[
                dict(
                    req["items"][0], tags=[], pinned=False, stability="volatile", device_scope="all", links=[]
                )
            ],
        )
        explicit = dict(req, items=[dict(req["items"][0], device_scope="all")])
        changed = dict(req, items=[dict(req["items"][0], body="different")])
        operation = write
    else:
        req.update(session_id=str(uuid.uuid4()), notes="n")
        legacy = dict(req, decisions=[], lessons=[], expected_versions=[])
        explicit = dict(req, decisions=[])
        changed = dict(req, notes="different")
        operation = call_the_day
    if event_style == "raw_unversioned_coerced":
        # Python considers 1000.0 == 1000; canonical JSON must still distinguish these requests.
        req = dict(legacy, token_budget=1000.0)
        explicit = dict(req, token_budget=1000)

    # Emulate the original writer at insertion: no mutation of authoritative historical events.
    insert_event = q.insert_event

    async def insert_historical(conn, **kwargs):
        payload = json.loads(json.dumps(kwargs["payload"]))
        payload["resolved"].pop("hash_version")
        if event_style.startswith("legacy"):
            payload["request"] = legacy
            if kind == "write":
                payload["resolved"].pop("write")
            if event_style == "legacy_versioned":
                payload["resolved"]["hash_version"] = 1
        canon = json.dumps(payload["request"], ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        kwargs.update(payload=payload, payload_sha256=hashlib.sha256(canon.encode()).hexdigest())
        return await insert_event(conn, **kwargs)

    async with await connect() as conn:
        with monkeypatch.context() as patch:
            patch.setattr(q, "insert_event", insert_historical)
            first = await operation(conn, world.ctx_a, req, deps=deps)
            await conn.commit()
        payload_before = await event_payload(conn, req["request_id"])
        await conn.commit()
        # Dict order is not meaningful in canonical JSON.
        replayed = await operation(conn, world.ctx_a, dict(reversed(list(req.items()))), deps=deps)
        await conn.commit()
        assert replayed.replayed and replayed.versions == first.versions
        with pytest.raises(ToolError, match="different payload") as exc:
            await operation(conn, world.ctx_a, changed, deps=deps)
        await conn.rollback()
        assert exc.value.code == "E_REQUEST_ID_CONFLICT"
        if event_style.startswith("legacy"):
            again = await operation(conn, world.ctx_a, explicit, deps=deps)
            await conn.commit()
            assert again.replayed and again.versions == first.versions
        else:
            # The compatibility path must not normalize round-8 requests that omitted defaults.
            for altered in (explicit, dict(req, occurred_at=None)):
                with pytest.raises(ToolError) as exc:
                    await operation(conn, world.ctx_a, altered, deps=deps)
                await conn.rollback()
                assert exc.value.code == "E_REQUEST_ID_CONFLICT"
        assert await event_payload(conn, req["request_id"]) == payload_before
        assert await count(conn, "events") == 1
        assert await count(conn, "memory_versions") == len(first.versions)


@pytest.mark.parametrize("kind", ["write", "call_the_day"])
async def test_unknown_idempotency_hash_version_fails_closed(connect, world, deps, monkeypatch, kind):
    req = {"project": MAIN, "request_id": str(uuid.uuid4()), "client": "pytest/0"}
    if kind == "write":
        req["items"] = [{"kind": "fact", "title": "t", "body": "b"}]
        operation = write
    else:
        req.update(session_id=str(uuid.uuid4()), notes="n")
        operation = call_the_day
    insert_event = q.insert_event

    async def insert_future(conn, **kwargs):
        kwargs["payload"]["resolved"]["hash_version"] = 99
        return await insert_event(conn, **kwargs)

    async with await connect() as conn:
        with monkeypatch.context() as patch:
            patch.setattr(q, "insert_event", insert_future)
            await operation(conn, world.ctx_a, req, deps=deps)
            await conn.commit()
        with pytest.raises(ToolError) as exc:
            await operation(conn, world.ctx_a, req, deps=deps)
        await conn.rollback()
        assert exc.value.code == "E_REQUEST_ID_CONFLICT"
        assert await count(conn, "events") == 1


# --------------------------------------------------------------------------- concurrency
async def test_concurrent_revision_conflict(connect, world, deps) -> None:
    async with await connect() as conn:
        res = await write(conn, world.ctx_a, write_req(MAIN, [item("Race", "Stand A")]), deps=deps)
        await conn.commit()
    lid, vid = res.versions[0].logical_id, res.versions[0].version_id

    async def revise(label: str):
        async with await connect() as c:
            try:
                out = await write(
                    c,
                    world.ctx_a,
                    write_req(
                        MAIN, [item("Race", f"Stand {label}", logical_id=lid, expected_version_id=vid)]
                    ),
                    deps=deps,
                )
                await c.commit()
                return out
            except ToolError as exc:
                await c.rollback()
                return exc

    outcomes = await asyncio.gather(revise("B"), revise("C"))
    errors = [o for o in outcomes if isinstance(o, ToolError)]
    wins = [o for o in outcomes if not isinstance(o, ToolError)]
    assert len(errors) == 1 and len(wins) == 1, outcomes
    assert errors[0].code == "E_VERSION_CONFLICT"
    assert errors[0].details["current_version_id"] == wins[0].versions[0].version_id
    async with await connect() as conn:
        assert await count(conn, "events") == 2
        cur = await conn.execute(
            "SELECT count(*) FROM memory_versions WHERE logical_id = %s AND superseded_at = 'infinity'",
            (lid,),
        )
        # head + the pre-occurred_at survivor of the original row
        assert (await cur.fetchone())[0] == 2


# --------------------------------------------------------------------------- bi-temporal
async def _create_and_correct(conn, world, deps):
    """Item valid from D0 (body 'v1'); correction of [D5, D10) to 'corr'. Returns ids and T values."""
    create = write_req(MAIN, [item("Zeit", "v1 body", valid_from=D0.isoformat())])
    r1 = await write(conn, world.ctx_a, create, deps=deps)
    await conn.commit()
    lid, v1 = r1.versions[0].logical_id, r1.versions[0].version_id
    fix = write_req(
        MAIN,
        [
            item(
                "Zeit",
                "corr body",
                logical_id=lid,
                expected_version_id=v1,
                valid_from=(D0 + 5 * DAY).isoformat(),
                valid_to=(D0 + 10 * DAY).isoformat(),
            )
        ],
    )
    r2 = await write(conn, world.ctx_a, fix, deps=deps)
    await conn.commit()
    p2 = await event_payload(conn, fix["request_id"])
    return lid, v1, r2.versions[0].version_id, parse_ts(p2["resolved"]["recorded_at"]), p2["resolved"]


async def test_backdated_correction_valid_at_known_at(connect, world, deps) -> None:
    async with await connect() as conn:
        lid, v1, v_corr, T, resolved = await _create_and_correct(conn, world, deps)
        now = datetime.now(UTC) + timedelta(seconds=5)
        before_T = T - ONE_US

        # known_at after the correction: three valid-time points, three different rows
        a = await head_at(conn, lid, D0 + 3 * DAY, now)
        b = await head_at(conn, lid, D0 + 7 * DAY, now)
        c = await head_at(conn, lid, D0 + 12 * DAY, now)
        assert a is not None and b is not None and c is not None
        assert a[1] == "v1 body" and b == (v_corr, "corr body") and c[1] == "v1 body"
        assert a[0] != v1 and c[0] != v1 and a[0] != c[0]  # surviving segments are new rows
        assert v_corr > a[0] and v_corr > c[0]  # replacement is head (greatest current version_id)

        # known_at before T: the original row answers everywhere
        for at in (D0 + 3 * DAY, D0 + 7 * DAY, D0 + 12 * DAY):
            assert await head_at(conn, lid, at, before_T) == (v1, "v1 body")
        assert await head_at(conn, lid, D0 - DAY, now) is None

        it = resolved["items"][0]
        assert it["supersedes"] == [v1] and it["supersedes_version_id"] == v1
        assert {s["from_version_id"] for s in it["survivors"]} == {v1}
        cur = await conn.execute("SELECT superseded_at FROM memory_versions WHERE version_id = %s", (v1,))
        assert (await cur.fetchone())[0] == T


async def test_surviving_segments_after_correction(connect, world, deps) -> None:
    async with await connect() as conn:
        create = write_req(MAIN, [item("Zeit", LONG_BODY, valid_from=D0.isoformat())])
        r1 = await write(conn, world.ctx_a, create, deps=deps)
        await conn.commit()
        lid, v1 = r1.versions[0].logical_id, r1.versions[0].version_id
        cur = await conn.execute(
            "SELECT chunk_id, ordinal, char_start, char_end, text, text_norm, e5_tokens FROM chunks"
            " WHERE version_id = %s ORDER BY ordinal",
            (v1,),
        )
        old_chunks = await cur.fetchall()
        assert len(old_chunks) > 1

        fix = write_req(
            MAIN,
            [
                item(
                    "Zeit",
                    "corr",
                    logical_id=lid,
                    expected_version_id=v1,
                    valid_from=(D0 + 5 * DAY).isoformat(),
                    valid_to=(D0 + 10 * DAY).isoformat(),
                )
            ],
        )
        r2 = await write(conn, world.ctx_a, fix, deps=deps)
        await conn.commit()
        v_corr = r2.versions[0].version_id

        cur = await conn.execute(
            "SELECT version_id, valid_from, valid_to = 'infinity', nullif(valid_to, 'infinity'), body,"
            " supersedes_version_id"
            " FROM memory_versions WHERE logical_id = %s AND superseded_at = 'infinity' AND version_id <> %s"
            " ORDER BY valid_from",
            (lid, v_corr),
        )
        survivors = await cur.fetchall()
        assert len(survivors) == 2
        s1, s2 = survivors
        assert (s1[1], s1[3], s1[4], s1[5]) == (D0, D0 + 5 * DAY, LONG_BODY, v1)
        assert (s2[1], s2[2], s2[4], s2[5]) == (D0 + 10 * DAY, True, LONG_BODY, v1)

        # chunks copied per survivor: same text/offsets, fresh chunk ids; originals untouched
        for svid, *_ in survivors:
            cur = await conn.execute(
                "SELECT chunk_id, ordinal, char_start, char_end, text, text_norm, e5_tokens FROM chunks"
                " WHERE version_id = %s ORDER BY ordinal",
                (svid,),
            )
            copied = await cur.fetchall()
            assert [c[1:] for c in copied] == [c[1:] for c in old_chunks]
            assert not ({c[0] for c in copied} & {c[0] for c in old_chunks})
        cur = await conn.execute(
            "SELECT chunk_id, ordinal, char_start, char_end, text, text_norm, e5_tokens FROM chunks"
            " WHERE version_id = %s ORDER BY ordinal",
            (v1,),
        )
        assert await cur.fetchall() == old_chunks
        # one embed job per new version (replacement + 2 survivors) + the original
        assert await count(conn, "jobs") == 4
        p2 = await event_payload(conn, fix["request_id"])
        assert len(p2["resolved"]["items"][0]["survivors"]) == 2
        assert len(p2["resolved"]["jobs"]) == 3


# --------------------------------------------------------------------------- content / authz
async def test_card_too_large(connect, world, deps) -> None:
    big = " ".join(f"Zeile {i} über das Projekt und seine Regeln" for i in range(200))
    async with await connect() as conn:
        with pytest.raises(ToolError) as ei:
            await write(
                conn, world.ctx_a, write_req(MAIN, [dict(item("Card", big), kind="project_card")]), deps=deps
            )
        await conn.rollback()
        assert _err(ei).code == "E_CARD_TOO_LARGE" and _err(ei).details["max"] == 512
        assert await count(conn, "events") == 0 and await count(conn, "memory_versions") == 0
        # 512 tokens or fewer is fine
        ok = await write(
            conn,
            world.ctx_a,
            write_req(MAIN, [dict(item("Card", "kurze Karte"), kind="project_card")]),
            deps=deps,
        )
        await conn.commit()
        assert ok.versions[0].logical_id == world.main_card_lid


async def test_unauthorized_write_no_disclosure(connect, world, deps) -> None:
    async with await connect() as conn:
        res = await write(conn, world.ctx_a, write_req(MAIN, [item("Geheim", "Inhalt")]), deps=deps)
        await conn.commit()
        lid, vid = res.versions[0].logical_id, res.versions[0].version_id

        # dev-b has no grant on MAIN: wrong expected_version_id must not leak the head
        with pytest.raises(ToolError) as ei:
            await write(
                conn,
                world.ctx_b,
                write_req(MAIN, [item("Geheim", "x", logical_id=lid, expected_version_id=vid + 100)]),
                deps=deps,
            )
        await conn.rollback()
        assert _err(ei).code == "E_FORBIDDEN_PROJECT"
        assert "current_version_id" not in _err(ei).details and str(vid) not in _err(ei).message
        # ... nor whether the logical id exists at all (same error for a nonexistent one)
        with pytest.raises(ToolError) as ei2:
            await write(
                conn,
                world.ctx_b,
                write_req(MAIN, [item("Geheim", "x", logical_id=lid + 999, expected_version_id=1)]),
                deps=deps,
            )
        await conn.rollback()
        assert _err(ei2).code == "E_FORBIDDEN_PROJECT"

        # dev-b may write OTHER, but the item's home is MAIN → E_NOT_FOUND (no cross-project revision)
        with pytest.raises(ToolError) as ei3:
            await write(
                conn,
                world.ctx_b,
                write_req(OTHER, [item("Geheim", "x", logical_id=lid, expected_version_id=vid + 100)]),
                deps=deps,
            )
        await conn.rollback()
        assert _err(ei3).code == "E_NOT_FOUND" and "current_version_id" not in _err(ei3).details

        # dev-b listing MAIN in project_ids of an OTHER write lacks the grant → E_FORBIDDEN_PROJECT
        with pytest.raises(ToolError) as ei4:
            await write(
                conn,
                world.ctx_b,
                write_req(OTHER, [item("Cross", "x", project_ids=[OTHER, MAIN])]),
                deps=deps,
            )
        await conn.rollback()
        assert _err(ei4).code == "E_FORBIDDEN_PROJECT"

        # authorized caller with a stale expected version gets the head
        with pytest.raises(ToolError) as ei5:
            await write(
                conn,
                world.ctx_a,
                write_req(MAIN, [item("Geheim", "x", logical_id=lid, expected_version_id=vid + 100)]),
                deps=deps,
            )
        await conn.rollback()
        assert _err(ei5).code == "E_VERSION_CONFLICT" and _err(ei5).details["current_version_id"] == vid
        assert await count(conn, "events") == 1


# --------------------------------------------------------------------------- replay
async def test_rebuild_projections_from_events_identical(connect, world, deps) -> None:
    async with await connect() as conn:
        r1 = await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item("A", LONG_BODY, tags=["x", "y"], importance=3, valid_from=D0.isoformat()),
                    item(
                        "B",
                        "kurz",
                        project_ids=[MAIN, OTHER],
                        device_scope="class:personal",  # visible to ctx_a: a later item links to it
                        links=[
                            {"rel": "derived_from", "target": "$0"},
                            {"rel": "depends_on", "target": "$0"},
                        ],
                    ),
                ],
            ),
            deps=deps,
        )
        await conn.commit()
        a, b = r1.versions
        r2 = await write(  # plain revision of A (interval [occurred_at, inf) → one survivor)
            conn,
            world.ctx_a,
            write_req(
                MAIN, [item("A2", "A geändert", logical_id=a.logical_id, expected_version_id=a.version_id)]
            ),
            deps=deps,
        )
        await conn.commit()
        await write(  # backdated correction of A, re-declaring a link (supersede + insert)
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item(
                        "A3",
                        "A korrigiert",
                        logical_id=a.logical_id,
                        expected_version_id=r2.versions[0].version_id,
                        valid_from=(D0 + 2 * DAY).isoformat(),
                        valid_to=(D0 + 4 * DAY).isoformat(),
                        links=[{"rel": "relates_to", "target": b.logical_id}],
                    ),
                    item(
                        "B2",
                        "B geändert",
                        logical_id=b.logical_id,
                        expected_version_id=b.version_id,
                        links=[{"rel": "depends_on", "target": a.logical_id}],
                    ),
                ],
            ),
            deps=deps,
        )
        await conn.commit()
        cur = await conn.execute(
            "SELECT max(version_id) FROM memory_versions"
            " WHERE logical_id = %s AND superseded_at = 'infinity'",
            (b.logical_id,),
        )
        (b_head,) = await cur.fetchone()
        await call_the_day(
            conn,
            world.ctx_a,
            {
                "project": MAIN,
                "request_id": str(uuid.uuid4()),
                "session_id": str(uuid.uuid4()),
                "client": "pytest/0",
                "notes": "Heute: Replay getestet.",
                "decisions": ["Replay nutzt nur payload.resolved"],
                "lessons": [{"title": "Lektion", "body": "Sequenzen nie im Replay", "tags": ["replay"]}],
                "card_update": {"body": "Projektkarte v1"},
                "expected_versions": [{"logical_id": b.logical_id, "version_id": b_head}],
            },
            deps=deps,
        )
        await conn.commit()

        before = await dump_projections(conn)
        assert len(before["memory_versions"]) >= 8 and before["links"] and before["jobs"]
        stats = await rebuild_projections(conn)
        await conn.commit()
        after = await dump_projections(conn)
        assert after == before
        assert stats.events == 4 and stats.versions == len(before["memory_versions"])

        # sequences were reset: a fresh write gets ids beyond the rebuilt maxima and succeeds
        r5 = await write(conn, world.ctx_a, write_req(MAIN, [item("Neu", "nach dem Rebuild")]), deps=deps)
        await conn.commit()
        cur = await conn.execute("SELECT max(version_id), max(logical_id) FROM memory_versions")
        mx_v, mx_l = await cur.fetchone()
        assert r5.versions[0].version_id == mx_v and r5.versions[0].logical_id == mx_l


# --------------------------------------------------------------------------- call_the_day
async def test_call_the_day_one_batch_and_one_close(connect, world, deps) -> None:
    async with await connect() as conn:
        base = await write(conn, world.ctx_a, write_req(MAIN, [item("Basis", "eine Tatsache")]), deps=deps)
        await conn.commit()
        lid, vid = base.versions[0].logical_id, base.versions[0].version_id
        session = str(uuid.uuid4())
        req = {
            "project": MAIN,
            "request_id": str(uuid.uuid4()),
            "session_id": session,
            "client": "pytest/0",
            "notes": "Wir haben den Write-Service gebaut.",
            "decisions": ["Advisory locks pro logical_id", "Ids vorab allokieren"],
            "lessons": [
                {
                    "title": "psycopg infinity",
                    "body": "nullif(valid_to,'infinity') beim Lesen",
                    "tags": ["pg"],
                }
            ],
            "card_update": {"body": "HLMemo Karte: Write-Service fertig."},
            "expected_versions": [{"logical_id": lid, "version_id": vid}],
        }
        res = await call_the_day(conn, world.ctx_a, req, deps=deps)
        await conn.commit()
        assert res.replayed is False and len(res.versions) == 3
        note, lesson, card = res.versions
        assert res.session_note_clue == f"v{note.version_id}"
        assert card.logical_id == world.main_card_lid

        assert await count(conn, "events") == 2
        cur = await conn.execute(
            "SELECT kind, session_id::text FROM events WHERE request_id = %s", (req["request_id"],)
        )
        assert await cur.fetchone() == ("call_the_day", session)
        cur = await conn.execute(
            "SELECT kind, body, tags FROM memory_versions WHERE version_id = %s", (note.version_id,)
        )
        k, body, tags = await cur.fetchone()
        assert k == "session_note" and "## Decisions\n- Advisory locks" in body and tags == ["session"]
        cur = await conn.execute(
            "SELECT kind, title FROM memory_versions WHERE version_id = %s", (lesson.version_id,)
        )
        assert await cur.fetchone() == ("lesson", "psycopg infinity")
        cur = await conn.execute(
            "SELECT rel, dst_logical_id, dst_version_id FROM links"
            " WHERE src_logical_id = %s ORDER BY link_id",
            (card.logical_id,),
        )
        assert await cur.fetchall() == [
            ("derived_from", note.logical_id, note.version_id),
            ("derived_from", lid, vid),
        ]

        # same request again → idempotent replay, still one close
        again = await call_the_day(conn, world.ctx_a, req, deps=deps)
        await conn.commit()
        assert again.replayed is True and again.versions == res.versions
        assert await count(conn, "events", "kind = 'call_the_day'") == 1

        # a second close of the same session (new request_id) → E_SESSION_CLOSED, nothing written
        with pytest.raises(ToolError) as ei:
            await call_the_day(conn, world.ctx_a, dict(req, request_id=str(uuid.uuid4())), deps=deps)
        await conn.rollback()
        assert _err(ei).code == "E_SESSION_CLOSED"
        assert await count(conn, "events") == 2 and await count(conn, "memory_versions") == 4

        # stale expected_versions → E_VERSION_CONFLICT (authorized caller)
        with pytest.raises(ToolError) as ei2:
            await call_the_day(
                conn,
                world.ctx_a,
                dict(
                    req,
                    request_id=str(uuid.uuid4()),
                    session_id=str(uuid.uuid4()),
                    card_update={"body": "neu", "expected_version_id": card.version_id},
                    expected_versions=[{"logical_id": lid, "version_id": vid + 50}],
                ),
                deps=deps,
            )
        await conn.rollback()
        assert _err(ei2).code == "E_VERSION_CONFLICT" and _err(ei2).details["current_version_id"] == vid


# --------------------------------------------------------------------------- codex review C3 / S2 / C5
async def test_spanning_correction_supersedes_all_overlapping_links(connect, world, deps) -> None:
    """C3: an edge kept as two adjacent current segments; a correction spanning both re-declares
    it → every overlapping link is superseded (one left current, no exclusion violation)."""
    async with await connect() as conn:
        tgt = await write(conn, world.ctx_a, write_req(MAIN, [item("Ziel", "Zielobjekt")]), deps=deps)
        await conn.commit()
        link = [{"rel": "depends_on", "target": tgt.versions[0].logical_id}]
        r1 = await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item(
                        "Q",
                        "eins",
                        valid_from=D0.isoformat(),
                        valid_to=(D0 + 7 * DAY).isoformat(),
                        links=link,
                    )
                ],
            ),
            deps=deps,
        )
        await conn.commit()
        lid, v1 = r1.versions[0].logical_id, r1.versions[0].version_id
        r2 = await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item(
                        "Q",
                        "zwei",
                        logical_id=lid,
                        expected_version_id=v1,
                        valid_from=(D0 + 7 * DAY).isoformat(),
                        links=link,
                    )
                ],
            ),
            deps=deps,
        )
        await conn.commit()
        cur = await conn.execute(
            "SELECT link_id FROM links WHERE src_logical_id = %s"
            " AND superseded_at = 'infinity' ORDER BY link_id",
            (lid,),
        )
        old_ids = [r[0] for r in await cur.fetchall()]
        assert len(old_ids) == 2

        fix = write_req(
            MAIN,
            [
                item(
                    "Q",
                    "korrigiert",
                    logical_id=lid,
                    expected_version_id=r2.versions[0].version_id,
                    valid_from=(D0 + 5 * DAY).isoformat(),
                    valid_to=(D0 + 10 * DAY).isoformat(),
                    links=link,
                )
            ],
        )
        await write(conn, world.ctx_a, fix, deps=deps)
        await conn.commit()
        cur = await conn.execute(
            "SELECT link_id, superseded_at = 'infinity', supersedes_link_id FROM links"
            " WHERE src_logical_id = %s ORDER BY link_id",
            (lid,),
        )
        rows = await cur.fetchall()
        assert [r[0] for r in rows[:2]] == old_ids and [r[1] for r in rows] == [False, False, True]
        assert rows[2][2] in old_ids
        p = await event_payload(conn, fix["request_id"])
        assert p["resolved"]["superseded_links"] == old_ids
        assert await count(conn, "links", "src_logical_id = %s AND superseded_at = 'infinity'", (lid,)) == 1


async def test_link_to_hidden_target_uniform_not_found(connect, world, deps) -> None:
    """S2: link targets are authorized with §4.4 (a) on the *selected* endpoint (device_scope and
    home-project membership included) before any existence / membership distinction."""
    async with await connect() as conn:
        # visible item, a second visible item, an item hidden by device_scope (ctx_a is 'personal'),
        # and an item whose home is OTHER (ctx_a may read it there, but it is not in MAIN)
        vis = await write(
            conn, world.ctx_a, write_req(MAIN, [item("Sichtbar", "s"), item("Auch", "a")]), deps=deps
        )
        await conn.commit()
        hid = await write(
            conn, world.ctx_a, write_req(MAIN, [item("Versteckt", "h", device_scope="class:work")]), deps=deps
        )
        await conn.commit()
        foreign = await write(conn, world.ctx_a, write_req(OTHER, [item("Fremd", "f")]), deps=deps)
        await conn.commit()
        v0, v1 = vis.versions
        h = hid.versions[0]
        f = foreign.versions[0]
        events_before = await count(conn, "events")

        async def attempt(links: list[dict]) -> ToolError:
            with pytest.raises(ToolError) as ei:
                await write(conn, world.ctx_a, write_req(MAIN, [item("Neu", "n", links=links)]), deps=deps)
            await conn.rollback()
            return ei.value

        baseline = await attempt([{"rel": "relates_to", "target": h.logical_id + 10_000}])  # nonexistent
        assert baseline.code == "E_NOT_FOUND"
        probes = [
            [{"rel": "relates_to", "target": h.logical_id}],  # hidden by device_scope, unpinned
            [{"rel": "derived_from", "target": h.logical_id}],  # hidden head selected as endpoint
            [{"rel": "derived_from", "target": h.logical_id, "target_version_id": h.version_id}],
            [{"rel": "relates_to", "target": h.logical_id, "target_version_id": h.version_id + 10_000}],
            [{"rel": "derived_from", "target": f.logical_id, "target_version_id": f.version_id}],  # foreign
            [{"rel": "relates_to", "target": f.logical_id}],
            # visible logical id but a pinned version that belongs to a hidden / foreign item: no
            # E_INVALID_ARG "not a version of the target" oracle
            [{"rel": "derived_from", "target": v0.logical_id, "target_version_id": h.version_id}],
            [{"rel": "derived_from", "target": v0.logical_id, "target_version_id": f.version_id}],
            [{"rel": "derived_from", "target": v0.logical_id, "target_version_id": h.version_id + 10_000}],
        ]
        for links in probes:
            err = await attempt(links)
            assert (err.code, err.message, err.details) == (
                baseline.code,
                baseline.message,
                baseline.details,
            ), links
        # both endpoints authorized → the membership distinction may be made
        err = await attempt(
            [{"rel": "derived_from", "target": v0.logical_id, "target_version_id": v1.version_id}]
        )
        assert err.code == "E_INVALID_ARG"
        assert await count(conn, "events") == events_before and await count(conn, "links") == 0

        # a visible target still links, pinned and unpinned
        ok = await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [
                    item(
                        "Neu",
                        "n",
                        links=[
                            {
                                "rel": "derived_from",
                                "target": v0.logical_id,
                                "target_version_id": v0.version_id,
                            },
                            {"rel": "relates_to", "target": v1.logical_id},
                        ],
                    )
                ],
            ),
            deps=deps,
        )
        await conn.commit()
        assert len(ok.versions) == 1 and await count(conn, "links") == 2


async def test_idempotency_hash_is_verbatim_not_normalized(connect, world, deps) -> None:
    """C5: the idempotency key hashes the arguments as received; omitted vs explicit defaults are
    different payloads (E_REQUEST_ID_CONFLICT), a byte-identical resend replays."""
    req = write_req(MAIN, [item("Idem", "verbatim")])
    assert "device_scope" not in req["items"][0]
    async with await connect() as conn:
        first = await write(conn, world.ctx_a, req, deps=deps)
        await conn.commit()
        cur = await conn.execute(
            "SELECT payload, payload_sha256 FROM events WHERE request_id = %s", (req["request_id"],)
        )
        payload, sha = await cur.fetchone()
        canon = json.dumps(req, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        assert sha == hashlib.sha256(canon.encode()).hexdigest()
        assert payload["request"] == req
        assert "device_scope" not in payload["request"]["items"][0]
        assert payload["resolved"]["write"]["items"][0]["device_scope"] == "all"

        explicit = dict(req, items=[dict(req["items"][0], device_scope="all")])
        with pytest.raises(ToolError) as ei:
            await write(conn, world.ctx_a, explicit, deps=deps)
        await conn.rollback()
        assert _err(ei).code == "E_REQUEST_ID_CONFLICT"
        with_null = dict(req, occurred_at=None)  # explicit null vs omitted key: also different bytes
        with pytest.raises(ToolError) as ei2:
            await write(conn, world.ctx_a, with_null, deps=deps)
        await conn.rollback()
        assert _err(ei2).code == "E_REQUEST_ID_CONFLICT"

        again = await write(conn, world.ctx_a, json.loads(json.dumps(req)), deps=deps)
        await conn.commit()
        assert again.replayed is True and again.versions == first.versions
        assert await count(conn, "events") == 1 and await count(conn, "memory_versions") == 1

        # call_the_day: same rule
        close = {
            "project": MAIN,
            "request_id": str(uuid.uuid4()),
            "session_id": str(uuid.uuid4()),
            "client": "pytest/0",
            "notes": "Tag beendet.",
        }
        res = await call_the_day(conn, world.ctx_a, close, deps=deps)
        await conn.commit()
        cur = await conn.execute(
            "SELECT payload, payload_sha256 FROM events WHERE request_id = %s", (close["request_id"],)
        )
        canon = json.dumps(close, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        payload, sha = await cur.fetchone()
        assert payload["request"] == close
        assert sha == hashlib.sha256(canon.encode()).hexdigest()
        with pytest.raises(ToolError) as ei3:
            await call_the_day(conn, world.ctx_a, dict(close, decisions=[]), deps=deps)
        await conn.rollback()
        assert _err(ei3).code == "E_REQUEST_ID_CONFLICT"
        again = await call_the_day(conn, world.ctx_a, dict(close), deps=deps)
        await conn.commit()
        assert again.replayed is True and again.versions == res.versions
