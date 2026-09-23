"""D-026 — ``memory.raw`` pages an oversized verbatim ``payload_item`` instead of failing with
``E_BUDGET_TOO_SMALL``: the body moves into the cursor stream as ``payload_body`` segments, every
page stays within its budget, the concatenated segments equal the written body, and chunks and
links still arrive in full. A payload that fits is served exactly as before (no paging fields)."""

from __future__ import annotations

import pytest

from hlmemo.core.budget import Meter, canonical
from hlmemo.core.read_service import default_read_deps, raw
from hlmemo.core.write_service import default_deps, write
from tests.integration._write_fixtures import MAIN, World, item, seed_world, write_req

pytestmark = pytest.mark.integration

BODY = " ".join(
    f"Absatz {i}: Der Dienst svc-qx7 liest APP_DB_DSN; Hata kodu E{4000 + i}." for i in range(600)
)


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        return await seed_world(conn)


async def _write(connect, world: World, body: str) -> int:  # noqa: ANN001
    async with await connect() as conn:
        tgt = await write(conn, world.ctx_a, write_req(MAIN, [item("Ziel", "kurz")]), deps=default_deps())
        await conn.commit()
        res = await write(
            conn,
            world.ctx_a,
            write_req(
                MAIN,
                [item("Gross", body, links=[{"rel": "relates_to", "target": tgt.versions[0].logical_id}])],
            ),
            deps=default_deps(),
        )
        await conn.commit()
    return res.versions[0].version_id


@pytest.mark.parametrize("budget", [1200, 2000, 4000])
async def test_raw_pages_oversized_payload_item(connect, world: World, budget: int) -> None:  # noqa: ANN001
    vid = await _write(connect, world, BODY)
    meter = Meter()
    deps = default_read_deps()
    pages = []
    cursor = None
    async with await connect() as conn:
        while True:
            req = {"project": MAIN, "version_id": vid, "token_budget": budget}
            if cursor:
                req["cursor"] = cursor
            page = await raw(conn, world.ctx_a, req, deps=deps)
            await conn.commit()
            assert page["budget"]["used"] <= budget
            assert meter.count_text(canonical(page)) == page["budget"]["used"]
            pages.append(page)
            cursor = page["next_cursor"]
            if cursor is None:
                break
            assert len(pages) < 1000
    assert all(p["payload_item"].get("body_paged") is True and "body" not in p["payload_item"] for p in pages)
    assert pages[0]["payload_item"]["title"] == "Gross"
    segments = [s for p in pages for s in p["payload_body"]]
    assert "".join(s["text"] for s in segments) == BODY
    assert [s["char_start"] for s in segments] == list(range(0, len(BODY), 512))
    chunks = [c for p in pages for c in p["chunks"]]
    assert [c["ordinal"] for c in chunks] == list(range(len(chunks))) and len(chunks) > 1
    assert [ln["rel"] for p in pages for ln in p["links"]] == ["relates_to"]


async def test_raw_small_payload_unchanged(connect, world: World) -> None:  # noqa: ANN001
    vid = await _write(connect, world, "ein kurzer Text")
    async with await connect() as conn:
        page = await raw(conn, world.ctx_a, {"project": MAIN, "version_id": vid, "token_budget": 2000})
    assert page["payload_item"]["body"] == "ein kurzer Text"
    assert "payload_body" not in page and "body_paged" not in page["payload_item"]
    assert page["next_cursor"] is None
