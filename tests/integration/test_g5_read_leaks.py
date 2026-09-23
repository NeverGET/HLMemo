"""G5 — read-path leaks (Codex review S1/S1b/C4; PHASE0-SPEC §1.1 (3), §3 memory.raw, §4.4).

* S1: a surviving segment of a backdated correction is stored under the *correcting* event.
  ``memory.raw`` on a readable survivor must return the survivor's own content provenance (the
  event/request item it was written as), never the correcting event's replacement item — which
  may carry a narrower ``device_scope`` (``all`` → ``class:work``) or narrower ``project_ids``.
* S1b: every endpoint reference embedded in ``payload_item.links`` (and the drilldown ``links``,
  cursor continuation pages) is filtered with the §4.4 (a) endpoint rule; unauthorized targets are
  omitted, never named.
* C4: ``chunks.project_ids``/``device_scope`` are copies; a stale restrictive copy must not hide a
  version (a) allows and a stale permissive copy must not leak one (a) denies.
* Uniform error: anything failing (a) is ``E_NOT_FOUND`` — no ``E_FORBIDDEN``/``E_VERSION_CONFLICT``.

Small world from ``_write_fixtures.seed_world`` (conftest truncates between tests); ``memory.query``
runs with a stub embedder (no embeddings are drained here, the lexical list carries the hits).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from hlmemo.auth.context import AuthContext, Role
from hlmemo.core.budget import Meter
from hlmemo.core.errors import ToolError
from hlmemo.core.read_service import ReadDeps, drilldown, query, raw
from hlmemo.core.write_service import default_deps, write
from tests.integration._write_fixtures import MAIN, OTHER, World, item, seed_world, write_req

pytestmark = pytest.mark.integration

D0 = datetime(2026, 9, 1, tzinfo=UTC)
DAY = timedelta(days=1)
SECRET = "SECRET-CORRECTION-BODY zk-secret-9931 nur fuer Arbeitsgeraete"
PUBLIC = "public original body: der Dienst svc-orig-77 liest APP_DB_DSN"
LINK_BODY = "link target body lnk-target-42"


class _StubEmbedder:
    """``memory.query`` embeds the query; no vectors are stored here so the list is empty anyway."""

    def embed_query(self, query: str) -> np.ndarray:
        return np.zeros(384, dtype=np.float32)


@pytest.fixture(scope="session")
def wdeps():
    return default_deps()


@pytest.fixture(scope="session")
def rdeps() -> ReadDeps:
    deps = ReadDeps(meter=Meter(), model_dir=Path("."), cursor_secret=b"g5-read-leaks-secret")
    deps._embedder = _StubEmbedder()  # type: ignore[assignment]
    return deps


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        return await seed_world(conn)


def _code(exc_info) -> str:  # noqa: ANN001
    return exc_info.value.code


def _ctx(device_id: int, device_class: str, grants: dict[int, Role]) -> AuthContext:
    return AuthContext(
        device_id=device_id,
        device_class=device_class,
        is_admin=False,
        token_generation=1,
        grants=grants,
        client="pytest/0",
    )


async def _write(conn, ctx, project: str, items: list[dict[str, Any]], deps) -> list[tuple[int, int]]:  # noqa: ANN001
    """Write a batch; returns ``[(logical_id, version_id), ...]`` in item order."""
    ack = await write(conn, ctx, write_req(project, items), deps=deps)
    await conn.commit()
    return [(v.logical_id, v.version_id) for v in ack.versions]


async def _correct(conn, ctx, project: str, lid: int, head: int, deps, **kw: Any) -> int:  # noqa: ANN001
    """Backdated correction of ``[D0+5d, D0+10d)`` — leaves two survivors (§1.1 (3))."""
    fix = item(
        "Zeit",
        SECRET,
        logical_id=lid,
        expected_version_id=head,
        valid_from=(D0 + 5 * DAY).isoformat(),
        valid_to=(D0 + 10 * DAY).isoformat(),
        **kw,
    )
    ack = await write(conn, ctx, write_req(project, [fix]), deps=deps)
    await conn.commit()
    return ack.versions[0].version_id


async def _survivors(conn, lid: int, v_corr: int) -> list[int]:  # noqa: ANN001
    cur = await conn.execute(
        "SELECT version_id FROM memory_versions WHERE logical_id = %s AND superseded_at = 'infinity'"
        " AND version_id <> %s ORDER BY valid_from",
        (lid, v_corr),
    )
    return [r[0] for r in await cur.fetchall()]


async def _raw(conn, ctx, project: str, vid: int, rdeps: ReadDeps, **kw: Any) -> dict[str, Any]:  # noqa: ANN001
    return await raw(
        conn, ctx, {"project": project, "version_id": vid, "token_budget": 4000, **kw}, deps=rdeps
    )


# --------------------------------------------------------------------------- S1
async def test_raw_survivor_does_not_leak_restricted_correction(connect, world: World, wdeps, rdeps) -> None:  # noqa: ANN001
    async with await connect() as conn:
        # (1) all -> class:work: the correction is invisible to the personal device A
        [(lid, v1)] = await _write(
            conn, world.ctx_a, MAIN, [item("Zeit", PUBLIC, valid_from=D0.isoformat())], wdeps
        )
        v_corr = await _correct(conn, world.ctx_a, MAIN, lid, v1, wdeps, device_scope="class:work")
        survivors = await _survivors(conn, lid, v_corr)
        assert len(survivors) == 2
        with pytest.raises(ToolError) as ei:
            await _raw(conn, world.ctx_a, MAIN, v_corr, rdeps)
        assert _code(ei) == "E_NOT_FOUND"
        for svid in survivors:
            res = await _raw(conn, world.ctx_a, MAIN, svid, rdeps)
            assert res["version_id"] == svid and res["supersedes_version_id"] == v1
            assert res["payload_item"]["body"] == PUBLIC
            assert res["payload_item"].get("device_scope", "all") == "all"
            assert SECRET not in str(res)
            # the content provenance is the original write, not the correcting event
            origin = await _raw(conn, world.ctx_a, MAIN, v1, rdeps)
            assert res["source_event"]["event_id"] == origin["source_event"]["event_id"]
            assert res["source_event"]["request_id"] == origin["source_event"]["request_id"]
        # a work device sees the correction itself with its own item
        await conn.execute(
            "INSERT INTO device_project_grants (device_id, project_id, role, granted_by_device_id)"
            " VALUES (%s, %s, 'read', 1)",
            (world.dev_b, world.main_id),
        )
        await conn.commit()
        ctx_b_main = _ctx(world.dev_b, "work", {**world.ctx_b.grants, world.main_id: Role.READ})
        res = await _raw(conn, ctx_b_main, MAIN, v_corr, rdeps)
        assert res["payload_item"]["body"] == SECRET

        # (2) project narrowing [MAIN, OTHER] -> [MAIN]: device B (OTHER only) reads the survivor
        [(lid2, w1)] = await _write(
            conn,
            world.ctx_a,
            MAIN,
            [item("Zeit", PUBLIC, valid_from=D0.isoformat(), project_ids=[MAIN, OTHER])],
            wdeps,
        )
        w_corr = await _correct(conn, world.ctx_a, MAIN, lid2, w1, wdeps, project_ids=[MAIN])
        survivors2 = await _survivors(conn, lid2, w_corr)
        assert len(survivors2) == 2
        with pytest.raises(ToolError) as ei:
            await _raw(conn, world.ctx_b, OTHER, w_corr, rdeps)
        assert _code(ei) == "E_NOT_FOUND"
        for svid in survivors2:
            res = await _raw(conn, world.ctx_b, OTHER, svid, rdeps)
            assert res["payload_item"]["body"] == PUBLIC
            assert res["payload_item"]["project_ids"] == [MAIN, OTHER]
            assert SECRET not in str(res)


async def test_raw_survivor_provenance_unauthorized_is_not_found(connect, world: World, wdeps, rdeps) -> None:  # noqa: ANN001
    """The survivor's origin is authorized under (a) like the addressed row; when it fails, the
    survivor is ``E_NOT_FOUND`` — uniformly, no other code and no partial envelope."""
    async with await connect() as conn:
        [(lid, v1)] = await _write(
            conn, world.ctx_a, MAIN, [item("Zeit", PUBLIC, valid_from=D0.isoformat())], wdeps
        )
        v_corr = await _correct(conn, world.ctx_a, MAIN, lid, v1, wdeps)
        survivors = await _survivors(conn, lid, v_corr)
        # desynchronise: the origin row becomes work-only while the survivor copy still says 'all'
        await conn.execute(
            "UPDATE memory_versions SET device_scope = 'class:work' WHERE version_id = %s", (v1,)
        )
        await conn.commit()
        for svid in survivors:
            with pytest.raises(ToolError) as ei:
                await _raw(conn, world.ctx_a, MAIN, svid, rdeps)
            assert _code(ei) == "E_NOT_FOUND"


# --------------------------------------------------------------------------- S1b
async def _link_world(conn, world: World, wdeps) -> tuple[int, int, int, int, int]:  # noqa: ANN001
    """Item ``src`` (all) with links to a visible target, a work-only target and an OTHER-only
    target; returns ``(src_lid, src_vid, ok_lid, work_lid, other_lid)``."""
    await conn.execute(
        "INSERT INTO device_project_grants (device_id, project_id, role, granted_by_device_id)"
        " VALUES (%s, %s, 'write', 1)",
        (world.dev_b, world.main_id),
    )
    await conn.commit()
    writer = _ctx(world.dev_b, "work", {world.main_id: Role.WRITE, world.other_id: Role.WRITE})
    [(ok_lid, _)] = await _write(
        conn, writer, OTHER, [item("ok", LINK_BODY, project_ids=[OTHER, MAIN])], wdeps
    )
    [(work_lid, _)] = await _write(
        conn,
        writer,
        OTHER,
        [item("work", LINK_BODY, device_scope="class:work", project_ids=[OTHER, MAIN])],
        wdeps,
    )
    [(other_lid, _)] = await _write(conn, world.ctx_a, OTHER, [item("other", LINK_BODY)], wdeps)
    body = " ".join(f"Satz {i}: der Dienst svc-qx7 liest APP_DB_DSN und meldet E4193." for i in range(120))
    src = item(
        "src",
        body,
        project_ids=[OTHER, MAIN],
        links=[
            {"rel": "relates_to", "target": ok_lid},
            {"rel": "relates_to", "target": work_lid},
            {"rel": "derived_from", "target": work_lid},
            {"rel": "relates_to", "target": other_lid},
        ],
    )
    # Work writer can see every endpoint in OTHER; personal readers in MAIN cannot.
    [(src_lid, src_vid)] = await _write(conn, writer, OTHER, [src], wdeps)
    return src_lid, src_vid, ok_lid, work_lid, other_lid


def _named_targets(res: dict[str, Any]) -> set[int]:
    named = {ln["target"] for ln in res["payload_item"].get("links", [])}
    named |= {ln["dst_logical_id"] for ln in res["links"]}
    return named


async def test_payload_item_links_filtered_by_endpoint_authz(connect, world: World, wdeps, rdeps) -> None:  # noqa: ANN001
    async with await connect() as conn:
        src_lid, src_vid, ok_lid, work_lid, other_lid = await _link_world(conn, world, wdeps)
        # reader: personal device with read on MAIN only
        reader = _ctx(world.dev_a, "personal", {world.main_id: Role.READ})
        res = await _raw(conn, reader, MAIN, src_vid, rdeps, token_budget=32000)
        assert _named_targets(res) == {ok_lid}
        assert [ln["target"] for ln in res["payload_item"]["links"]] == [ok_lid]
        assert {work_lid, other_lid}.isdisjoint(_named_targets(res))
        # cursor continuation pages carry the same filtered item
        page_budget = rdeps.meter.count({**res, "chunks": res["chunks"][:1]}) + 200
        first = await _raw(conn, reader, MAIN, src_vid, rdeps, token_budget=page_budget)
        assert first["next_cursor"] is not None and len(first["chunks"]) >= 1
        assert _named_targets(first) == {ok_lid}
        page2 = await _raw(
            conn, reader, MAIN, src_vid, rdeps, token_budget=page_budget, cursor=first["next_cursor"]
        )
        assert page2["chunks"][0]["ordinal"] > first["chunks"][-1]["ordinal"]
        assert _named_targets(page2) == {ok_lid}
        assert [ln["target"] for ln in page2["payload_item"]["links"]] == [ok_lid]
        # Personal reader in OTHER sees ok + other, never the work-only target.
        full = await _raw(conn, world.ctx_a, OTHER, src_vid, rdeps, token_budget=32000)
        assert _named_targets(full) == {ok_lid, other_lid}
        assert src_lid == full["logical_id"]


async def test_drilldown_links_filtered(connect, world: World, wdeps, rdeps) -> None:  # noqa: ANN001
    async with await connect() as conn:
        _src_lid, src_vid, ok_lid, work_lid, other_lid = await _link_world(conn, world, wdeps)
        cur = await conn.execute(
            "SELECT logical_id, version_id FROM memory_versions WHERE logical_id = ANY(%s)",
            ([ok_lid, work_lid, other_lid],),
        )
        vid_of = dict(await cur.fetchall())
        reader = _ctx(world.dev_a, "personal", {world.main_id: Role.READ})
        res = await drilldown(
            conn, reader, {"project": MAIN, "clue_ids": [f"v{src_vid}"], "token_budget": 4000}, deps=rdeps
        )
        clues = {ln["clue"] for it in res["items"] for ln in it["links"]}
        assert clues == {f"v{vid_of[ok_lid]}"}
        # a small budget pages through the body: every page's links stay filtered
        first = await drilldown(
            conn, reader, {"project": MAIN, "clue_ids": [f"v{src_vid}"], "token_budget": 700}, deps=rdeps
        )
        assert first["next_cursor"] is not None
        page2 = await drilldown(
            conn,
            reader,
            {
                "project": MAIN,
                "clue_ids": [f"v{src_vid}"],
                "token_budget": 700,
                "cursor": first["next_cursor"],
            },
            deps=rdeps,
        )
        for page in (first, page2):
            assert {ln["clue"] for it in page["items"] for ln in it["links"]} <= {f"v{vid_of[ok_lid]}"}
            # Whole-token match outside the opaque cursor: a random base64 cursor can contain
            # e.g. "v2" as a substring (observed flake), which is not a leaked clue.
            visible = str({k: v for k, v in page.items() if k != "next_cursor"})
            for hidden in (work_lid, other_lid):
                assert not re.search(rf"(?<![\w-])v{vid_of[hidden]}(?![\w-])", visible)


# --------------------------------------------------------------------------- C4
async def test_stale_chunk_scope_cannot_hide_authorized_version(connect, world: World, wdeps, rdeps) -> None:  # noqa: ANN001
    async with await connect() as conn:
        [(_lid, vid)] = await _write(
            conn, world.ctx_a, MAIN, [item("Hidden", "eindeutig chunkscope-zz81 term im body")], wdeps
        )
        req = {"project": MAIN, "query": "chunkscope-zz81", "token_budget": 2000}
        res = await query(conn, world.ctx_a, req, deps=rdeps)
        assert [h["clue"] for h in res["hits"]] == [f"v{vid}.0"]

        # restrictive stale copy (work-only / foreign project) must not hide the 'all' version
        await conn.execute(
            "UPDATE chunks SET device_scope = 'class:work', project_ids = '{999999}' WHERE version_id = %s",
            (vid,),
        )
        await conn.commit()
        res = await query(conn, world.ctx_a, req, deps=rdeps)
        assert [h["clue"] for h in res["hits"]] == [f"v{vid}.0"]
        assert res["hits"][0]["device_scope"] == "all" and res["evidence"] == "matched"

        # permissive stale copy must not leak a version the reader's (a) denies
        await conn.execute(
            "UPDATE chunks SET device_scope = 'all', project_ids = %s WHERE version_id = %s",
            ([world.main_id], vid),
        )
        await conn.execute(
            "UPDATE memory_versions SET device_scope = 'class:work' WHERE version_id = %s", (vid,)
        )
        await conn.commit()
        res = await query(conn, world.ctx_a, req, deps=rdeps)
        assert res["hits"] == [] and res["evidence"] == "none"


# --------------------------------------------------------------------------- uniform error
async def test_unauthorized_reads_are_uniform_not_found(connect, world: World, wdeps, rdeps) -> None:  # noqa: ANN001
    async with await connect() as conn:
        [(lid, v1)] = await _write(
            conn, world.ctx_a, MAIN, [item("Zeit", PUBLIC, valid_from=D0.isoformat())], wdeps
        )
        v_corr = await _correct(conn, world.ctx_a, MAIN, lid, v1, wdeps, device_scope="class:work")
        [(_olid, ovid)] = await _write(conn, world.ctx_a, OTHER, [item("other", LINK_BODY)], wdeps)
        cur = await conn.execute("SELECT max(version_id) + 1000 FROM memory_versions")
        (missing,) = await cur.fetchone()
        reader = _ctx(world.dev_a, "personal", {world.main_id: Role.READ})
        # raw: out of device scope, other project, unknown, superseded-but-out-of-scope: one code
        for target in (v_corr, ovid, missing):
            with pytest.raises(ToolError) as ei:
                await _raw(conn, reader, MAIN, target, rdeps)
            assert _code(ei) == "E_NOT_FOUND", target
            assert not ei.value.details.get("current_version_id")
        # drilldown: same for clues, whole-item and chunk
        for clue in (f"v{v_corr}", f"v{v_corr}.0", f"v{ovid}", f"v{missing}"):
            with pytest.raises(ToolError) as ei:
                await drilldown(
                    conn, reader, {"project": MAIN, "clue_ids": [clue], "token_budget": 2000}, deps=rdeps
                )
            assert _code(ei) == "E_NOT_FOUND", clue
        # the superseded original stays readable via raw (historical, (a) only)
        res = await _raw(conn, reader, MAIN, v1, rdeps)
        assert res["superseded_at"] is not None and res["payload_item"]["body"] == PUBLIC
