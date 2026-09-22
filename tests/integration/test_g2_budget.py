"""G2 — budget guarantee, service level (PHASE0-SPEC §3 budget rule, §4.12; VALIDATION-GATES G2).

Service-level part: ``core/read_service`` ``query``/``drilldown``/``raw`` on the loaded G3 world.
Every successful response re-measured with the §3 meter (o200k_base over the canonical JSON)
must satisfy ``used == measured <= limit``; the only admissible failure is
``E_BUDGET_TOO_SMALL`` with ``min`` above the offered budget (raw's fixed part — verbatim
``payload_item`` — can exceed a small budget). Wire-level G2 tests (single text block, no
``structuredContent``, ``tools/list`` without ``outputSchema``) belong to the server task and
live at module level; the class below overrides the conftest truncation only for its own tests.
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

import random
from collections import Counter

import pytest

from hlmemo.core.budget import BUDGET_MAX, BUDGET_MIN, Meter, canonical
from hlmemo.core.errors import ToolError
from hlmemo.core.read_service import drilldown, query, raw
from tests.integration._read_fixtures import (  # noqa: F401 - fixtures by import
    MAIN,
    OTHER,
    RetrWorld,
    embedder,
    load_queries,
    read_deps,
    retr_world,
    world,
)

pytestmark = pytest.mark.integration

SEED = 20260922
N_CALLS = 1000
BUDGET_RANGE = (500, 8000)


def _measure(meter: Meter, resp: dict) -> int:
    return meter.count_text(canonical(resp))


class TestServiceBudget:
    @pytest.fixture
    def _clean_tables(self) -> None:
        """The G3 world (10+ min to build) must survive these tests: no truncation."""
        yield

    async def test_1000_random_budgets_never_overflow(self, world: RetrWorld, connect, read_deps) -> None:  # noqa: ANN001
        rng = random.Random(SEED)
        meter = read_deps.meter
        queries = [q["query"] for q in load_queries()]
        # only fx-main versions: fx-other rows are E_NOT_FOUND for the reader (see the isolation test)
        versions = sorted(
            v for v, lid in world.version_to_logical.items() if world.project_of_logical(lid) == MAIN
        )
        ctx = world.ctx_reader
        kinds = Counter()
        outcomes = Counter()
        max_ratio = 0.0
        async with await connect() as conn:
            for i in range(N_CALLS):
                budget = rng.randint(*BUDGET_RANGE)
                kind = rng.choice(
                    ("query", "query", "query", "drilldown", "drilldown", "drilldown", "raw", "raw")
                )
                kinds[kind] += 1
                vid = rng.choice(versions)
                if kind == "query":
                    req = {"project": MAIN, "query": rng.choice(queries), "token_budget": budget}
                    if rng.random() < 0.2:
                        req["include_archived"] = True
                    if rng.random() < 0.1:
                        req["kinds"] = rng.sample(["fact", "lesson", "episode", "experience"], 2)
                    call = query
                elif kind == "drilldown":
                    n = rng.randint(1, 4)
                    picks = rng.sample(versions, n)
                    clues = [f"v{v}" if rng.random() < 0.5 else f"v{v}.0" for v in picks]
                    req = {"project": MAIN, "clue_ids": clues, "token_budget": budget}
                    call = drilldown
                else:
                    req = {"project": MAIN, "version_id": vid, "token_budget": budget}
                    call = raw
                try:
                    resp = await call(conn, ctx, req, deps=read_deps)
                except ToolError as exc:
                    assert exc.code == "E_BUDGET_TOO_SMALL", (
                        f"call {i} {kind}: unexpected {exc.code}: {exc.message}"
                    )
                    assert exc.details.get("min", 0) > budget, (
                        f"call {i}: min {exc.details} not above {budget}"
                    )
                    outcomes[f"{kind}:too_small"] += 1
                    continue
                measured = _measure(meter, resp)
                assert resp["budget"]["limit"] == budget
                assert resp["budget"]["tokenizer"] == "o200k_base"
                assert resp["budget"]["used"] == measured, (
                    f"call {i} {kind}: used {resp['budget']['used']} != {measured}"
                )
                assert measured <= budget, f"call {i} {kind}: {measured} > {budget}"
                max_ratio = max(max_ratio, measured / budget)
                outcomes[f"{kind}:ok"] += 1
                # a page with a cursor must continue under the same guarantee
                if kind != "query" and resp.get("next_cursor") and rng.random() < 0.3:
                    req2 = {**req, "cursor": resp["next_cursor"]}
                    resp2 = await call(conn, ctx, req2, deps=read_deps)
                    m2 = _measure(meter, resp2)
                    assert resp2["budget"]["used"] == m2 <= budget
                    outcomes[f"{kind}:page2"] += 1
        print(f"\n[G2] {N_CALLS} calls {dict(kinds)} -> {dict(outcomes)}; max used/limit = {max_ratio:.3f}")
        assert outcomes["query:ok"] == kinds["query"], "query never fails on budget within [500, 8000]"
        assert sum(v for k, v in outcomes.items() if k.endswith(":ok")) >= 0.9 * N_CALLS

    async def test_budget_below_min_rejected(self, world: RetrWorld, connect, read_deps) -> None:  # noqa: ANN001
        ctx = world.ctx_reader
        vid = min(world.version_to_logical)
        cases = [
            (query, {"project": MAIN, "query": "svc"}),
            (drilldown, {"project": MAIN, "clue_ids": [f"v{vid}"]}),
            (raw, {"project": MAIN, "version_id": vid}),
        ]
        async with await connect() as conn:
            cur = await conn.execute("SELECT count(*) FROM events")
            (events_before,) = await cur.fetchone()
            for call, base in cases:
                with pytest.raises(ToolError) as ei:
                    await call(conn, ctx, {**base, "token_budget": BUDGET_MIN - 1}, deps=read_deps)
                assert ei.value.code == "E_BUDGET_TOO_SMALL" and ei.value.details["min"] == BUDGET_MIN
                with pytest.raises(ToolError) as ei:
                    await call(conn, ctx, {**base, "token_budget": BUDGET_MAX + 1}, deps=read_deps)
                assert ei.value.code == "E_BUDGET_TOO_LARGE"
                with pytest.raises(ToolError) as ei:
                    await call(conn, ctx, base, deps=read_deps)  # missing budget
                assert ei.value.code == "E_INVALID_ARG"
                with pytest.raises(ToolError) as ei:
                    await call(conn, ctx, {**base, "token_budget": "1000"}, deps=read_deps)
                assert ei.value.code == "E_INVALID_ARG"
            # exactly the minimum is accepted by query (envelope fits)
            resp = await query(
                conn, ctx, {"project": MAIN, "query": "svc", "token_budget": BUDGET_MIN}, deps=read_deps
            )
            assert resp["budget"]["used"] <= BUDGET_MIN
            # nothing was written by any of the above (query never writes; rejected calls never write)
            cur = await conn.execute("SELECT count(*) FROM events")
            (events_after,) = await cur.fetchone()
            assert events_after == events_before

    async def test_evidence_none_when_no_hits(self, world: RetrWorld, connect, read_deps) -> None:  # noqa: ANN001
        ctx = world.ctx_reader
        async with await connect() as conn:
            # nothing is valid in year 2000: deduped candidate set empty → evidence "none"
            resp = await query(
                conn,
                ctx,
                {
                    "project": MAIN,
                    "query": "svc-qx7 APP_DB_DSN",
                    "token_budget": 1000,
                    "valid_at": "2000-01-01T00:00:00Z",
                },
                deps=read_deps,
            )
            assert resp["evidence"] == "none" and resp["hits"] == [] and resp["omitted"] == 0
            assert resp["budget"]["used"] <= 1000 and resp["indexing_pending"] is False
            # a kind filter that excludes every item
            resp = await query(
                conn,
                ctx,
                {"project": MAIN, "query": "svc-qx7", "token_budget": 1000, "kinds": ["project_card"]},
                deps=read_deps,
            )
            assert resp["evidence"] == "none" and resp["hits"] == []
            # matched: the same query at now()
            resp = await query(
                conn,
                ctx,
                {"project": MAIN, "query": "svc-qx7 APP_DB_DSN", "token_budget": 1000},
                deps=read_deps,
            )
            assert resp["evidence"] == "matched" and resp["hits"]
            # budget exhaustion is never "none": a tiny budget with hits still says matched
            resp = await query(
                conn,
                ctx,
                {"project": MAIN, "query": "svc-qx7 APP_DB_DSN", "token_budget": BUDGET_MIN},
                deps=read_deps,
            )
            assert resp["evidence"] == "matched"
            assert resp["omitted"] >= 1 or resp["hits"]

    async def test_isolation_fx_other_never_leaks(self, world: RetrWorld, connect, read_deps) -> None:  # noqa: ANN001
        reader, other = world.ctx_reader, world.ctx_other
        main_lids = {lid for lid, key in world.logical_to_key.items() if key.startswith(MAIN + ":")}
        other_lids = {lid for lid, key in world.logical_to_key.items() if key.startswith(OTHER + ":")}
        assert len(other_lids) == 400 and len(main_lids) == 2000
        other_versions = [world.logical_to_version[lid] for lid in sorted(other_lids)]
        queries = load_queries()
        async with await connect() as conn:
            leaked = 0
            for qd in queries:
                resp = await query(
                    conn,
                    reader,
                    {"project": MAIN, "query": qd["query"], "token_budget": 8000},
                    deps=read_deps,
                )
                for h in resp["hits"]:
                    if world.logical_of_clue(h["clue"]) not in main_lids:
                        leaked += 1
            assert leaked == 0
            # the reader has no grant on fx-other at all
            with pytest.raises(ToolError) as ei:
                await query(
                    conn, reader, {"project": OTHER, "query": "svc", "token_budget": 1000}, deps=read_deps
                )
            assert ei.value.code == "E_FORBIDDEN_PROJECT"
            # fx-other rows addressed through fx-main are not found (no distinction from unknown)
            for vid in other_versions[:50]:
                with pytest.raises(ToolError) as ei:
                    await raw(
                        conn,
                        reader,
                        {"project": MAIN, "version_id": vid, "token_budget": 8000},
                        deps=read_deps,
                    )
                assert ei.value.code == "E_NOT_FOUND"
                with pytest.raises(ToolError) as ei:
                    await drilldown(
                        conn,
                        reader,
                        {"project": MAIN, "clue_ids": [f"v{vid}"], "token_budget": 8000},
                        deps=read_deps,
                    )
                assert ei.value.code == "E_NOT_FOUND"
            # and the work device with a grant only on fx-other sees only fx-other, never fx-main
            for qd in queries[:20]:
                resp = await query(
                    conn,
                    other,
                    {"project": OTHER, "query": qd["query"], "token_budget": 4000},
                    deps=read_deps,
                )
                assert all(world.logical_of_clue(h["clue"]) in other_lids for h in resp["hits"])
            with pytest.raises(ToolError) as ei:
                await query(
                    conn, other, {"project": MAIN, "query": "svc", "token_budget": 1000}, deps=read_deps
                )
            assert ei.value.code == "E_FORBIDDEN_PROJECT"
            # no access event was recorded for any rejected call
            cur = await conn.execute(
                "SELECT count(*) FROM events e JOIN memory_versions mv ON mv.version_id = ANY("
                "  ARRAY(SELECT jsonb_array_elements_text(e.payload->'resolved'->'version_ids'))::bigint[])"
                " WHERE e.kind = 'access' AND e.device_id = %s AND %s = ANY(mv.project_ids)",
                (reader.device_id, world.other_id),
            )
            (n,) = await cur.fetchone()
            assert n == 0
