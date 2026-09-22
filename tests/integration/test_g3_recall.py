"""G3 — Recall@5 on the frozen synthetic fixture (PHASE0-SPEC §7, VALIDATION-GATES G3).

100 queries (34 TR / 33 DE / 33 EN, 25 identifier-heavy) against 2,000 ``fx-main`` items
(+ 400 ``fx-other`` decoys the reader device cannot see). Recall@5 = the gold ``logical_id`` is
among the first five deduped hits of ``memory.query``. Gate: overall ≥ 0.90; per-language and
identifier-subset recall are printed (run with ``-s`` or ``-rA``).
"""

# ruff: noqa: F811 - pytest fixtures are imported into the module and requested by parameter name
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass

import pytest

from hlmemo.core.read_service import query
from tests.integration._read_fixtures import (  # noqa: F401 - fixtures by import
    MAIN,
    RetrWorld,
    _clean_tables,
    embedder,
    load_queries,
    read_deps,
    retr_world,
    world,
)

pytestmark = pytest.mark.integration

K = 5
THRESHOLD = 0.90
BUDGET = 8000


@dataclass(slots=True)
class Outcome:
    qid: str
    lang: str
    heavy: bool
    query: str
    gold: int
    top: list[int]  # logical ids of the first K hits
    rank: int | None  # 1-based rank of gold among *all* returned hits, None if absent

    @property
    def hit(self) -> bool:
        return self.gold in self.top


@pytest.fixture(scope="module")
async def outcomes(retr_world: RetrWorld, connect, read_deps) -> list[Outcome]:  # noqa: ANN001
    queries = load_queries()
    out: list[Outcome] = []
    async with await connect() as conn:
        for qd in queries:
            res = await query(
                conn,
                retr_world.ctx_reader,
                {"project": MAIN, "query": qd["query"], "token_budget": BUDGET},
                deps=read_deps,
            )
            lids = [retr_world.logical_of_clue(h["clue"]) for h in res["hits"]]
            gold = retr_world.key_to_logical[qd["gold_logical_key"]]
            rank = lids.index(gold) + 1 if gold in lids else None
            out.append(
                Outcome(
                    qd["qid"], qd["lang"], bool(qd["identifier_heavy"]), qd["query"], gold, lids[:K], rank
                )
            )
            await asyncio.sleep(0)
    return out


def _recall(items: list[Outcome]) -> float:
    return sum(1 for o in items if o.hit) / len(items) if items else 0.0


def _report(title: str, items: list[Outcome]) -> None:
    print(f"\n[G3] {title}: Recall@{K} = {_recall(items):.3f} ({sum(o.hit for o in items)}/{len(items)})")


async def test_recall_at_5_ge_090(outcomes: list[Outcome], retr_world: RetrWorld) -> None:
    assert len(outcomes) == 100
    _report("overall", outcomes)
    misses = [o for o in outcomes if not o.hit]
    for o in misses:
        print(
            f"  MISS {o.qid} [{o.lang}{' ident' if o.heavy else ''}] gold={retr_world.logical_to_key[o.gold]}"
            f" rank={o.rank} q={o.query!r}"
        )
    assert _recall(outcomes) >= THRESHOLD, f"Recall@{K} {_recall(outcomes):.3f} < {THRESHOLD}"


async def test_per_language_recall_logged(outcomes: list[Outcome]) -> None:
    by_lang: dict[str, list[Outcome]] = defaultdict(list)
    for o in outcomes:
        by_lang[o.lang].append(o)
    assert set(by_lang) == {"tr", "de", "en"}
    for lang in ("tr", "de", "en"):
        _report(lang.upper(), by_lang[lang])
        light = [o for o in by_lang[lang] if not o.heavy]
        heavy = [o for o in by_lang[lang] if o.heavy]
        print(
            f"      {lang.upper()} light={_recall(light):.3f} ({len(light)})"
            f" heavy={_recall(heavy):.3f} ({len(heavy)})"
        )
    # logged, not gated per language (§7); every language must at least be represented and non-zero
    for lang, items in by_lang.items():
        assert items and _recall(items) > 0.0, f"{lang}: zero recall"


async def test_identifier_queries_recall(outcomes: list[Outcome]) -> None:
    heavy = [o for o in outcomes if o.heavy]
    light = [o for o in outcomes if not o.heavy]
    assert len(heavy) == 25
    _report("identifier-heavy", heavy)
    _report("non-identifier", light)
    assert _recall(heavy) >= THRESHOLD, f"identifier-heavy Recall@{K} {_recall(heavy):.3f} < {THRESHOLD}"
