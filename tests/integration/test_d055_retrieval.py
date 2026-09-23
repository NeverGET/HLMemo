"""D-055 against Postgres: the ``0003_title_lexical`` title list, the shared-corpus DF statistic and
query-centred previews through ``memory.query``."""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hlmemo.core.read_service import query
from hlmemo.core.retrieval import DF_MIN_DOCS, TI_MAX, split_terms
from hlmemo.core.term_stats import StatsCache
from hlmemo.core.write_service import default_deps, write
from hlmemo.db import read_queries as q
from tests.integration._read_fixtures import embedder, read_deps  # noqa: F401 - fixtures by import
from tests.integration._write_fixtures import MAIN, item, seed_world, write_req

pytestmark = pytest.mark.integration


def _filters(world, scopes: list[str]) -> q.QueryFilters:  # noqa: ANN001
    now = datetime.now(UTC)
    return q.QueryFilters(pid=world.main_id, scopes=scopes, valid_at=now, known_at=now, statuses=["active"])


async def _write(conn, world, items):  # noqa: ANN001, ANN202
    versions = []
    for i in range(0, len(items), 50):  # memory.write takes at most 50 items per batch
        ack = await write(conn, world.ctx_a, write_req(MAIN, items[i : i + 50]), deps=default_deps())
        await conn.commit()
        versions.extend(ack.versions)
    return versions


async def test_title_list_matches_path_parts_and_respects_scope(connect) -> None:  # noqa: ANN001
    async with await connect() as conn:
        world = await seed_world(conn)
        target, other, private = await _write(
            conn,
            world,
            [
                item("docs/runbooks/pgbouncer_failover.md § Steps", "Switch the primary, then verify."),
                item("notes/Güvenlik Çözümü.md", "Unrelated body text."),
                item("ops/pgbouncer_private.md", "Private.", device_scope="class:work"),
            ],
        )
        f = _filters(world, ["all", "class:personal", f"device:{world.dev_a}"])
        async with conn.transaction():
            # identifier term → AND of its parts; a plain word → prefix match on a path part
            hits = await q.title_candidates(conn, f, ["pgbouncer_failover.md"], TI_MAX)
            assert [c.version_id for c in hits] == [target.version_id]
            hits = await q.title_candidates(conn, f, ["runbook"], TI_MAX)
            assert [c.version_id for c in hits] == [target.version_id]
            # accents/case folded exactly like normalize(): 'Güvenlik Çözümü' ~ 'guvenlik cozumu'
            hits = await q.title_candidates(conn, f, ["cozum"], TI_MAX)
            assert [c.version_id for c in hits] == [other.version_id]
            # (a) applies: the class:work item is invisible to a personal device
            hits = await q.title_candidates(conn, f, ["pgbouncer"], TI_MAX)
            assert [c.version_id for c in hits] == [target.version_id]
            assert private.version_id not in {c.version_id for c in hits}
            chunk = await conn.execute(
                "SELECT chunk_id FROM chunks WHERE version_id = %s AND ordinal = 0", (target.version_id,)
            )
            assert hits[0].chunk_id == (await chunk.fetchone())[0]
            assert await q.title_candidates(conn, f, [], TI_MAX) == []


async def test_df_statistic_filters_common_terms_from_the_shared_corpus(connect) -> None:  # noqa: ANN001
    n = DF_MIN_DOCS + 10
    async with await connect() as conn:
        world = await seed_world(conn)
        items = [item(f"note {i}", f"common chatter number {i} about deployments") for i in range(n)]
        items.append(item("the needle", "common chatter plus zanzibarite marker"))
        items.append(item("private", "secretword only here", device_scope="class:work"))
        await _write(conn, world, items)
        cache = StatsCache()
        async with conn.transaction():
            stats = await cache.get(conn, world.main_id)
        assert stats.chunks.n == n + 1  # device-scoped rows are not part of the statistic
        assert stats.chunks.prefix_df("common") == n + 1
        assert stats.chunks.prefix_df("zanzibarite") == 1
        assert stats.chunks.prefix_df("secretword") == 0
        assert stats.titles.n == n + 1 and stats.titles.prefix_df("note") == n
        terms = split_terms("common chatter about zanzibarite", stats)
        assert terms.lexical == ["zanzibarite"]
        # all-common query: the rarest terms are kept, the lexical list is never empty
        assert split_terms("common chatter", stats).lexical == ["common", "chatter"]
        async with conn.transaction():
            assert await cache.get(conn, world.main_id) is stats  # cached


async def test_query_uses_title_list_and_centres_previews(connect, read_deps) -> None:  # noqa: ANN001, F811
    filler = " ".join(f"line {i} of unrelated operational prose." for i in range(150))
    async with await connect() as conn:
        world = await seed_world(conn)
        filler_items = [item(f"misc/{i}.md", filler) for i in range(DF_MIN_DOCS)]
        (target,) = await _write(
            conn,
            world,
            [item("docs/retention_policy.md", filler + " Orders are kept for 45 days in kafka. " + filler)],
        )
        await _write(conn, world, filler_items)
        res = await query(
            conn,
            world.ctx_a,
            {"project": MAIN, "query": "retention policy kafka days", "token_budget": 3000},
            deps=read_deps,
        )
        assert read_deps.meter.count(res) == res["budget"]["used"] <= 3000
        top = res["hits"][0]
        assert top["title"] == "docs/retention_policy.md"
        assert "kept for 45 days in kafka" in top["preview"]
        assert target.version_id == int(top["clue"][1:].split(".")[0])


async def test_title_folding_is_identical_on_both_sides(connect) -> None:  # noqa: ANN001
    """Review 33 #2: Greek final sigma, ß/ẞ, Turkish İ/ı and umlauts fold the same in title and query."""
    async with await connect() as conn:
        world = await seed_world(conn)
        (target,) = await _write(conn, world, [item("ΟΔΟΣ/Straße_İstanbul-ıspanak Ärger.md", "body")])
        f = _filters(world, ["all", "class:personal", f"device:{world.dev_a}"])
        raws = ("οδός", "ΟΔΌΣ", "οδος", "STRASSE", "straße", "STRAẞE", "istanbul", "İSTANBUL")
        raws += ("ISPANAK", "ıspanak", "ärger", "ARGER")
        async with conn.transaction():
            for raw in raws:
                terms = split_terms(raw).title
                hits = await q.title_candidates(conn, f, terms, TI_MAX)
                assert [c.version_id for c in hits] == [target.version_id], raw
            cur = await conn.execute(
                "SELECT hlm_title_norm(%s) = hlm_title_norm(%s), hlm_title_norm(%s)",
                ("ΟΔΟΣ", "οδός", "Straße İ ı Ä"),
            )
            same, folded = await cur.fetchone()
            assert same and folded == "strasse i i a"


async def test_df_cache_sees_a_small_write_within_max_staleness(connect) -> None:  # noqa: ANN001
    """D-063 (replaces Sol 33 #1 "next query"): a small write is seen once the background refresh
    it triggered completes (≤ MAX_STALENESS_S); the query right after it is served the old DF
    without blocking."""
    import asyncio

    from hlmemo.core.term_stats import MAX_STALENESS_S

    async with await connect() as conn:
        world = await seed_world(conn)
        await _write(conn, world, [item(f"n{i}", f"plain filler text {i}") for i in range(DF_MIN_DOCS + 5)])
        cache = StatsCache()
        async with conn.transaction():
            before = await cache.get(conn, world.main_id)
        assert before.chunks.prefix_df("quokka") == 0
        await _write(conn, world, [item("one more", "quokka sighting")])
        async with conn.transaction():
            assert await cache.get(conn, world.main_id) is before  # served stale, never blocked
        await asyncio.wait_for(
            asyncio.gather(*[t for t in cache._tasks.values() if not t.done()]), timeout=MAX_STALENESS_S
        )
        async with conn.transaction():
            after = await cache.get(conn, world.main_id)
        assert after is not before and after.chunks.prefix_df("quokka") == 1
        assert cache.refreshes == 2


async def test_historical_query_bypasses_df_filtering(connect, read_deps, monkeypatch) -> None:  # noqa: ANN001, F811
    """Review 33 #6: valid_at/known_at in the past → no DF statistics are consulted."""
    async with await connect() as conn:
        world = await seed_world(conn)
        await _write(conn, world, [item("x", "hello world")])
        calls: list[int] = []
        real = StatsCache.get

        async def spy(self, c, pid):  # noqa: ANN001, ANN202
            calls.append(pid)
            return await real(self, c, pid)

        monkeypatch.setattr(StatsCache, "get", spy)
        req = {"project": MAIN, "query": "hello", "token_budget": 1000}
        await query(conn, world.ctx_a, req, deps=read_deps)
        assert calls == [world.main_id]
        await query(conn, world.ctx_a, {**req, "known_at": "2020-01-01T00:00:00Z"}, deps=read_deps)
        await query(conn, world.ctx_a, {**req, "valid_at": "2020-01-01T00:00:00Z"}, deps=read_deps)
        assert calls == [world.main_id]
