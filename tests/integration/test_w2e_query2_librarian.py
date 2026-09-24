"""W2e x W2b: ``memory.query`` with and without ``synthesize`` on the query/2 read side.

* The ``librarian`` block (open questions, W2b/W2c) is returned with and without the flag. With it,
  the block is packed after the hits AND the synthesis; one exact ``budget.used`` ≤ limit covers
  hits + synthesis + librarian (G2 over a budget sweep).
* The D-057 supersession rule hides the superseded hit with and without the flag; the synthesis is
  never shown the hidden item and never cites it.
* Without the flag (absent or ``false``) the app-bound tool returns ``read_service.query``'s output
  byte for byte, librarian block included.

The librarian (placement/relation) and the synthesis model are scripted stubs: no live LLM call.
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from hlmemo.config import get_settings
from hlmemo.core import synthesis_service as ss
from hlmemo.core.budget import Meter, canonical
from hlmemo.core.errors import ToolError
from hlmemo.core.read_service import query
from hlmemo.librarian.roles import record_role_decision
from hlmemo.librarian.tasks import synthesis as syn
from hlmemo.server.tools import query as query_tool
from tests.integration._librarian_fixtures import (
    ScriptedLLM,
    lib_settings,
    make_provider,
    make_worker,
    seed_reserved,
    stub_chain,
)
from tests.integration._read_fixtures import embedder, read_deps  # noqa: F401 - fixtures by import
from tests.integration._w2b_fixtures import Oracle, embed, write_items
from tests.integration._write_fixtures import MAIN, World, item, seed_world

pytestmark = pytest.mark.integration

D_OLD = datetime(2026, 1, 1, tzinfo=UTC).isoformat()
D_NEW = datetime(2026, 6, 1, tzinfo=UTC).isoformat()
OLD = ("Deploy host", "Production runs on the Hetzner CX33 host in Falkenstein.")
NEW = ("Deploy host moved", "Production moved to the Hostinger KVM 2 host; the Hetzner host is gone.")
CONTRA = {(NEW[0], OLD[0]): ("contradicts", "new", "high")}
FILLER = [
    (f"Production host note {i}", f"Production host runbook step {i}: check the host disk, backups and TLS.")
    for i in range(12)
]
Q = "production host"
AS_OF = {"valid_at": "2030-01-01T00:00:00Z", "known_at": "2030-01-01T00:00:00Z"}
METER = Meter()


@pytest.fixture
async def world(connect) -> World:  # noqa: ANN001
    async with await connect() as conn:
        w = await seed_world(conn)
        await seed_reserved(conn)
        return w


async def _seed(db_dsn, connect, world: World, embedder, *, role: str, same_valid: bool) -> tuple[Any, Any]:  # noqa: ANN001
    """OLD then NEW (+ fillers) in MAIN, embedded, reviewed by the scripted librarian in ``role``."""
    if role != "observer":
        async with await connect() as conn:
            await record_role_decision(conn, role=role, decided_by=world.ctx_admin, decision="D-test")
            await conn.commit()
    (old,) = await write_items(connect, world.ctx_a, MAIN, [item(*OLD, valid_from=D_OLD)])
    (new,) = await write_items(
        connect, world.ctx_a, MAIN, [item(*NEW, valid_from=D_OLD if same_valid else D_NEW)]
    )
    await write_items(connect, world.ctx_a, MAIN, [item(t, b) for t, b in FILLER])
    await embed(connect, embedder)
    llm = ScriptedLLM(default=Oracle(relations=CONTRA))
    provider = make_provider(db_dsn, llm, budget_disabled=True)
    await make_worker(lib_settings(db_dsn, librarian_role=role), provider, connect).drain()
    await provider.aclose()
    return old, new


def _synth(db_dsn: str, llm: ScriptedLLM) -> syn.Synthesizer:
    settings = get_settings(db_dsn=db_dsn, librarian_enabled=True, llm_mode="live", llm_budget_disabled=True)
    return syn.Synthesizer(settings, chain=stub_chain(fallback=False), transport=llm.transport)


def _cite_first(body: dict[str, Any]) -> dict[str, Any]:
    import json

    payload = json.loads(body["messages"][1]["content"].split("INPUT: ", 1)[1])
    ids = [e["id"] for e in payload["excerpts"]]
    return {"status": "answered", "sentences": [{"text": "The host moved.", "cite": [ids[0]]}]}


def _args(budget: int = 2000) -> dict[str, Any]:
    return {"project": MAIN, "query": Q, "token_budget": budget}


async def _plain(connect, world: World, read_deps, budget: int = 2000) -> dict[str, Any]:  # noqa: ANN001
    async with await connect() as conn:
        out = await query(conn, world.ctx_a, _args(budget), deps=read_deps)
        await conn.commit()
    return out


async def _synthesized(connect, world: World, read_deps, synth, budget: int = 2000) -> dict[str, Any]:  # noqa: ANN001
    async with await connect() as conn:
        await conn.commit()  # idle: the synthesis never runs inside a caller's transaction (D-062)
        out = await ss.query_synthesize(
            conn, world.ctx_a, _args(budget), deps=read_deps, synth=synth, tau=1.0
        )
        await conn.commit()
    return out


def _exact(out: dict[str, Any]) -> None:
    assert out["budget"]["used"] == METER.count_text(canonical(out)) <= out["budget"]["limit"], out["budget"]


async def test_librarian_block_with_and_without_synthesize(
    db_dsn, connect, world: World, embedder, read_deps
) -> None:  # noqa: ANN001
    await _seed(db_dsn, connect, world, embedder, role="observer", same_valid=False)
    plain = await _plain(connect, world, read_deps)
    assert plain["contract_version"] == "query/2" and plain["librarian"]["pending_questions"] == 1
    _exact(plain)

    # no flag (absent / false) through the app-bound tool == read_service.query, byte for byte
    # (a fixed future bi-temporal point keeps `as_of` deterministic)
    app = SimpleNamespace(state=SimpleNamespace(read_deps=read_deps, pool=None))
    for budget in (300, 700, 2000):
        args = {**_args(budget), **AS_OF}
        async with await connect() as conn:
            ref = canonical(await query(conn, world.ctx_a, dict(args), deps=read_deps))
            await conn.commit()
        assert '"librarian":' in ref or budget < 2000  # the block takes what the hits left
        for extra in ({}, {"synthesize": False}):
            async with await connect() as conn:
                tool_out = await query_tool.memory_query(conn, world.ctx_a, {**args, **extra}, app=app)
                await conn.commit()
            assert canonical(tool_out) == ref

    llm = ScriptedLLM(default=_cite_first)
    synth = _synth(db_dsn, llm)
    try:
        out = await _synthesized(connect, world, read_deps, synth)
        assert out["synthesis"]["status"] == "answered" and llm.calls == 1, out
        assert set(out["synthesis"]["clues"]) <= {h["clue"] for h in out["hits"]}
        assert out["librarian"] == plain["librarian"]  # same block, packed last
        assert "synthesis" not in plain and "synthesis_unavailable" not in plain
        _exact(out)

        # G2 over a budget sweep: one exact budget.used covers hits + synthesis + librarian
        both = 0
        for budget in range(200, 2001, 45):
            for run in ("plain", "synth"):
                try:
                    if run == "plain":
                        res = await _plain(connect, world, read_deps, budget)
                    else:
                        res = await _synthesized(connect, world, read_deps, synth, budget)
                except ToolError as err:
                    assert err.code == "E_BUDGET_TOO_SMALL" and err.details["min"] > budget
                    continue
                _exact(res)
                if run == "synth":
                    s = res.get("synthesis")
                    if s is not None:
                        assert set(s["clues"]) <= {h["clue"] for h in res["hits"]}
                    if s is not None and "librarian" in res:
                        both += 1
                    if "librarian" in res:
                        share = METER.count(res["librarian"])
                        assert share <= budget * 0.10, (budget, share)
        assert both >= 10, both
    finally:
        await synth.aclose()


async def test_supersession_hides_the_old_hit_with_and_without_synthesize(
    db_dsn, connect, world: World, embedder, read_deps
) -> None:  # noqa: ANN001
    """Same valid_from (no close possible): the applied ``supersedes`` link alone hides OLD."""
    old, _new = await _seed(db_dsn, connect, world, embedder, role="autonomous", same_valid=True)
    async with await connect() as conn:
        cur = await conn.execute("SELECT count(*) FROM links WHERE rel = 'supersedes'")
        assert (await cur.fetchone())[0] == 1
        await conn.commit()
    plain = await _plain(connect, world, read_deps)
    titles = [h["title"] for h in plain["hits"]]
    assert NEW[0] in titles and OLD[0] not in titles

    llm = ScriptedLLM(default=_cite_first)
    synth = _synth(db_dsn, llm)
    try:
        out = await _synthesized(connect, world, read_deps, synth)
        _exact(out)
        titles = [h["title"] for h in out["hits"]]
        assert NEW[0] in titles and OLD[0] not in titles
        assert out["synthesis"]["status"] == "answered", out
        prompt = llm.requests[-1]["messages"][1]["content"]
        assert OLD[1] not in prompt and NEW[1] in prompt  # the hidden item never reaches the model
        old_clues = {c for c in out["synthesis"]["clues"] if c.split(".")[0] == f"v{old.version_id}"}
        assert not old_clues
    finally:
        await synth.aclose()
