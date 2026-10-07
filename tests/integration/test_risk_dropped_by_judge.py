"""``dropped_by_judge`` (consult 114): a judged risk check also lists the retrieval matches the
judge did not warn on, so a relevant lesson the judge drops still reaches the writer.

The first real test drive showed the shape these tests pin: with the judge on, every candidate was
dropped and the verdict was ``no_matching_evidence``, while retrieval ranked the applicable lesson
first. The verdict, the warnings and ``judged`` keep their meaning (G-LIVE-C is unchanged); only the
new list is added, from the same visible, re-checked candidate set, packed after the warnings.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from hlmemo.core import risk_service as rs
from hlmemo.core.budget import Meter
from hlmemo.core.lesson_service import register_lesson
from hlmemo.core.read_service import default_read_deps
from hlmemo.core.write_service import default_deps, write
from hlmemo.librarian import risk_judge as rj
from hlmemo.worker.main import drain
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
    """Every budget: used ≤ limit and exact; dropped entries only once all warnings fit, never an
    empty list; the counters account for every entry; some budget shows a partly filled list. (The
    comparison with the pre-change packing and the omitted-warning case are unit tests.)"""

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
            listed = out.get("dropped_by_judge")
            if listed is None:  # no room for even one entry: both fields left out
                assert "dropped_omitted" not in out, out
                continue
            assert out["omitted"] == 0 and listed, out  # only after every warning, never empty
            assert len(listed) + out["dropped_omitted"] == total, out
            seen_partial = seen_partial or len(listed) < total
    finally:
        await judge.aclose()
    assert seen_partial


async def test_ungranted_and_invisible_lessons_never_appear(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
    """L50: a project without a grant; L22: another device class (class:work). Never candidates."""
    invisible = {f"v{world.version_of_lesson['L50']}", f"v{world.version_of_lesson['L22']}"}
    llm = ScriptedLLM(default=NONE)
    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
    try:
        for case in load_cases():
            out, _, _ = await run_case(connect, world, deps, case["task"], judge=judge)
            listed = out.get("dropped_by_judge", [])
            assert not invisible & {d["clue"] for d in listed}, case["id"]
            assert all(d["source_project"] != "rk-secret" for d in listed), case["id"]
    finally:
        await judge.aclose()


async def test_privacy_withheld_lessons_never_appear_although_above_the_threshold(
    connect,  # noqa: ANN001
    world,  # noqa: ANN001
    deps,  # noqa: ANN001
    db_dsn,  # noqa: ANN001
) -> None:
    """Review 115. Withheld from the judge by the privacy gate, so never in the dropped list: L16 is
    scoped to this one device (``device:<id>``); L27/L28/L36 live in ``hlm-global``, whose policy is
    ``librarian: off``. The test is not vacuous: each of them passes TAU for some case here."""
    withheld = {world.version_of_lesson[k]: k for k in ("L16", "L27", "L28", "L36")}
    above: set[str] = set()
    llm = ScriptedLLM(default=NONE)
    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
    try:
        for case in load_cases():
            async with await connect() as conn:
                cands, _ = await rs.candidates(
                    conn, world.ctx_reader, world.projects["rk-main"], case["task"], deps
                )
                await conn.commit()
            above |= {
                withheld[c.version_id] for c in cands if c.version_id in withheld and c.det_score >= rs.TAU
            }
            out, _, _ = await run_case(connect, world, deps, case["task"], judge=judge)
            listed = {d["clue"] for d in out.get("dropped_by_judge", [])}
            assert not listed & {f"v{v}" for v in withheld}, (case["id"], listed)
    finally:
        await judge.aclose()
    assert above, "no withheld lesson passed TAU: the test would prove nothing"


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
        assert "L02" not in {world.lesson_of_clue(d["clue"]) for d in out.get("dropped_by_judge", [])}, out
    finally:
        await set_grant(shell, True)
        await judge.aclose()


# --------------------------------------------------------------------------- closed / superseded
# Last in the module: it adds lessons to the shared world.
_TASK = "Rotate the zeppelin-quartz relay certificate on the blimp gateway before the mesh renewal window."


async def _lesson(connect, world, deps, mistake: str) -> tuple[int, int]:  # noqa: ANN001
    async with await connect() as conn:
        res = await register_lesson(
            conn,
            world.ctx_loader,
            {
                "project": "rk-main",
                "request_id": str(uuid.uuid4()),
                "mistake": mistake,
                "fix": "Do: renew the relay certificate one gateway at a time.\nAvoid: a mesh-wide swap.",
                "tags": ["blimp@1", "active"],
            },
            deps=default_deps(),
        )
        await conn.commit()
    await drain(connect, deps.embedder)
    return res["version_id"], res["logical_id"]


async def _close(connect, world, vid: int, lid: int) -> None:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT title, body, valid_from FROM memory_versions WHERE version_id = %s", (vid,)
        )
        title, body, valid_from = await cur.fetchone()
        await write(
            conn,
            world.ctx_loader,
            {
                "project": "rk-main",
                "request_id": str(uuid.uuid4()),
                "client": "pytest-risk/0",
                "items": [
                    {
                        "kind": "lesson",
                        "title": title,
                        "body": body,
                        "logical_id": lid,
                        "expected_version_id": vid,
                        "valid_from": valid_from.isoformat(),
                        "close": True,
                        "tags": ["blimp@1", "active"],
                    }
                ],
            },
            deps=default_deps(),
        )
        await conn.commit()


async def _current(connect, vids: list[int]) -> list[int]:  # noqa: ANN001
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT version_id FROM memory_versions WHERE version_id = ANY(%s)"
            " AND valid_to > now() AND superseded_at > now()",
            (vids,),
        )
        return [r[0] for r in await cur.fetchall()]


async def test_closed_lessons_never_appear_also_when_closed_during_the_judge(
    connect,  # noqa: ANN001
    world,  # noqa: ANN001
    deps,  # noqa: ANN001
    db_dsn,  # noqa: ANN001
) -> None:
    """Review 115: a closed lesson (a whole supersede closes it the same way) is not current, so it
    is never a candidate; one closed while the judge runs is removed by the D-062 re-check."""
    vid, lid = await _lesson(
        connect,
        world,
        deps,
        "Zeppelin-quartz relay certificate rotation on the blimp gateway broke the mesh\n"
        "When: all gateways were rotated at once during the renewal window.",
    )
    llm = ScriptedLLM(default=NONE)
    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
    try:
        out, _, _ = await run_case(connect, world, deps, _TASK, judge=judge)
        assert f"v{vid}" in {d["clue"] for d in out.get("dropped_by_judge", [])}, out  # control: listed

        async def close_it(_body: dict[str, Any]) -> None:
            await _close(connect, world, vid, lid)

        llm.on_request = close_it  # closed while the judge runs
        out, _, _ = await run_case(connect, world, deps, _TASK, judge=judge)
        assert out["judged"] is True, out
        assert f"v{vid}" not in {d["clue"] for d in out.get("dropped_by_judge", [])}, out
        assert await _current(connect, [vid]) == [], "the closed lesson is still current"
        llm.on_request = None  # closed before the check: not even a candidate
        out, _, _ = await run_case(connect, world, deps, _TASK, judge=judge)
        assert f"v{vid}" not in {d["clue"] for d in out.get("dropped_by_judge", [])}, out
    finally:
        await judge.aclose()
