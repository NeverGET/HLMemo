Review a release candidate of HLMemo (MCP memory server): `memory.risk_check` gains an additive field `dropped_by_judge`. Working dir = the candidate's worktree (branch risk-dropped, commit 5c5a71f), read-only. The threat model and severity rubric, written before this review, follow; then the diff.
Reply in <= 30 lines: verdict (GO / GO-WITH-FIXES / NO-GO), then findings HIGH/MEDIUM/LOW per the rubric with file:line and a concrete trigger (a HIGH needs the shape of a reproducing test). Answer explicitly: (a) can any dropped entry come from an item the caller could not see as a warning? (b) can verdict/warnings/judged change for any existing input? (c) can a secret or redacted text reach `why`/`title`? (d) is the budget packing correct (warnings first, counters right)?

# risk_check: show the lessons the judge drops. Threat model and severity rubric (before review)

Written 2026-10-07, before the change is reviewed. Trigger: the first real test drive (BACKLOG "First real test-drive
findings"). With the judge on, `memory.risk_check` returned `no_matching_evidence` after dropping 10 of 10 candidates.
The dropped set included the lesson that applied, which retrieval had ranked first; with the judge timed out, the same
lesson came back as the top warning.

## The change
An additive field. When the judge runs and drops candidates that passed the deterministic retrieval threshold, the
response lists the best of them as `dropped_by_judge: [{clue, title, why, source_project}]`, where `why` is the judge's
reason. `verdict`, `warnings` and `judged` keep their meaning, so the release-blocking live gate G-LIVE-C (catch rate,
false-warn rate) measures the same thing as before. The protocol (R18), the digest and the skill tell writers to read
`dropped_by_judge`, just as they read `warnings`.

## What is protected
- **Safety of the risk check:** a relevant lesson must reach the writer. A false `no_matching_evidence` is the failure
  this change addresses.
- **The verdict contract:** `verdict ∈ {warn, no_matching_evidence}`, D-014 (never "no risk"), and D-071 (G-LIVE-C
  false-warn ≤ 0.10).
- **Privacy and scope:** the new list must obey the same visibility as `warnings` (the projects the caller can read,
  device scope, the D-083 isolation) and the same budget packing (`omitted`).
- **Cost and latency:** no new LLM call. The judge's existing output is reused.

## Failure modes to look for
1. A dropped candidate that the caller could not see as a warning (another project, a device-scoped item, a closed
   or superseded item) leaks through the new list.
2. The new list is unbounded or pushes `warnings` out of the token budget.
3. `verdict` or `judged` changes for an existing input (G-LIVE-C drift).
4. Retrieval-only mode (judge down or timed out) reports a misleading or empty `dropped_by_judge` instead of omitting
   the field.
5. The judge's free-text reason echoes memory text that the redaction (librarian/redact.py) would have hidden, or
   secret-shaped text.
6. Docs tell writers something the code does not do.

## Severity rubric
- **HIGH:** failure modes 1, 3 or 5 with ordinary input; a relevant dropped lesson still invisible in the
  test-drive repro.
- **MEDIUM:** 2 or 4; missing tests for the listed cases.
- **LOW:** wording and docs.

## Review plan
Astra low and Sol xhigh in parallel, at most 2 rounds, a reproducing test for each HIGH. Before the review: the
test-drive repro on a local copy (the dropped lesson appears in `dropped_by_judge`), and the risk tests pass.

## Diff (src, tests)
diff --git a/src/hlmemo/brief/protocol_digest.txt b/src/hlmemo/brief/protocol_digest.txt
index c126c7f..2d724e7 100644
--- a/src/hlmemo/brief/protocol_digest.txt
+++ b/src/hlmemo/brief/protocol_digest.txt
@@ -3,7 +3,7 @@ Read first: the injected brief, else memory.query your task (token_budget 3000).
 - Query each area before work there; previews are excerpts, so drill the top hits (memory.drilldown) first.
 - superseded:true is not current: follow superseded_by. memory.ask (when listed): open its handles to check quotes.
 - Before a deploy, migration, prod-data change, deletion, force-push or secret: memory.risk_check, and memory.query
-  kinds:[lesson] for the component. On warn, read each lesson and say how you comply; no match is not safety.
+  kinds:[lesson] for the component. Read warnings and dropped_by_judge, say how you comply; no match is not safety.
 Write into project <slug> only (another slug or extra project_ids only if the owner asks). Memory is append-only.
 - Durable items: current facts, decisions + reasons, lessons, dated episodes. No transcript/tool-output dumps, guesses
   as fact, other projects' data or secret values (keys, tokens, passwords, DSNs with passwords); name where they live.
diff --git a/src/hlmemo/core/risk_service.py b/src/hlmemo/core/risk_service.py
index 89b7068..adab7e2 100644
--- a/src/hlmemo/core/risk_service.py
+++ b/src/hlmemo/core/risk_service.py
@@ -1,7 +1,8 @@
 """``memory.risk_check`` (PHASE2-4-ROADMAP W2d; report D.3 #7, D.4(b); D-014, D-062, D-067).
 
 ``risk_check(conn, ctx, args, deps=, judge=, detach=, reconnect=)`` → ``{project, verdict, judged,
-judge, reason?, warnings, omitted, candidates_considered, guard_dropped?, budget}``;
+judge, reason?, warnings, omitted, dropped_by_judge?, dropped_omitted?, candidates_considered,
+guard_dropped?, budget}``;
 ``verdict ∈ {"warn", "no_matching_evidence"}``. Per D-014 it never says "no risk": the absence of
 a warning only means no stored lesson matched.
 
@@ -30,6 +31,15 @@ a warning only means no stored lesson matched.
    the result is the deterministic verdict: ``judged:false``, ``judge:"retrieval_only"``, and
    ``reason`` names the cause. Judged results carry ``judge:"ok"`` (``"ok_fallback"``: the
    fallback tier judged, D-066).
+   **Dropped by the judge** (consult 114, 2026-10-07): a judged result also lists, as
+   ``dropped_by_judge``, the candidates that passed the deterministic threshold (``det_score ≥
+   TAU``, the set a retrieval-only answer would warn on) but that the judge did not match, best
+   first, at most ``MAX_DROPPED``. The judge explains only its matches, so each entry's ``why`` is
+   deterministic (kind, lists, score), and a secret-shaped title is masked. The first real test
+   drive showed the judge dropping the one lesson that applied while retrieval ranked it first; the
+   writer now sees it and decides. ``verdict``, ``warnings`` and ``judged`` keep their meaning, so
+   the G-LIVE-C rates are unchanged. Privacy-withheld candidates are never in this list (the judge
+   never saw them; they warn at ``TAU_STRICT``). Retrieval-only results carry neither field.
 4. **D-062**: the judge never runs inside a transaction. Over the API the request transaction
    (device FOR SHARE) is committed and its connection returned (``detach``) before the judge;
    afterwards ONE short transaction on a fresh connection re-checks the device (revoked / expired /
@@ -38,7 +48,10 @@ a warning only means no stored lesson matched.
 
 The output is packed like every read result: ``budget.used`` is the exact o200k count of the
 canonical JSON; warnings are added in order until the next one would not fit (``omitted`` counts
-the rest); ``E_BUDGET_TOO_SMALL`` only if the envelope without warnings does not fit.
+the rest); ``E_BUDGET_TOO_SMALL`` only if the envelope without warnings does not fit. A judged
+result's ``dropped_by_judge`` is packed after the warnings, which keep priority: when any warning
+is omitted the list is empty, and ``dropped_omitted`` counts the dropped candidates that did not
+fit (its own counter, so ``omitted`` keeps meaning warnings only).
 """
 
 from __future__ import annotations
@@ -72,6 +85,7 @@ from hlmemo.core.retrieval import (
     rrf_fuse,
     split_terms,
 )
+from hlmemo.core.secret_guard import strong_secret_rule
 from hlmemo.core.write_models import SLUG_RE, _Strict, parse_request
 from hlmemo.db import auth_queries
 from hlmemo.db import read_queries as rq
@@ -83,6 +97,7 @@ TOP_K = 10
 LIST_LIMIT = 50  # per RRF list over the lesson universe
 MATCH_CHUNKS = 3  # matching chunks per candidate handed to the judge (its text window)
 MAX_WARNINGS = 3
+MAX_DROPPED = 3  # candidates above TAU that the judge did not warn on (``dropped_by_judge``)
 WHY_MAX = rj.WHY_MAX
 
 # ---- calibrated constants (cal split of tests/fixtures/risk; see its README) --------------------
@@ -234,6 +249,20 @@ def _warning(c: RiskCandidate, why: str) -> dict[str, Any]:
     return {"clue": c.clue, "title": c.title, "why": why[:WHY_MAX], "source_project": c.project}
 
 
+def _safe_title(title: str) -> str:
+    """A title matching a strong secret shape (possible in items written before PV-1) is masked."""
+    rule = strong_secret_rule(title)
+    return title if rule is None else f"<redacted:{rule}>"
+
+
+def _dropped(c: RiskCandidate) -> dict[str, Any]:
+    why = (
+        f"Retrieval matched this {c.kind} ({c.lists} lists, score {c.det_score:.3f}), but the librarian "
+        "judge did not warn on it. Drill the clue and decide whether it applies to the task."
+    )
+    return {"clue": c.clue, "title": _safe_title(c.title), "why": why[:WHY_MAX], "source_project": c.project}
+
+
 def _det_why(c: RiskCandidate, reason: str) -> str:
     return (
         f"Retrieval-only match on this {c.kind} ({c.lists} lists, score {c.det_score:.3f}); "
@@ -249,9 +278,16 @@ def deterministic_warnings(
 
 
 def _pack(
-    deps: ReadDeps, envelope: dict[str, Any], budget: int, warnings: list[dict[str, Any]]
+    deps: ReadDeps,
+    envelope: dict[str, Any],
+    budget: int,
+    warnings: list[dict[str, Any]],
+    dropped: list[dict[str, Any]] | None = None,
 ) -> dict[str, Any]:
     meter = deps.meter
+    if dropped is not None:  # the dropped list's empty state is part of the envelope the warnings pack in
+        envelope["dropped_by_judge"] = []
+        envelope["dropped_omitted"] = len(dropped)
     prefix = [0]
     base = meter.settle(envelope, budget)
     for w in warnings:
@@ -263,6 +299,17 @@ def _pack(
 
     try:
         pack_prefix(meter, envelope, budget, len(warnings), apply, lambda n: base + prefix[n])
+        if dropped and envelope["omitted"] == 0:  # warnings keep priority
+            base2 = meter.settle(envelope, budget)
+            prefix2 = [0]
+            for d in dropped:
+                prefix2.append(prefix2[-1] + meter.count(d) + 1)
+
+            def apply_dropped(n: int) -> None:
+                envelope["dropped_by_judge"] = dropped[:n]
+                envelope["dropped_omitted"] = len(dropped) - n
+
+            pack_prefix(meter, envelope, budget, len(dropped), apply_dropped, lambda n: base2 + prefix2[n])
     except BudgetError as exc:
         raise ToolError(exc.code, str(exc), **exc.details) from exc
     return envelope
@@ -356,6 +403,7 @@ async def risk_check(
             all_withheld = res.status == rj.NO_CANDIDATES and len(res.denied) == len(cands)
     by_vid = {c.version_id: c for c in cands}
     warned: list[tuple[int, dict[str, Any]]]
+    dropped: list[tuple[int, dict[str, Any]]] = []
     if called and res.judged:
         judged = True
         warned = [(vid, _warning(by_vid[vid], why or "Applies to this task.")) for vid, why in res.matches]
@@ -363,19 +411,28 @@ async def risk_check(
         withheld = [c for c in cands if c.version_id in res.denied]
         warned += _det(withheld, "withheld by the privacy policy", TAU_STRICT)
         warned = warned[:MAX_WARNINGS]
+        # shown to the judge, above the retrieval threshold, not matched: the writer decides (consult 114)
+        excluded = {vid for vid, _ in res.matches} | res.denied
+        passed = sorted(
+            (c for c in cands if c.det_score >= TAU and c.version_id not in excluded),
+            key=lambda c: -c.det_score,
+        )
+        dropped = [(c.version_id, _dropped(c)) for c in passed[:MAX_DROPPED]]
     elif all_withheld:  # nothing could be sent: the privacy-withheld rule applies to every candidate
         warned = _det(cands, "withheld by the privacy policy", TAU_STRICT)
     else:
         warned = _det(cands, status.replace("_", " "), TAU)
 
     if called:  # time passed without a transaction: authority and visibility are re-checked
+        shown = [v for v, _ in warned] + [v for v, _ in dropped]
         if released:
             assert reconnect is not None
             async with reconnect() as fresh_conn:
-                visible = await _recheck(fresh_conn, ctx, request.project, [v for v, _ in warned])
+                visible = await _recheck(fresh_conn, ctx, request.project, shown)
         else:
-            visible = await _recheck(conn, ctx, request.project, [v for v, _ in warned])
+            visible = await _recheck(conn, ctx, request.project, shown)
         warned = [(v, w) for v, w in warned if v in visible]
+        dropped = [(v, d) for v, d in dropped if v in visible]
 
     envelope: dict[str, Any] = {
         "project": project.slug,
@@ -390,7 +447,7 @@ async def risk_check(
         envelope["reason"] = "withheld" if all_withheld else status
     if guard_dropped:
         envelope["guard_dropped"] = guard_dropped
-    return _pack(deps, envelope, budget, [w for _, w in warned])
+    return _pack(deps, envelope, budget, [w for _, w in warned], [d for _, d in dropped] if judged else None)
 
 
 def _det(cands: list[RiskCandidate], reason: str, tau: float) -> list[tuple[int, dict[str, Any]]]:
@@ -400,6 +457,7 @@ def _det(cands: list[RiskCandidate], reason: str, tau: float) -> list[tuple[int,
 
 __all__ = [
     "MATCH_CHUNKS",
+    "MAX_DROPPED",
     "TAU",
     "TAU_STRICT",
     "TOOL",
diff --git a/src/hlmemo/server/tools/risk.py b/src/hlmemo/server/tools/risk.py
index 85fa1c3..901b345 100644
--- a/src/hlmemo/server/tools/risk.py
+++ b/src/hlmemo/server/tools/risk.py
@@ -60,7 +60,8 @@ REGISTER_LESSON_INPUT = _schema(
 
 RISK_CHECK_DESCRIPTION = (
     "Check a planned task against past lessons you can read (all granted projects). warn lists "
-    "warnings to heed; no_matching_evidence is not a safety guarantee; judged=false: retrieval only."
+    "warnings to heed; read dropped_by_judge too (retrieval matches the judge did not warn on); "
+    "no_matching_evidence is not a safety guarantee; judged=false: retrieval only."
 )
 REGISTER_LESSON_DESCRIPTION = (
     "Record a lesson from a mistake (mistake/fix/context); idempotent per request_id."
diff --git a/tests/integration/test_risk_dropped_by_judge.py b/tests/integration/test_risk_dropped_by_judge.py
new file mode 100644
index 0000000..3af1d95
--- /dev/null
+++ b/tests/integration/test_risk_dropped_by_judge.py
@@ -0,0 +1,176 @@
+"""``dropped_by_judge`` (consult 114): a judged risk check also lists the retrieval matches the
+judge did not warn on, so a relevant lesson the judge drops still reaches the writer.
+
+The first real test drive showed the shape these tests pin: with the judge on, every candidate was
+dropped and the verdict was ``no_matching_evidence``, while retrieval ranked the applicable lesson
+first. The verdict, the warnings and ``judged`` keep their meaning (G-LIVE-C is unchanged); only the
+new list is added, from the same visible, re-checked candidate set, packed after the warnings.
+"""
+
+from __future__ import annotations
+
+from typing import Any
+
+import pytest
+
+from hlmemo.core import risk_service as rs
+from hlmemo.core.budget import Meter
+from hlmemo.core.read_service import default_read_deps
+from hlmemo.librarian import risk_judge as rj
+from tests.integration._librarian_fixtures import ScriptedLLM, stub_chain
+from tests.integration._risk_fixtures import load_cases, seed_world
+from tests.integration.test_w2d_risk import _id_of, judge_settings, run_case
+
+pytestmark = pytest.mark.integration
+
+NONE = {"verdict": "none", "matches": []}
+KEYS = {"clue", "title", "why", "source_project"}
+
+
+@pytest.fixture(autouse=True)
+async def _clean_tables():  # the module's world survives between its tests
+    yield
+
+
+@pytest.fixture(scope="module")
+def deps():
+    return default_read_deps()
+
+
+@pytest.fixture(scope="module")
+async def world(connect, deps):  # noqa: ANN001
+    return await seed_world(connect, deps.embedder)
+
+
+def _case(case_id: str) -> str:
+    return next(c["task"] for c in load_cases() if c["id"] == case_id)
+
+
+async def test_judge_drops_everything_yet_the_lesson_reaches_the_writer(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
+    """The test-drive shape: the judge warns on nothing; retrieval ranks the gold lesson first."""
+    llm = ScriptedLLM(default=NONE)
+    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
+    try:
+        out, lessons, _ = await run_case(connect, world, deps, _case("P01"), judge=judge)
+    finally:
+        await judge.aclose()
+    assert out["judged"] is True and out["judge"] == rj.OK, out
+    assert out["verdict"] == rs.VERDICT_NONE and out["warnings"] == [] and lessons == [], out  # unchanged
+    dropped = out["dropped_by_judge"]
+    assert 1 <= len(dropped) <= rs.MAX_DROPPED and out["dropped_omitted"] == 0, out
+    assert all(set(d) == KEYS for d in dropped), dropped
+    # the gold lesson reaches the writer
+    assert "L01" in {world.lesson_of_clue(d["clue"]) for d in dropped}, dropped
+    assert all("did not warn on it" in d["why"] for d in dropped), dropped
+    det, _, _ = await run_case(connect, world, deps, _case("P01"), mode="deterministic")
+    # the same set a retrieval-only answer warns on, in the same (best-first) order
+    assert [d["clue"] for d in dropped] == [w["clue"] for w in det["warnings"]], (dropped, det)
+
+
+async def test_retrieval_only_results_carry_no_dropped_list(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
+    out, _, _ = await run_case(connect, world, deps, _case("P01"), judge=None)  # judge disabled
+    assert out["judged"] is False and "dropped_by_judge" not in out and "dropped_omitted" not in out, out
+    out, _, _ = await run_case(connect, world, deps, _case("P01"), mode="deterministic", judge=None)
+    assert out["judged"] is False and "dropped_by_judge" not in out, out
+    # a judge failure (an all-uncited warn) is retrieval-only too
+    llm = ScriptedLLM(default={"verdict": "warn", "matches": [{"id": "R99", "why": "invented"}]})
+    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
+    try:
+        out, _, _ = await run_case(connect, world, deps, _case("P01"), judge=judge)
+    finally:
+        await judge.aclose()
+    assert out["judged"] is False and out["reason"] == rj.GUARD and "dropped_by_judge" not in out, out
+
+
+async def test_matched_lessons_are_not_listed_as_dropped(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
+    def answer(body: dict[str, Any]) -> dict[str, Any]:
+        return {"verdict": "warn", "matches": [{"id": _id_of(body, "bash -s"), "why": "pipes the script"}]}
+
+    llm = ScriptedLLM(default=answer)
+    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
+    try:
+        out, lessons, _ = await run_case(connect, world, deps, _case("P01"), judge=judge)
+    finally:
+        await judge.aclose()
+    assert out["judged"] is True and lessons == ["L01"], out
+    assert "L01" not in {world.lesson_of_clue(d["clue"]) for d in out["dropped_by_judge"]}, out
+
+
+async def test_budget_packing_fills_the_dropped_list_after_the_warnings(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
+    """Every budget: used ≤ limit and exact; dropped entries only once all warnings fit; the two
+    counters account for every entry; some budget shows a partly filled list. (The case where a
+    warning itself is omitted is a unit test: one warning always fits the 256-token minimum here.)"""
+
+    def answer(body: dict[str, Any]) -> dict[str, Any]:
+        return {"verdict": "warn", "matches": [{"id": _id_of(body, "bash -s"), "why": "pipes " + "x " * 120}]}
+
+    meter = Meter()
+    llm = ScriptedLLM(default=answer)
+    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
+    seen_partial = False
+    try:
+        full, _, _ = await run_case(connect, world, deps, _case("P01"), judge=judge, budget=8000)
+        total = len(full["dropped_by_judge"])
+        assert total >= 2 and full["omitted"] == 0, full
+        for budget in range(256, 1200, 16):
+            try:
+                out, _, _ = await run_case(connect, world, deps, _case("P01"), judge=judge, budget=budget)
+            except Exception as exc:  # noqa: BLE001 - too small for the bare envelope
+                assert getattr(exc, "code", "") == "E_BUDGET_TOO_SMALL", exc
+                continue
+            used = out["budget"]["used"]
+            assert meter.count(out) <= used <= budget, (budget, out)
+            assert len(out["dropped_by_judge"]) + out["dropped_omitted"] == total, out
+            if out["omitted"] > 0:
+                assert out["dropped_by_judge"] == [], out  # warnings keep priority
+            elif 0 < len(out["dropped_by_judge"]) < total:
+                seen_partial = True
+    finally:
+        await judge.aclose()
+    assert seen_partial
+
+
+async def test_ungranted_and_invisible_lessons_never_appear(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
+    invisible = {f"v{world.version_of_lesson['L50']}", f"v{world.version_of_lesson['L22']}"}
+    llm = ScriptedLLM(default=NONE)
+    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
+    try:
+        for case in load_cases()[:24]:
+            out, _, _ = await run_case(connect, world, deps, case["task"], judge=judge)
+            listed = out.get("dropped_by_judge", [])
+            assert not invisible & {d["clue"] for d in listed}, case["id"]
+            assert all(d["source_project"] != "rk-secret" for d in listed), case["id"]
+    finally:
+        await judge.aclose()
+
+
+async def test_dropped_entries_are_rechecked_after_the_judge(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
+    """D-062: a grant removed while the judge runs takes that project's lesson out of the list."""
+    shell = world.projects["rk-shell"]
+    rid = world.ctx_reader.device_id
+
+    async def set_grant(pid: int, present: bool) -> None:
+        value = "NULL" if present else "now()"
+        async with await connect() as c:
+            await c.execute(
+                f"UPDATE device_project_grants SET revoked_at = {value}"
+                " WHERE device_id = %s AND project_id = %s",
+                (rid, pid),
+            )
+            await c.commit()
+
+    async def drop_shell(_body: dict[str, Any]) -> None:
+        await set_grant(shell, False)
+
+    llm = ScriptedLLM(default=NONE)
+    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
+    try:
+        out, _, _ = await run_case(connect, world, deps, _case("P02"), judge=judge)  # L02 lives in rk-shell
+        assert "L02" in {world.lesson_of_clue(d["clue"]) for d in out["dropped_by_judge"]}, out  # control
+        llm.on_request = drop_shell
+        out, _, _ = await run_case(connect, world, deps, _case("P02"), judge=judge)
+        assert out["judged"] is True, out
+        assert "L02" not in {world.lesson_of_clue(d["clue"]) for d in out["dropped_by_judge"]}, out
+    finally:
+        await set_grant(shell, True)
+        await judge.aclose()
diff --git a/tests/unit/test_risk_dropped_units.py b/tests/unit/test_risk_dropped_units.py
new file mode 100644
index 0000000..c4e6692
--- /dev/null
+++ b/tests/unit/test_risk_dropped_units.py
@@ -0,0 +1,84 @@
+"""Units of ``dropped_by_judge`` (consult 114): the entry shape and the secret mask on titles."""
+
+from __future__ import annotations
+
+import json
+import random
+import string
+
+from hlmemo.core import risk_service as rs
+from hlmemo.core.budget import Meter
+
+
+def _cand(title: str, score: float = 0.06) -> rs.RiskCandidate:
+    return rs.RiskCandidate(
+        version_id=812,
+        logical_id=40,
+        project_id=3,
+        project_ids=[3],
+        project="demo",
+        device_scope="all",
+        kind="lesson",
+        title=title,
+        rrf=score,
+        det_score=score,
+        vector_dist=0.1,
+        ranks=(1, None, 2, 1),
+    )
+
+
+def test_dropped_entry_shape_and_deterministic_why() -> None:
+    d = rs._dropped(_cand("Bump app.js?v= on every frontend change"))
+    assert set(d) == {"clue", "title", "why", "source_project"}
+    assert d["clue"] == "v812" and d["source_project"] == "demo"
+    assert "LTV lists" in d["why"] and "did not warn on it" in d["why"] and len(d["why"]) <= rs.WHY_MAX
+
+
+def test_secret_shaped_title_is_masked() -> None:
+    rnd = random.Random(7)
+    token = "gh" + "p_" + "".join(rnd.choice(string.ascii_letters + string.digits) for _ in range(36))
+    d = rs._dropped(_cand(f"Rotate the deploy token {token} after use"))
+    assert d["title"] == "<redacted:github-token>"
+    assert token not in json.dumps(d)
+
+
+class _Deps:
+    meter = Meter()
+
+
+def _env() -> dict[str, object]:
+    return {"project": "demo", "verdict": "warn", "judged": True, "judge": "ok", "warnings": [], "omitted": 0}
+
+
+def _item(i: int, words: int) -> dict[str, str]:
+    return {"clue": f"v{i}", "title": f"lesson {i}", "why": "w " * words, "source_project": "demo"}
+
+
+def test_pack_omits_the_dropped_list_while_a_warning_is_omitted() -> None:
+    warnings = [_item(i, 140) for i in range(3)]
+    dropped = [_item(10 + i, 20) for i in range(3)]
+    out = rs._pack(_Deps(), _env(), 400, warnings, dropped)
+    assert out["omitted"] > 0 and out["dropped_by_judge"] == [] and out["dropped_omitted"] == 3, out
+    assert Meter().count(out) <= out["budget"]["used"] <= 400
+
+
+def test_pack_fills_dropped_after_all_warnings() -> None:
+    warnings = [_item(1, 20)]
+    dropped = [_item(10 + i, 60) for i in range(3)]
+    full = rs._pack(_Deps(), _env(), 4000, warnings, dropped)
+    assert full["omitted"] == 0 and len(full["dropped_by_judge"]) == 3 and full["dropped_omitted"] == 0
+    for budget in range(256, 600, 8):
+        out = rs._pack(_Deps(), _env(), budget, warnings, dropped)
+        assert Meter().count(out) <= out["budget"]["used"] <= budget, budget
+        assert len(out["dropped_by_judge"]) + out["dropped_omitted"] == 3, out
+        assert out["dropped_by_judge"] == dropped[: len(out["dropped_by_judge"])], out  # best first, a prefix
+
+
+def test_pack_without_a_dropped_list_adds_no_field() -> None:
+    out = rs._pack(_Deps(), _env(), 1000, [_item(1, 10)], None)
+    assert "dropped_by_judge" not in out and "dropped_omitted" not in out
+
+
+def test_plain_title_is_kept() -> None:
+    title = "Test schema migrations on a prod copy"
+    assert rs._dropped(_cand(title))["title"] == title
