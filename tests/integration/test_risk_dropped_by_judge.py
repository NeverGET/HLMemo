"""``dropped_by_judge`` (consult 114): a judged risk check also lists the retrieval matches the
judge did not warn on, so a relevant lesson the judge drops still reaches the writer.

The first real test drive showed the shape these tests pin: with the judge on, every candidate was
dropped and the verdict was ``no_matching_evidence``, while retrieval ranked the applicable lesson
first. The verdict, the warnings and ``judged`` keep their meaning (G-LIVE-C is unchanged); only the
new list is added, from the same visible, re-checked candidate set, packed after the warnings.
"""

from __future__ import annotations

from typing import Any

import pytest

from hlmemo.core import risk_service as rs
from hlmemo.core.budget import Meter
from hlmemo.core.read_service import default_read_deps
from hlmemo.librarian import risk_judge as rj
from tests.integration._librarian_fixtures import ScriptedLLM, stub_chain
from tests.integration._risk_fixtures import load_cases, seed_world
from tests.integration.test_w2d_risk import _id_of, judge_settings, run_case

pytestmark = pytest.mark.integration

NONE = {"verdict": "none", "matches": []}
KEYS = {"clue", "title", "why", "source_project"}


@pytest.fixture(autouse=True)
async def _clean_tables():  # the module's world survives between its tests
    yield


@pytest.fixture(scope="module")
def deps():
    return default_read_deps()


@pytest.fixture(scope="module")
async def world(connect, deps):  # noqa: ANN001
    return await seed_world(connect, deps.embedder)


def _case(case_id: str) -> str:
    return next(c["task"] for c in load_cases() if c["id"] == case_id)


async def test_judge_drops_everything_yet_the_lesson_reaches_the_writer(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """The test-drive shape: the judge warns on nothing; retrieval ranks the gold lesson first."""
    llm = ScriptedLLM(default=NONE)
    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
    try:
        out, lessons, _ = await run_case(connect, world, deps, _case("P01"), judge=judge)
    finally:
        await judge.aclose()
    assert out["judged"] is True and out["judge"] == rj.OK, out
    assert out["verdict"] == rs.VERDICT_NONE and out["warnings"] == [] and lessons == [], out  # unchanged
    dropped = out["dropped_by_judge"]
    assert 1 <= len(dropped) <= rs.MAX_DROPPED and out["dropped_omitted"] == 0, out
    assert all(set(d) == KEYS for d in dropped), dropped
    # the gold lesson reaches the writer
    assert "L01" in {world.lesson_of_clue(d["clue"]) for d in dropped}, dropped
    assert all("did not warn on it" in d["why"] for d in dropped), dropped
    det, _, _ = await run_case(connect, world, deps, _case("P01"), mode="deterministic")
    # the same set a retrieval-only answer warns on, in the same (best-first) order
    assert [d["clue"] for d in dropped] == [w["clue"] for w in det["warnings"]], (dropped, det)


async def test_retrieval_only_results_carry_no_dropped_list(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    out, _, _ = await run_case(connect, world, deps, _case("P01"), judge=None)  # judge disabled
    assert out["judged"] is False and "dropped_by_judge" not in out and "dropped_omitted" not in out, out
    out, _, _ = await run_case(connect, world, deps, _case("P01"), mode="deterministic", judge=None)
    assert out["judged"] is False and "dropped_by_judge" not in out, out
    # a judge failure (an all-uncited warn) is retrieval-only too
    llm = ScriptedLLM(default={"verdict": "warn", "matches": [{"id": "R99", "why": "invented"}]})
    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
    try:
        out, _, _ = await run_case(connect, world, deps, _case("P01"), judge=judge)
    finally:
        await judge.aclose()
    assert out["judged"] is False and out["reason"] == rj.GUARD and "dropped_by_judge" not in out, out


async def test_matched_lessons_are_not_listed_as_dropped(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    def answer(body: dict[str, Any]) -> dict[str, Any]:
        return {"verdict": "warn", "matches": [{"id": _id_of(body, "bash -s"), "why": "pipes the script"}]}

    llm = ScriptedLLM(default=answer)
    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
    try:
        out, lessons, _ = await run_case(connect, world, deps, _case("P01"), judge=judge)
    finally:
        await judge.aclose()
    assert out["judged"] is True and lessons == ["L01"], out
    assert "L01" not in {world.lesson_of_clue(d["clue"]) for d in out["dropped_by_judge"]}, out


async def test_budget_packing_fills_the_dropped_list_after_the_warnings(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """Every budget: used ≤ limit and exact; dropped entries only once all warnings fit; the two
    counters account for every entry; some budget shows a partly filled list. (The case where a
    warning itself is omitted is a unit test: one warning always fits the 256-token minimum here.)"""

    def answer(body: dict[str, Any]) -> dict[str, Any]:
        return {"verdict": "warn", "matches": [{"id": _id_of(body, "bash -s"), "why": "pipes " + "x " * 120}]}

    meter = Meter()
    llm = ScriptedLLM(default=answer)
    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
    seen_partial = False
    try:
        full, _, _ = await run_case(connect, world, deps, _case("P01"), judge=judge, budget=8000)
        total = len(full["dropped_by_judge"])
        assert total >= 2 and full["omitted"] == 0, full
        for budget in range(256, 1200, 16):
            try:
                out, _, _ = await run_case(connect, world, deps, _case("P01"), judge=judge, budget=budget)
            except Exception as exc:  # noqa: BLE001 - too small for the bare envelope
                assert getattr(exc, "code", "") == "E_BUDGET_TOO_SMALL", exc
                continue
            used = out["budget"]["used"]
            assert meter.count(out) <= used <= budget, (budget, out)
            assert len(out["dropped_by_judge"]) + out["dropped_omitted"] == total, out
            if out["omitted"] > 0:
                assert out["dropped_by_judge"] == [], out  # warnings keep priority
            elif 0 < len(out["dropped_by_judge"]) < total:
                seen_partial = True
    finally:
        await judge.aclose()
    assert seen_partial


async def test_ungranted_and_invisible_lessons_never_appear(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    invisible = {f"v{world.version_of_lesson['L50']}", f"v{world.version_of_lesson['L22']}"}
    llm = ScriptedLLM(default=NONE)
    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
    try:
        for case in load_cases()[:24]:
            out, _, _ = await run_case(connect, world, deps, case["task"], judge=judge)
            listed = out.get("dropped_by_judge", [])
            assert not invisible & {d["clue"] for d in listed}, case["id"]
            assert all(d["source_project"] != "rk-secret" for d in listed), case["id"]
    finally:
        await judge.aclose()


async def test_dropped_entries_are_rechecked_after_the_judge(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """D-062: a grant removed while the judge runs takes that project's lesson out of the list."""
    shell = world.projects["rk-shell"]
    rid = world.ctx_reader.device_id

    async def set_grant(pid: int, present: bool) -> None:
        value = "NULL" if present else "now()"
        async with await connect() as c:
            await c.execute(
                f"UPDATE device_project_grants SET revoked_at = {value}"
                " WHERE device_id = %s AND project_id = %s",
                (rid, pid),
            )
            await c.commit()

    async def drop_shell(_body: dict[str, Any]) -> None:
        await set_grant(shell, False)

    llm = ScriptedLLM(default=NONE)
    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
    try:
        out, _, _ = await run_case(connect, world, deps, _case("P02"), judge=judge)  # L02 lives in rk-shell
        assert "L02" in {world.lesson_of_clue(d["clue"]) for d in out["dropped_by_judge"]}, out  # control
        llm.on_request = drop_shell
        out, _, _ = await run_case(connect, world, deps, _case("P02"), judge=judge)
        assert out["judged"] is True, out
        assert "L02" not in {world.lesson_of_clue(d["clue"]) for d in out["dropped_by_judge"]}, out
    finally:
        await set_grant(shell, True)
        await judge.aclose()
