"""``unjudged`` (second test drive, 2026-10-08): a retrieval-only risk check also lists the best
candidates it did not warn on, so a judge timeout no longer answers with nothing to read.

The shape these tests pin: a short task ranked the applicable lesson in the top ``TOP_K`` but below
``TAU``; the judged call of the same task warned on it, the timed-out call returned
``no_matching_evidence`` with an empty result. ``verdict``, ``warnings`` and ``judged`` keep their
meaning (G-LIVE-C is unchanged); only the new list is added, from the same visible candidate set,
re-checked after a judge call, packed after the warnings.
"""

from __future__ import annotations

from typing import Any

import pytest

from hlmemo.core import risk_service as rs
from hlmemo.librarian import risk_judge as rj
from tests.integration._librarian_fixtures import ScriptedLLM, stub_chain
from tests.integration._risk_fixtures import load_cases, seed_world
from tests.integration.test_w2d_risk import MAIN, judge_settings, run_case

pytestmark = pytest.mark.integration

NONE = {"verdict": "none", "matches": []}
UNCITED = {"verdict": "warn", "matches": [{"id": "R99", "why": "invented"}]}  # the D-067 guard fails it
KEYS = {"clue", "title", "why", "source_project"}


@pytest.fixture(autouse=True)
async def _clean_tables():  # the module's world survives between its tests
    yield


@pytest.fixture(scope="module")
def deps():
    from hlmemo.core.read_service import default_read_deps

    return default_read_deps()


@pytest.fixture(scope="module")
async def world(connect, deps):  # noqa: ANN001
    return await seed_world(connect, deps.embedder)


def _case(case_id: str) -> str:
    return next(c["task"] for c in load_cases() if c["id"] == case_id)


async def _cands(connect, world, deps, task: str) -> list[rs.RiskCandidate]:  # noqa: ANN001
    async with await connect() as conn:
        cands, _ = await rs.candidates(conn, world.ctx_reader, world.projects[MAIN], task, deps)
        await conn.commit()
    return cands


def _expected(cands: list[rs.RiskCandidate], warned: set[str]) -> list[str]:
    rest = [c for c in cands if c.clue not in warned and c.det_score > 0]
    return [c.clue for c in sorted(rest, key=lambda c: -c.det_score)[: rs.MAX_UNJUDGED]]


async def test_retrieval_only_lists_the_best_unwarned_candidates(connect, world, deps) -> None:  # noqa: ANN001
    """Every case, judge disabled: the list is exactly the best unwarned candidates, never a warned
    one; the test-drive shape (nothing warned, the gold lesson listed) occurs in the fixtures."""
    shape = any_missed = 0
    for case in load_cases():
        out, _, _ = await run_case(connect, world, deps, case["task"], judge=None)
        assert out["judged"] is False and out["reason"] == rj.DISABLED, out
        warned = {w["clue"] for w in out["warnings"]}
        listed = out.get("unjudged", [])
        assert [u["clue"] for u in listed] == _expected(
            await _cands(connect, world, deps, case["task"]), warned
        )
        assert not warned & {u["clue"] for u in listed}, case["id"]
        assert all(set(u) == KEYS and "no judge checked it (disabled)" in u["why"] for u in listed), listed
        assert out.get("unjudged_omitted", 0) == 0, out  # BUDGET 4000 holds every entry
        missed = set(case["gold"]) - {world.lesson_of_clue(c) for c in warned}
        any_missed += bool(missed)
        if missed & {world.lesson_of_clue(u["clue"]) for u in listed}:
            shape += 1
    print(f"retrieval-only missed a gold lesson in {any_missed} cases; unjudged lists it in {shape}")
    assert shape, "no missed gold lesson is listed: the test proves nothing"


async def test_deterministic_mode_and_judge_failures_list_unjudged(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    task = _case("P01")
    out, _, _ = await run_case(connect, world, deps, task, mode="deterministic", judge=None)
    assert out["judged"] is False and out["reason"] == rj.NOT_REQUESTED and out["unjudged"], out
    assert all("(not requested)" in u["why"] for u in out["unjudged"]), out
    llm = ScriptedLLM(default=UNCITED)
    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
    try:
        failed, _, _ = await run_case(connect, world, deps, task, judge=judge)
    finally:
        await judge.aclose()
    assert failed["judged"] is False and failed["reason"] == rj.GUARD, failed
    assert [u["clue"] for u in failed["unjudged"]] == [u["clue"] for u in out["unjudged"]], (failed, out)
    assert [w["clue"] for w in failed["warnings"]] == [w["clue"] for w in out["warnings"]], (failed, out)


async def test_judged_results_carry_no_unjudged(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    llm = ScriptedLLM(default=NONE)
    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
    try:
        for case_id in ("P01", "P02"):
            out, _, _ = await run_case(connect, world, deps, _case(case_id), judge=judge)
            assert out["judged"] is True and "unjudged" not in out and "unjudged_omitted" not in out, out
    finally:
        await judge.aclose()


async def test_ungranted_and_invisible_lessons_never_appear(connect, world, deps) -> None:  # noqa: ANN001
    """L50: a project without a grant; L22: another device class (class:work). Never candidates."""
    invisible = {f"v{world.version_of_lesson['L50']}", f"v{world.version_of_lesson['L22']}"}
    for case in load_cases():
        out, _, _ = await run_case(connect, world, deps, case["task"], judge=None)
        listed = out.get("unjudged", [])
        assert not invisible & {u["clue"] for u in listed}, case["id"]
        assert all(u["source_project"] != "rk-secret" for u in listed), case["id"]


async def test_unjudged_entries_are_rechecked_after_a_failed_judge_call(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """D-062: the judge was called (and failed), so time passed without a transaction; a grant
    removed meanwhile takes that project's lessons out of warnings and unjudged alike."""
    shell = world.projects["rk-shell"]
    rid = world.ctx_reader.device_id
    shell_clues = {
        c.clue for c in await _cands(connect, world, deps, _case("P02")) if c.project == "rk-shell"
    }
    assert shell_clues, "P02 retrieves no rk-shell lesson: the test would prove nothing"

    async def set_grant(present: bool) -> None:
        value = "NULL" if present else "now()"
        async with await connect() as c:
            await c.execute(
                f"UPDATE device_project_grants SET revoked_at = {value}"
                " WHERE device_id = %s AND project_id = %s",
                (rid, shell),
            )
            await c.commit()

    async def drop_shell(_body: dict[str, Any]) -> None:
        await set_grant(False)

    def shown(out: dict[str, Any]) -> set[str]:
        return {w["clue"] for w in out["warnings"]} | {u["clue"] for u in out.get("unjudged", [])}

    llm = ScriptedLLM(default=UNCITED)
    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
    try:
        out, _, _ = await run_case(connect, world, deps, _case("P02"), judge=judge)
        assert out["reason"] == rj.GUARD and shown(out) & shell_clues, out  # control: listed
        llm.on_request = drop_shell
        out, _, _ = await run_case(connect, world, deps, _case("P02"), judge=judge)
        assert out["judged"] is False and out["reason"] == rj.GUARD, out
        assert not shown(out) & shell_clues, out
    finally:
        await set_grant(True)
        await judge.aclose()
