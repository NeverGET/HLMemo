Review a release candidate of HLMemo (MCP memory server): `memory.risk_check` gains an additive field `unjudged` on retrieval-only results, and query previews skip a leading YAML frontmatter block. Working dir = a clean export of the candidate (commit 6c18254), read-only. The threat model and severity rubric, written before this review, follow; then the diff.
Reply in <= 30 lines: verdict (GO / GO-WITH-FIXES / NO-GO), then findings HIGH/MEDIUM/LOW per the rubric with file:line and a concrete trigger (a HIGH needs the shape of a reproducing test). Answer explicitly: (a) can any unjudged entry come from an item the caller could not get as a warning? (b) can verdict/warnings/omitted/judged or a budget error change for any existing input? (c) can a secret shape reach the response unredacted? (d) can preview_text drop body text that is not frontmatter, or backtrack super-linearly?

# risk_check `unjudged` and frontmatter-free previews: threat model and severity rubric (before review)

Written 2026-10-08, before the change is reviewed. Trigger: the second real test drive (BACKLOG "Second test-drive
findings"). A short task text ranked the applicable lessons in the top 10 candidates but below `TAU` (0.045). The
judged call warned on the right lesson; a call whose judge timed out answered `no_matching_evidence` with 0 warnings
and nothing else to read. Reproduced on production with `mode: deterministic` (0 warnings) and `mode: auto` (the
judge warned on the right lesson). D-257's `dropped_by_judge` lists only candidates above `TAU` and only on judged
results, so it does not cover this case.

## The change
1. **`unjudged` (additive).** When `judged` is false (any reason: judge disabled, not requested, unavailable, timeout,
   guard, budget, no_detach, all privacy-withheld), the response lists the best candidates it did not warn on as
   `unjudged: [{clue, title, why, source_project}]` and `unjudged_omitted`. Best `det_score` first, retrieval order
   breaking ties, at most 3; candidates with `det_score` 0 (only a distant vector match) are left out. `why` is a fixed
   sentence (kind, lists, score, threshold, reason); the title passes the same redaction as `dropped_by_judge`
   (`_safe_title`). Packed after the warnings with the D-257 packer (the field names are parameters now). After a judge
   call (`called`), the entries go through the same D-062 re-check as warnings. Judged results carry no `unjudged`.
2. **Previews skip a leading YAML frontmatter block of chunk 0** (`core/retrieval.py`, `preview_text`). Imported
   files keep their frontmatter in the body; previews often showed `---\ntitle: …`. Drilldown and stored data are
   unchanged.
3. The risk tool description, the protocol (R17 pause line, R18), the digest line, the skills and the `hlm` preflight
   line name `unjudged`. tools/list stays at 2998/3000 tokens (G-SURF).

## What is protected
- **Safety of the risk check:** a relevant lesson must reach the writer; a false-empty `no_matching_evidence` is the
  failure this change addresses.
- **The verdict contract:** `verdict ∈ {warn, no_matching_evidence}`, D-014, and G-LIVE-C (catch and false-warn rates
  measure `verdict`/`warnings`, which must not change for any input).
- **Visibility:** the new list must obey the same visibility as `warnings`: the caller's readable projects, device
  scope, the D-083 isolation policy, current items only, and the D-062 re-check after time passed outside a transaction.
- **Secrets:** no secret-shaped text in a title reaches the response unredacted.
- **Budget:** `warnings`, `omitted` and the budget errors are unchanged for every input; `budget.used` stays exact and
  ≤ the limit.
- **Preview correctness:** stripping must only remove a real frontmatter block, never body text, and must stay linear.

## Known, accepted for this release
- Privacy-withheld candidates (`device:*`, `policy.librarian=off`, co-owned by an ungranted project) can appear in
  `unjudged`: they are items the caller may read; the privacy gate governs what is sent to the LLM, and such items
  already appear as deterministic warnings at `TAU_STRICT`. (`dropped_by_judge` excludes them because "the judge did not
  warn" would be false for an item the judge never saw.)
- The Redactor's known gaps (BACKLOG: JSON-style and quoted multi-word values) apply to these titles as to
  `dropped_by_judge`.
- When nothing relevant exists, `unjudged` still lists up to 3 weak candidates; each says it is below the threshold
  and unjudged. That is noise by design, accepted for the safety goal.

## Severity rubric
- **HIGH:** an `unjudged` entry from an item the caller could not get as a warning (another project, another device
  scope, an isolated project, a closed or superseded item, one made invisible during the judge call); an unredacted
  secret shape in the response; any change of `verdict`, `warnings`, `omitted`, `judged` or a budget error for an
  existing input; `budget.used` above the limit; a preview that drops body text that is not frontmatter, or a regex
  with super-linear backtracking on chunk-sized input.
- **MEDIUM:** a wrong or misleading count (`unjudged_omitted`), an empty `unjudged: []` list, an entry duplicated in
  `warnings`, the preflight line misreporting the result, docs that contradict the code.
- **LOW:** wording, comments, test gaps without a reachable failure.

## Diff (src, tests, docs) since main
diff --git a/docs/protocol/HLMEMO-PROTOCOL.md b/docs/protocol/HLMEMO-PROTOCOL.md
index f327c3f..7767891 100644
--- a/docs/protocol/HLMEMO-PROTOCOL.md
+++ b/docs/protocol/HLMEMO-PROTOCOL.md
@@ -181,6 +181,8 @@ checks a write grant on `hlm-global` (`librarian/reserved.py:16`; `core/write_se
 owner's device was granted one for the D-234 import, and no later decision records its removal.
 
 **R17. Close each session once with `memory.call_the_day`** (fresh UUIDs for `request_id` and `session_id`).
+A long pause that may end the session closes it; work after the pause is a new session with a fresh
+`session_id`, closed again (test-drive 2026-10-08).
 `notes` = what changed, why, pointers (files, commits, D-ids, clues), then `## Open` bullets. `decisions` = one
 line each with its reason. Leave `lessons` empty, since it skips R15's schema; write every lesson with
 `memory.register_lesson`. Use `card_update` only to fix a card line your session made false: a minimal edit,
@@ -201,12 +203,14 @@ once per project (`E_SESSION_CLOSED`, `core/write_service.py:776-780`); card hea
 On `warn`, drill each lesson and state how you comply. `no_matching_evidence` is not a guarantee: the LLM judge can
 drop a relevant lesson (test-drive 2026-10-07). So a judged result also lists `dropped_by_judge`: the retrieval
 matches the judge did not warn on (D-257). Their `why` is a fixed retrieval note (the judge gives no reason for a
-non-match), so read the lesson itself and decide whether it applies. Before a deploy
+non-match), so read the lesson itself and decide whether it applies. A retrieval-only result (`judged: false`, for
+example a judge timeout) lists `unjudged` instead: the best retrieved lessons it did not warn on, with the same kind
+of fixed note (test-drive 2026-10-08); read them the same way. Before a deploy
 or a prod-data change, also run `memory.query` with `kinds: ["lesson"]` for the component, worded the way the
 lessons are written. `judged: false` means retrieval only. A warned lesson tagged `resolved`/`historical` is a reminder: say so.
 *Why:* lesson-backed checks are what prevent repeats (D-222). *Enforced by:* protocol only. The verdict
-semantics are server today (`server/tools/risk.py:60-63`; `core/risk_service.py:4-6,26-31`), and so is the
-`dropped_by_judge` list (`core/risk_service.py`, consults 114 and 115). Candidates are chosen by kind with no tag filter
+semantics are server today (`server/tools/risk.py:61-65`; `core/risk_service.py:6-7,29-33`), and so are the
+`dropped_by_judge` and `unjudged` lists (`core/risk_service.py`, consults 114 and 115). Candidates are chosen by kind with no tag filter
 (`db/risk_queries.py:31`).
 
 **R19. Make writes idempotent.** One `request_id` per logical write; a transport retry resends the identical
@@ -462,7 +466,7 @@ Read first: the injected brief, else memory.query your task (token_budget 3000).
 - Query each area before work there; previews are excerpts, so drill the top hits (memory.drilldown) first.
 - superseded:true is not current: follow superseded_by. memory.ask (when listed): open its handles to check quotes.
 - Before a deploy, migration, prod-data change, deletion, force-push or secret: memory.risk_check, and memory.query
-  kinds:[lesson] for the component. Read warnings and dropped_by_judge, say how you comply; no match is not safety.
+  kinds:[lesson] per component. Read warnings, dropped_by_judge, unjudged; say how you comply; no match is not safety.
 Write into project <slug> only (another slug or extra project_ids only if the owner asks). Memory is append-only.
 - Durable items: current facts, decisions + reasons, lessons, dated episodes. No transcript/tool-output dumps, guesses
   as fact, other projects' data or secret values (keys, tokens, passwords, DSNs with passwords); name where they live.
diff --git a/integrations/claude/skills/hlm-library/SKILL.md b/integrations/claude/skills/hlm-library/SKILL.md
index 0cbdf07..0cbc297 100644
--- a/integrations/claude/skills/hlm-library/SKILL.md
+++ b/integrations/claude/skills/hlm-library/SKILL.md
@@ -38,7 +38,7 @@ working path. macOS has no `timeout` command; do not wrap commands in it.
 - Per project: `memory_query` shows the card (is it stale?), and `librarian.pending_questions` the review backlog.
 - Recent session notes: the brief of each project, or `memory_query` with `kinds: ["session_note"]`.
 - Before any risky step, `memory_risk_check` **and** a `memory_query` with `kinds: ["lesson"]` for the component: the
-  risk judge can drop a relevant lesson (R18), so read both `warnings` and `dropped_by_judge`. On a warning, drill
+  risk judge can drop a relevant lesson (R18), so read `warnings`, `dropped_by_judge` and `unjudged`. On a warning, drill
   the lesson and say how you comply.
 
 ## 2. Librarian proposals: verify, correct, withdraw
diff --git a/integrations/claude/skills/hlmemo/SKILL.md b/integrations/claude/skills/hlmemo/SKILL.md
index fab2aa7..3ddf543 100644
--- a/integrations/claude/skills/hlmemo/SKILL.md
+++ b/integrations/claude/skills/hlmemo/SKILL.md
@@ -40,7 +40,8 @@ asks, and ask before writing anything.
 3. **Check risk** (R18) with `memory_risk_check` before a deploy, a migration, a prod-data change, a deletion, a
    force-push or anything touching secrets: lesson-backed checks are what prevent repeats.
 4. **Write as you go** (R5–R14), while the evidence is in front of you, with `updates` when your item makes
-   something you read outdated. **Close once** (R17) with `call_the_day`.
+   something you read outdated. **Close once** (R17) with `call_the_day`; work after a long pause that closed the
+   session continues under a fresh `session_id`.
 
 ## 3. The tools
 
@@ -68,13 +69,16 @@ MCP tools.
   It returns `answer`, `confidence`, `abstained`, `claims[]` (each with `support[]` of `{handle, quote}`),
   `primary[]` and `related[]`. Pass `project` every time; without it the server uses the device's default project.
   The quotes are verbatim but the answer is an LLM's, so open the primary handles with `memory_drilldown` before
-  you act on it. `abstained: true` means memory does not say; it is not a "no".
+  you act on it. `abstained: true` means memory does not say; it is not a "no". For a subagent's brief, ask for
+  an area's known open risks and lessons with their clues, and pass the checked ones on.
 - **`mcp__hlm__memory_risk_check`**, past lessons against a planned step:
   `{"project": "my-project", "task": "Run the migration that adds an index on events(created_at) in prod", "token_budget": 2000}`.
   `verdict: "warn"` lists `warnings[]` (`clue`, `title`, `why`, `source_project`): drill each one and say how you
   comply. A judged result also lists `dropped_by_judge[]` (same fields): retrieval matches the judge did not warn
   on, since the judge can drop a relevant lesson. Their `why` is a fixed retrieval note (the judge gives no reason
-  for a non-match): drill the clue, read the lesson and decide whether it applies.
+  for a non-match): drill the clue, read the lesson and decide whether it applies. A retrieval-only result
+  (`judged: false`, e.g. a judge timeout) lists `unjudged[]` instead, the best retrieved lessons it did not warn on;
+  read them the same way.
   `no_matching_evidence` means no stored lesson matched, not that the step is safe, so before a deploy or prod-data
   change also run `memory_query` with `kinds: ["lesson"]` for the component; `judged: false` means retrieval only,
   with no LLM judge. A warned lesson tagged `resolved` or `historical` is a reminder: say so
diff --git a/integrations/claude/skills/hlmemo/references/HLMEMO-PROTOCOL.md b/integrations/claude/skills/hlmemo/references/HLMEMO-PROTOCOL.md
index e301911..cd914b5 100644
--- a/integrations/claude/skills/hlmemo/references/HLMEMO-PROTOCOL.md
+++ b/integrations/claude/skills/hlmemo/references/HLMEMO-PROTOCOL.md
@@ -1,4 +1,4 @@
-> Copy of `docs/protocol/HLMEMO-PROTOCOL.md` (HLMemo repository, protocol v1 draft), copied 2026-10-07, source sha256 e75d37190d0d909bf1fb2a2aa7815f4656f4845644671d8333861f7e02ab59db. If the two differ, the source file wins.
+> Copy of `docs/protocol/HLMEMO-PROTOCOL.md` (HLMemo repository, protocol v1 draft), copied 2026-10-08, source sha256 2644a17a261672868fa2f1035f58c88e8948748971d618774617c3c33628ca84. If the two differ, the source file wins.
 
 # HLMemo protocol v1: rules for project writers
 
@@ -183,6 +183,8 @@ checks a write grant on `hlm-global` (`librarian/reserved.py:16`; `core/write_se
 owner's device was granted one for the D-234 import, and no later decision records its removal.
 
 **R17. Close each session once with `memory.call_the_day`** (fresh UUIDs for `request_id` and `session_id`).
+A long pause that may end the session closes it; work after the pause is a new session with a fresh
+`session_id`, closed again (test-drive 2026-10-08).
 `notes` = what changed, why, pointers (files, commits, D-ids, clues), then `## Open` bullets. `decisions` = one
 line each with its reason. Leave `lessons` empty, since it skips R15's schema; write every lesson with
 `memory.register_lesson`. Use `card_update` only to fix a card line your session made false: a minimal edit,
@@ -203,12 +205,14 @@ once per project (`E_SESSION_CLOSED`, `core/write_service.py:776-780`); card hea
 On `warn`, drill each lesson and state how you comply. `no_matching_evidence` is not a guarantee: the LLM judge can
 drop a relevant lesson (test-drive 2026-10-07). So a judged result also lists `dropped_by_judge`: the retrieval
 matches the judge did not warn on (D-257). Their `why` is a fixed retrieval note (the judge gives no reason for a
-non-match), so read the lesson itself and decide whether it applies. Before a deploy
+non-match), so read the lesson itself and decide whether it applies. A retrieval-only result (`judged: false`, for
+example a judge timeout) lists `unjudged` instead: the best retrieved lessons it did not warn on, with the same kind
+of fixed note (test-drive 2026-10-08); read them the same way. Before a deploy
 or a prod-data change, also run `memory.query` with `kinds: ["lesson"]` for the component, worded the way the
 lessons are written. `judged: false` means retrieval only. A warned lesson tagged `resolved`/`historical` is a reminder: say so.
 *Why:* lesson-backed checks are what prevent repeats (D-222). *Enforced by:* protocol only. The verdict
-semantics are server today (`server/tools/risk.py:60-63`; `core/risk_service.py:4-6,26-31`), and so is the
-`dropped_by_judge` list (`core/risk_service.py`, consults 114 and 115). Candidates are chosen by kind with no tag filter
+semantics are server today (`server/tools/risk.py:61-65`; `core/risk_service.py:6-7,29-33`), and so are the
+`dropped_by_judge` and `unjudged` lists (`core/risk_service.py`, consults 114 and 115). Candidates are chosen by kind with no tag filter
 (`db/risk_queries.py:31`).
 
 **R19. Make writes idempotent.** One `request_id` per logical write; a transport retry resends the identical
@@ -464,7 +468,7 @@ Read first: the injected brief, else memory.query your task (token_budget 3000).
 - Query each area before work there; previews are excerpts, so drill the top hits (memory.drilldown) first.
 - superseded:true is not current: follow superseded_by. memory.ask (when listed): open its handles to check quotes.
 - Before a deploy, migration, prod-data change, deletion, force-push or secret: memory.risk_check, and memory.query
-  kinds:[lesson] for the component. Read warnings and dropped_by_judge, say how you comply; no match is not safety.
+  kinds:[lesson] per component. Read warnings, dropped_by_judge, unjudged; say how you comply; no match is not safety.
 Write into project <slug> only (another slug or extra project_ids only if the owner asks). Memory is append-only.
 - Durable items: current facts, decisions + reasons, lessons, dated episodes. No transcript/tool-output dumps, guesses
   as fact, other projects' data or secret values (keys, tokens, passwords, DSNs with passwords); name where they live.
diff --git a/src/hlmemo/brief/protocol_digest.txt b/src/hlmemo/brief/protocol_digest.txt
index 2d724e7..095d220 100644
--- a/src/hlmemo/brief/protocol_digest.txt
+++ b/src/hlmemo/brief/protocol_digest.txt
@@ -3,7 +3,7 @@ Read first: the injected brief, else memory.query your task (token_budget 3000).
 - Query each area before work there; previews are excerpts, so drill the top hits (memory.drilldown) first.
 - superseded:true is not current: follow superseded_by. memory.ask (when listed): open its handles to check quotes.
 - Before a deploy, migration, prod-data change, deletion, force-push or secret: memory.risk_check, and memory.query
-  kinds:[lesson] for the component. Read warnings and dropped_by_judge, say how you comply; no match is not safety.
+  kinds:[lesson] per component. Read warnings, dropped_by_judge, unjudged; say how you comply; no match is not safety.
 Write into project <slug> only (another slug or extra project_ids only if the owner asks). Memory is append-only.
 - Durable items: current facts, decisions + reasons, lessons, dated episodes. No transcript/tool-output dumps, guesses
   as fact, other projects' data or secret values (keys, tokens, passwords, DSNs with passwords); name where they live.
diff --git a/src/hlmemo/cli/preflight.py b/src/hlmemo/cli/preflight.py
index 7d91c02..08c8e66 100644
--- a/src/hlmemo/cli/preflight.py
+++ b/src/hlmemo/cli/preflight.py
@@ -171,6 +171,19 @@ def _dropped_note(listed: int, more: int) -> str:
     return note + ": read them and decide whether they apply before acting."
 
 
+def _unjudged_note(listed: int, more: int) -> str:
+    """Retrieval-only: the best retrieved lessons it did not warn on (2026-10-08), listed vs cut."""
+    if not listed:
+        return (
+            f"Retrieval found {more} more lesson(s) that no judge checked and that did not fit the budget: "
+            "raise token_budget and check again before acting."
+        )
+    note = f"unjudged in the hlmemo-risk block lists {listed} retrieved lesson(s) no judge checked"
+    if more:
+        note += f" ({more} more did not fit the budget)"
+    return note + ": read them and decide whether they apply before acting."
+
+
 def risk_line(risk: dict[str, Any] | None, risk_error: str | None) -> str | None:
     """The wrapper's own (trusted) one-line summary of memory.risk_check; None when not run."""
     if risk_error is not None:
@@ -184,15 +197,22 @@ def risk_line(risk: dict[str, Any] | None, risk_error: str | None) -> str | None
         how = f"RETRIEVAL ONLY, not judged by the librarian LLM ({reason})"
     n = _count(risk.get("warnings")) + _count(risk.get("omitted"))
     listed, more = _count(risk.get("dropped_by_judge")), _count(risk.get("dropped_omitted"))
+    u_listed, u_more = _count(risk.get("unjudged")), _count(risk.get("unjudged_omitted"))
+    extra = _dropped_note(listed, more) if listed or more else ""
+    if u_listed or u_more:
+        extra = (extra + " " if extra else "") + _unjudged_note(u_listed, u_more)
     if risk.get("verdict") == "warn" and n:
         line = (
             f"memory.risk_check flagged {n} past lesson(s) for this task ({how}; see the hlmemo-risk "
             "block): check whether they apply before acting and drill their clues if unsure."
         )
-        return line + (f" {_dropped_note(listed, more)}" if listed or more else "")
+        return line + (f" {extra}" if extra else "")
     if listed or more:  # the judge matched none, yet retrieval found some (consults 115, 116)
         head = f"memory.risk_check: the librarian judge matched no past lesson ({how})."
-        return f"{head} {_dropped_note(listed, more)}"
+        return f"{head} {extra}"
+    if u_listed or u_more:  # retrieval-only: nothing passed the warn threshold, yet some came close
+        head = f"memory.risk_check: no past lesson passed the retrieval warn threshold ({how})."
+        return f"{head} {extra}"
     return (
         f"memory.risk_check found no matching past lesson for this task ({how}; not a guarantee of safety)."
     )
diff --git a/src/hlmemo/core/retrieval.py b/src/hlmemo/core/retrieval.py
index 164ea42..b9d8d1f 100644
--- a/src/hlmemo/core/retrieval.py
+++ b/src/hlmemo/core/retrieval.py
@@ -298,6 +298,26 @@ def term_matches(text: str, terms: Sequence[str]) -> list[tuple[int, int]]:
     return out
 
 
+#: a YAML frontmatter block opening an item: ``---``, then key, indented, list or blank lines, ``---``
+_FRONTMATTER_RE = re.compile(
+    r"\A---[ \t]*\n(?:[A-Za-z_][\w-]*:.*\n|[ \t]+\S.*\n|-[ \t].*\n|[ \t]*\n){1,40}?---[ \t]*(?:\n|\Z)"
+)
+
+
+def preview_text(text: str, ordinal: int) -> str:
+    """The text a preview is cut from: chunk 0 without a leading YAML frontmatter block (second test
+    drive, 2026-10-08). Imported files keep their frontmatter in the body, and the query-centred
+    window often landed on its ``title:`` line, which repeats the hit's title. Only the preview
+    skips it; drilldown shows the stored text. A chunk that is nothing but the block keeps it."""
+    if ordinal != 0:
+        return text
+    m = _FRONTMATTER_RE.match(text)
+    if m is None:
+        return text
+    rest = text[m.end() :].lstrip("\n")
+    return rest if rest.strip() else text
+
+
 def query_preview(meter: Meter, text: str, terms: Sequence[str], max_tokens: int) -> str:
     """D-055 query-centred preview: the ``max_tokens``-token (o200k) window of ``text`` covering the
     most distinct query terms (then the most occurrences, then the earliest start), starting
@@ -343,7 +363,7 @@ def render_hit(meter: Meter, f: Fused, preview_tok: int, terms: Sequence[str] =
         "clue": encode_clue(row.version_id, row.ordinal),
         "kind": row.kind,
         "title": row.title,
-        "preview": query_preview(meter, row.text, terms, preview_tok),
+        "preview": query_preview(meter, preview_text(row.text, row.ordinal), terms, preview_tok),
         "score": round(f.score, SCORE_DIGITS),
         "valid_from": fmt_ts(row.valid_from),
         "tags": list(row.tags),
diff --git a/src/hlmemo/core/risk_service.py b/src/hlmemo/core/risk_service.py
index 3d169a2..4c62195 100644
--- a/src/hlmemo/core/risk_service.py
+++ b/src/hlmemo/core/risk_service.py
@@ -1,8 +1,8 @@
 """``memory.risk_check`` (PHASE2-4-ROADMAP W2d; report D.3 #7, D.4(b); D-014, D-062, D-067).
 
 ``risk_check(conn, ctx, args, deps=, judge=, detach=, reconnect=)`` → ``{project, verdict, judged,
-judge, reason?, warnings, omitted, dropped_by_judge?, dropped_omitted?, candidates_considered,
-guard_dropped?, budget}``;
+judge, reason?, warnings, omitted, dropped_by_judge?, dropped_omitted?, unjudged?, unjudged_omitted?,
+candidates_considered, guard_dropped?, budget}``;
 ``verdict ∈ {"warn", "no_matching_evidence"}``. Per D-014 it never says "no risk": the absence of
 a warning only means no stored lesson matched.
 
@@ -41,6 +41,14 @@ a warning only means no stored lesson matched.
    writer now sees it and decides. ``verdict``, ``warnings`` and ``judged`` keep their meaning, so
    the G-LIVE-C rates are unchanged. Privacy-withheld candidates are never in this list (the judge
    never saw them; they warn at ``TAU_STRICT``). Retrieval-only results carry neither field.
+   **Unjudged** (second test drive, 2026-10-08): a retrieval-only result (``judged:false``, any
+   reason) also lists, as ``unjudged``, the best candidates it did not warn on, best ``det_score``
+   first, at most ``MAX_UNJUDGED``; a candidate with ``det_score`` 0 (only a distant vector match) is
+   left out. A short task text ranks the applicable lesson in the top ``TOP_K`` but below ``TAU``, so
+   a judge timeout used to answer ``no_matching_evidence`` with nothing to read while the judged
+   call of the same task warned on it. Entries have the ``dropped_by_judge`` shape: a deterministic
+   ``why`` (kind, lists, score, the reason) and the redacted title. ``verdict``, ``warnings`` and
+   ``judged`` keep their meaning. Judged results carry neither ``unjudged`` field.
 4. **D-062**: the judge never runs inside a transaction. Over the API the request transaction
    (device FOR SHARE) is committed and its connection returned (``detach``) before the judge;
    afterwards ONE short transaction on a fresh connection re-checks the device (revoked / expired /
@@ -56,6 +64,7 @@ is added in the room left: ``dropped_by_judge`` (a best-first prefix) and ``drop
 entries that did not fit, its own counter). When not even one entry fits, only ``dropped_omitted``
 is added (no list): the caller learns that a larger budget would show N more matches. When even
 that counter does not fit, both fields are left out (the one case a caller cannot tell).
+``unjudged`` / ``unjudged_omitted`` pack the same way on a retrieval-only result.
 """
 
 from __future__ import annotations
@@ -103,6 +112,9 @@ LIST_LIMIT = 50  # per RRF list over the lesson universe
 MATCH_CHUNKS = 3  # matching chunks per candidate handed to the judge (its text window)
 MAX_WARNINGS = 3
 MAX_DROPPED = 3  # candidates above TAU that the judge did not warn on (``dropped_by_judge``)
+MAX_UNJUDGED = 3  # retrieval-only: the best candidates not warned on (``unjudged``)
+DROPPED_FIELDS = ("dropped_by_judge", "dropped_omitted")
+UNJUDGED_FIELDS = ("unjudged", "unjudged_omitted")
 _REDACTOR = Redactor()  # the librarian's redaction (secrets only; email/phone stay as written)
 WHY_MAX = rj.WHY_MAX
 
@@ -272,6 +284,14 @@ def _dropped(c: RiskCandidate) -> dict[str, Any]:
     return {"clue": c.clue, "title": _safe_title(c.title), "why": why[:WHY_MAX], "source_project": c.project}
 
 
+def _unjudged(c: RiskCandidate, reason: str) -> dict[str, Any]:
+    why = (
+        f"Retrieval found this {c.kind} ({c.lists} lists, score {c.det_score:.3f}; a retrieval-only warning "
+        f"needs {TAU:.3f}) and no judge checked it ({reason}). Drill the clue and decide whether it applies."
+    )
+    return {"clue": c.clue, "title": _safe_title(c.title), "why": why[:WHY_MAX], "source_project": c.project}
+
+
 def _det_why(c: RiskCandidate, reason: str) -> str:
     return (
         f"Retrieval-only match on this {c.kind} ({c.lists} lists, score {c.det_score:.3f}); "
@@ -292,7 +312,11 @@ def _pack(
     budget: int,
     warnings: list[dict[str, Any]],
     dropped: list[dict[str, Any]] | None = None,
+    *,
+    names: tuple[str, str] = DROPPED_FIELDS,
 ) -> dict[str, Any]:
+    """``dropped`` is the list packed after the warnings: ``dropped_by_judge`` on a judged result,
+    ``unjudged`` (``names=UNJUDGED_FIELDS``) on a retrieval-only one."""
     meter = deps.meter
     prefix = [0]
     base = meter.settle(envelope, budget)
@@ -308,27 +332,33 @@ def _pack(
     except BudgetError as exc:
         raise ToolError(exc.code, str(exc), **exc.details) from exc
     if dropped and envelope["omitted"] == 0:
-        return _pack_dropped(meter, envelope, budget, dropped)
+        return _pack_dropped(meter, envelope, budget, dropped, names)
     return envelope
 
 
 def _pack_dropped(
-    meter: Any, envelope: dict[str, Any], budget: int, dropped: list[dict[str, Any]]
+    meter: Any,
+    envelope: dict[str, Any],
+    budget: int,
+    dropped: list[dict[str, Any]],
+    names: tuple[str, str] = DROPPED_FIELDS,
 ) -> dict[str, Any]:
-    """Adds ``dropped_by_judge`` / ``dropped_omitted`` only in the room the packed warnings left,
-    on a copy. When not even one entry fits, only the counter is added (``dropped_omitted: N``, no
-    list), so a caller can tell the judge left out N matches that need a larger budget (review 116).
-    Edge: when even the counter does not fit, the envelope stays exactly as the warnings left it."""
+    """Adds the list (``names[0]``: ``dropped_by_judge`` or ``unjudged``) and its counter
+    (``names[1]``) only in the room the packed warnings left, on a copy. When not even one entry
+    fits, only the counter is added (e.g. ``dropped_omitted: N``, no list), so a caller can tell N
+    matches need a larger budget (review 116). Edge: when even the counter does not fit, the
+    envelope stays exactly as the warnings left it."""
+    key, more = names
     trial = dict(envelope)
-    trial["dropped_by_judge"] = []
-    trial["dropped_omitted"] = len(dropped)
+    trial[key] = []
+    trial[more] = len(dropped)
     prefix = [0]
     for d in dropped:
         prefix.append(prefix[-1] + meter.count(d) + 1)
 
     def apply(n: int) -> None:
-        trial["dropped_by_judge"] = dropped[:n]
-        trial["dropped_omitted"] = len(dropped) - n
+        trial[key] = dropped[:n]
+        trial[more] = len(dropped) - n
 
     try:
         base = meter.settle(trial, budget)
@@ -338,7 +368,7 @@ def _pack_dropped(
     if n > 0:
         return trial
     counter_only = dict(envelope)
-    counter_only["dropped_omitted"] = len(dropped)
+    counter_only[more] = len(dropped)
     if meter.settle(counter_only, budget) <= budget:
         return counter_only
     meter.settle(envelope, budget)  # the envelope as the warnings left it, budget block exact
@@ -434,6 +464,7 @@ async def risk_check(
     by_vid = {c.version_id: c for c in cands}
     warned: list[tuple[int, dict[str, Any]]]
     dropped: list[tuple[int, dict[str, Any]]] = []
+    unjudged: list[tuple[int, dict[str, Any]]] = []  # retrieval-only: the best candidates not warned on
     if called and res.judged:
         judged = True
         warned = [(vid, _warning(by_vid[vid], why or "Applies to this task.")) for vid, why in res.matches]
@@ -450,11 +481,13 @@ async def risk_check(
         dropped = [(c.version_id, _dropped(c)) for c in passed[:MAX_DROPPED]]
     elif all_withheld:  # nothing could be sent: the privacy-withheld rule applies to every candidate
         warned = _det(cands, "withheld by the privacy policy", TAU_STRICT)
+        unjudged = _unjudged_list(cands, warned, "withheld by the privacy policy")
     else:
         warned = _det(cands, status.replace("_", " "), TAU)
+        unjudged = _unjudged_list(cands, warned, status.replace("_", " "))
 
     if called:  # time passed without a transaction: authority and visibility are re-checked
-        shown = [v for v, _ in warned] + [v for v, _ in dropped]
+        shown = [v for v, _ in warned] + [v for v, _ in dropped] + [v for v, _ in unjudged]
         if released:
             assert reconnect is not None
             async with reconnect() as fresh_conn:
@@ -463,6 +496,7 @@ async def risk_check(
             visible = await _recheck(conn, ctx, request.project, shown)
         warned = [(v, w) for v, w in warned if v in visible]
         dropped = [(v, d) for v, d in dropped if v in visible]
+        unjudged = [(v, u) for v, u in unjudged if v in visible]
 
     envelope: dict[str, Any] = {
         "project": project.slug,
@@ -477,7 +511,10 @@ async def risk_check(
         envelope["reason"] = "withheld" if all_withheld else status
     if guard_dropped:
         envelope["guard_dropped"] = guard_dropped
-    return _pack(deps, envelope, budget, [w for _, w in warned], [d for _, d in dropped] if judged else None)
+    warnings = [w for _, w in warned]
+    if judged:
+        return _pack(deps, envelope, budget, warnings, [d for _, d in dropped])
+    return _pack(deps, envelope, budget, warnings, [u for _, u in unjudged], names=UNJUDGED_FIELDS)
 
 
 def _det(cands: list[RiskCandidate], reason: str, tau: float) -> list[tuple[int, dict[str, Any]]]:
@@ -485,9 +522,23 @@ def _det(cands: list[RiskCandidate], reason: str, tau: float) -> list[tuple[int,
     return [(c.version_id, _warning(c, _det_why(c, reason))) for c in hits[:MAX_WARNINGS]]
 
 
+def _unjudged_list(
+    cands: list[RiskCandidate], warned: list[tuple[int, dict[str, Any]]], reason: str
+) -> list[tuple[int, dict[str, Any]]]:
+    """The best retrieved candidates a retrieval-only answer did not warn on (test drive
+    2026-10-08): best ``det_score`` first, retrieval order breaking ties; a candidate that
+    qualifies in no list (``det_score`` 0: only a distant vector match) is left out."""
+    shown = {v for v, _ in warned}
+    rest = sorted(
+        (c for c in cands if c.version_id not in shown and c.det_score > 0), key=lambda c: -c.det_score
+    )
+    return [(c.version_id, _unjudged(c, reason)) for c in rest[:MAX_UNJUDGED]]
+
+
 __all__ = [
     "MATCH_CHUNKS",
     "MAX_DROPPED",
+    "MAX_UNJUDGED",
     "TAU",
     "TAU_STRICT",
     "TOOL",
diff --git a/src/hlmemo/server/tools/risk.py b/src/hlmemo/server/tools/risk.py
index 3a57a3c..9a2022a 100644
--- a/src/hlmemo/server/tools/risk.py
+++ b/src/hlmemo/server/tools/risk.py
@@ -59,8 +59,8 @@ REGISTER_LESSON_INPUT = _schema(
 )
 
 RISK_CHECK_DESCRIPTION = (
-    "Check a planned task against past lessons you can read (all granted projects). warn lists "
-    "warnings to heed, and dropped_by_judge too; "
+    "Check a planned task against past lessons you can read (all granted projects). Heed warnings, "
+    "dropped_by_judge and unjudged; "
     "no_matching_evidence is not a safety guarantee; judged=false: retrieval only."
 )
 REGISTER_LESSON_DESCRIPTION = (
diff --git a/tests/integration/test_risk_unjudged.py b/tests/integration/test_risk_unjudged.py
new file mode 100644
index 0000000..c433b5c
--- /dev/null
+++ b/tests/integration/test_risk_unjudged.py
@@ -0,0 +1,160 @@
+"""``unjudged`` (second test drive, 2026-10-08): a retrieval-only risk check also lists the best
+candidates it did not warn on, so a judge timeout no longer answers with nothing to read.
+
+The shape these tests pin: a short task ranked the applicable lesson in the top ``TOP_K`` but below
+``TAU``; the judged call of the same task warned on it, the timed-out call returned
+``no_matching_evidence`` with an empty result. ``verdict``, ``warnings`` and ``judged`` keep their
+meaning (G-LIVE-C is unchanged); only the new list is added, from the same visible candidate set,
+re-checked after a judge call, packed after the warnings.
+"""
+
+from __future__ import annotations
+
+from typing import Any
+
+import pytest
+
+from hlmemo.core import risk_service as rs
+from hlmemo.librarian import risk_judge as rj
+from tests.integration._librarian_fixtures import ScriptedLLM, stub_chain
+from tests.integration._risk_fixtures import load_cases, seed_world
+from tests.integration.test_w2d_risk import MAIN, judge_settings, run_case
+
+pytestmark = pytest.mark.integration
+
+NONE = {"verdict": "none", "matches": []}
+UNCITED = {"verdict": "warn", "matches": [{"id": "R99", "why": "invented"}]}  # the D-067 guard fails it
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
+    from hlmemo.core.read_service import default_read_deps
+
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
+async def _cands(connect, world, deps, task: str) -> list[rs.RiskCandidate]:  # noqa: ANN001
+    async with await connect() as conn:
+        cands, _ = await rs.candidates(conn, world.ctx_reader, world.projects[MAIN], task, deps)
+        await conn.commit()
+    return cands
+
+
+def _expected(cands: list[rs.RiskCandidate], warned: set[str]) -> list[str]:
+    rest = [c for c in cands if c.clue not in warned and c.det_score > 0]
+    return [c.clue for c in sorted(rest, key=lambda c: -c.det_score)[: rs.MAX_UNJUDGED]]
+
+
+async def test_retrieval_only_lists_the_best_unwarned_candidates(connect, world, deps) -> None:  # noqa: ANN001
+    """Every case, judge disabled: the list is exactly the best unwarned candidates, never a warned
+    one; the test-drive shape (nothing warned, the gold lesson listed) occurs in the fixtures."""
+    shape = any_missed = 0
+    for case in load_cases():
+        out, _, _ = await run_case(connect, world, deps, case["task"], judge=None)
+        assert out["judged"] is False and out["reason"] == rj.DISABLED, out
+        warned = {w["clue"] for w in out["warnings"]}
+        listed = out.get("unjudged", [])
+        assert [u["clue"] for u in listed] == _expected(
+            await _cands(connect, world, deps, case["task"]), warned
+        )
+        assert not warned & {u["clue"] for u in listed}, case["id"]
+        assert all(set(u) == KEYS and "no judge checked it (disabled)" in u["why"] for u in listed), listed
+        assert out.get("unjudged_omitted", 0) == 0, out  # BUDGET 4000 holds every entry
+        missed = set(case["gold"]) - {world.lesson_of_clue(c) for c in warned}
+        any_missed += bool(missed)
+        if missed & {world.lesson_of_clue(u["clue"]) for u in listed}:
+            shape += 1
+    print(f"retrieval-only missed a gold lesson in {any_missed} cases; unjudged lists it in {shape}")
+    assert shape, "no missed gold lesson is listed: the test proves nothing"
+
+
+async def test_deterministic_mode_and_judge_failures_list_unjudged(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
+    task = _case("P01")
+    out, _, _ = await run_case(connect, world, deps, task, mode="deterministic", judge=None)
+    assert out["judged"] is False and out["reason"] == rj.NOT_REQUESTED and out["unjudged"], out
+    assert all("(not requested)" in u["why"] for u in out["unjudged"]), out
+    llm = ScriptedLLM(default=UNCITED)
+    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
+    try:
+        failed, _, _ = await run_case(connect, world, deps, task, judge=judge)
+    finally:
+        await judge.aclose()
+    assert failed["judged"] is False and failed["reason"] == rj.GUARD, failed
+    assert [u["clue"] for u in failed["unjudged"]] == [u["clue"] for u in out["unjudged"]], (failed, out)
+    assert [w["clue"] for w in failed["warnings"]] == [w["clue"] for w in out["warnings"]], (failed, out)
+
+
+async def test_judged_results_carry_no_unjudged(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
+    llm = ScriptedLLM(default=NONE)
+    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
+    try:
+        for case_id in ("P01", "P02"):
+            out, _, _ = await run_case(connect, world, deps, _case(case_id), judge=judge)
+            assert out["judged"] is True and "unjudged" not in out and "unjudged_omitted" not in out, out
+    finally:
+        await judge.aclose()
+
+
+async def test_ungranted_and_invisible_lessons_never_appear(connect, world, deps) -> None:  # noqa: ANN001
+    """L50: a project without a grant; L22: another device class (class:work). Never candidates."""
+    invisible = {f"v{world.version_of_lesson['L50']}", f"v{world.version_of_lesson['L22']}"}
+    for case in load_cases():
+        out, _, _ = await run_case(connect, world, deps, case["task"], judge=None)
+        listed = out.get("unjudged", [])
+        assert not invisible & {u["clue"] for u in listed}, case["id"]
+        assert all(u["source_project"] != "rk-secret" for u in listed), case["id"]
+
+
+async def test_unjudged_entries_are_rechecked_after_a_failed_judge_call(connect, world, deps, db_dsn) -> None:  # noqa: ANN001
+    """D-062: the judge was called (and failed), so time passed without a transaction; a grant
+    removed meanwhile takes that project's lessons out of warnings and unjudged alike."""
+    shell = world.projects["rk-shell"]
+    rid = world.ctx_reader.device_id
+    shell_clues = {
+        c.clue for c in await _cands(connect, world, deps, _case("P02")) if c.project == "rk-shell"
+    }
+    assert shell_clues, "P02 retrieves no rk-shell lesson: the test would prove nothing"
+
+    async def set_grant(present: bool) -> None:
+        value = "NULL" if present else "now()"
+        async with await connect() as c:
+            await c.execute(
+                f"UPDATE device_project_grants SET revoked_at = {value}"
+                " WHERE device_id = %s AND project_id = %s",
+                (rid, shell),
+            )
+            await c.commit()
+
+    async def drop_shell(_body: dict[str, Any]) -> None:
+        await set_grant(False)
+
+    def shown(out: dict[str, Any]) -> set[str]:
+        return {w["clue"] for w in out["warnings"]} | {u["clue"] for u in out.get("unjudged", [])}
+
+    llm = ScriptedLLM(default=UNCITED)
+    judge = rj.RiskJudge(judge_settings(db_dsn), chain=stub_chain(fallback=False), transport=llm.transport)
+    try:
+        out, _, _ = await run_case(connect, world, deps, _case("P02"), judge=judge)
+        assert out["reason"] == rj.GUARD and shown(out) & shell_clues, out  # control: listed
+        llm.on_request = drop_shell
+        out, _, _ = await run_case(connect, world, deps, _case("P02"), judge=judge)
+        assert out["judged"] is False and out["reason"] == rj.GUARD, out
+        assert not shown(out) & shell_clues, out
+    finally:
+        await set_grant(True)
+        await judge.aclose()
diff --git a/tests/unit/test_cli_preflight_risk.py b/tests/unit/test_cli_preflight_risk.py
index 420358e..05c9f37 100644
--- a/tests/unit/test_cli_preflight_risk.py
+++ b/tests/unit/test_cli_preflight_risk.py
@@ -293,3 +293,37 @@ def test_risk_wait_is_bounded_in_total(tmp_path: Path, monkeypatch: pytest.Monke
     elapsed = time.monotonic() - t0
     assert out.ok and out.risk_error == "timeout"
     assert elapsed < 2.0, elapsed  # total cap 1.0 s, not 0.9 + 1.5 s
+
+
+def test_retrieval_only_no_match_with_unjudged_lessons_names_them() -> None:
+    """2026-10-08: a judge timeout used to read as "no matching lesson" while retrieval had them."""
+    risk = {
+        **RISK_WARN,
+        "verdict": "no_matching_evidence",
+        "judged": False,
+        "judge": "retrieval_only",
+        "reason": "timeout",
+        "warnings": [],
+        "omitted": 0,
+        "unjudged": _DROPPED * 2,
+        "unjudged_omitted": 1,
+    }
+    p = build_prompt(query_ok(), project="p", device="d", queried_at="t", task="x", risk=risk)
+    assert "found no matching past lesson" not in p
+    assert "no past lesson passed the retrieval warn threshold (RETRIEVAL ONLY" in p
+    assert "lists 2 retrieved lesson(s) no judge checked (1 more did not fit the budget)" in p
+    only_counter = {k: v for k, v in risk.items() if k != "unjudged"}
+    p = build_prompt(query_ok(), project="p", device="d", queried_at="t", task="x", risk=only_counter)
+    assert "Retrieval found 1 more lesson(s) that no judge checked and that did not fit the budget" in p
+
+
+def test_warn_line_mentions_unjudged_lessons_too() -> None:
+    risk = {
+        **RISK_WARN,
+        "judged": False,
+        "judge": "retrieval_only",
+        "reason": "timeout",
+        "unjudged": _DROPPED,
+    }
+    p = build_prompt(query_ok(), project="p", device="d", queried_at="t", task="x", risk=risk)
+    assert "flagged" in p and "lists 1 retrieved lesson(s) no judge checked: read them" in p
diff --git a/tests/unit/test_d055_retrieval.py b/tests/unit/test_d055_retrieval.py
index f27f0d3..a926c18 100644
--- a/tests/unit/test_d055_retrieval.py
+++ b/tests/unit/test_d055_retrieval.py
@@ -21,7 +21,9 @@ from hlmemo.core.retrieval import (
     Fused,
     dedupe_and_order,
     pack_query,
+    preview_text,
     query_preview,
+    render_hit,
     rrf_fuse,
     select_terms,
     split_terms,
@@ -595,3 +597,40 @@ def test_install_id_loser_rereads_the_winner(tmp_path: Path, monkeypatch: pytest
 
     monkeypatch.setattr(client_config, "_publish_install_id", lose)
     assert client_config.install_id() == winner
+
+
+# --------------------------------------------------------------------------- frontmatter (2026-10-08)
+_FM = (
+    '---\ntitle: "INFRA · Start API containers only through the swap script"\ndate: 2026-08-09T13:00:00\n'
+    "tags: [infra, traefik@3, active]\nsource_path: deploy/zero-downtime-swap.sh\n---\n"
+)
+_BODY = "## Mistake\nThe serving container lacked the healthcheck label, so the swap script bootstrapped."
+
+
+@pytest.mark.parametrize(
+    ("text", "ordinal", "expected"),
+    [
+        (_FM + _BODY, 0, _BODY),
+        ("---\ntitle: t\ntags:\n  - a\n  - b\n\n---\nbody text", 0, "body text"),
+        (_FM + _BODY, 1, _FM + _BODY),  # only chunk 0 opens the item
+        ("---\nA horizontal rule, then prose\n---\nmore", 0, "---\nA horizontal rule, then prose\n---\nmore"),
+        ("---\ntitle: t\n---\n", 0, "---\ntitle: t\n---\n"),  # nothing but the block: kept
+        ("---\ntitle: t\nno closing line", 0, "---\ntitle: t\nno closing line"),
+        (_BODY, 0, _BODY),
+    ],
+)
+def test_preview_text_skips_a_leading_yaml_block_of_chunk_zero(
+    text: str, ordinal: int, expected: str
+) -> None:
+    assert preview_text(text, ordinal) == expected
+
+
+def test_a_hit_preview_starts_after_the_frontmatter(meter: Meter) -> None:
+    text = _FM + _BODY + " " + FILLER
+    f = Fused(1, 1, 1, 0.5)
+    f.row = _row(1, 1, text)
+    terms = ["swap", "script"]
+    assert "source_path" in query_preview(meter, text, terms, PREVIEW_TOK)  # the shape the test drive saw
+    hit = render_hit(meter, f, PREVIEW_TOK, terms)
+    assert "swap script" in hit["preview"], hit["preview"]
+    assert "title:" not in hit["preview"] and "source_path" not in hit["preview"], hit["preview"]
diff --git a/tests/unit/test_risk_dropped_units.py b/tests/unit/test_risk_dropped_units.py
index 59420c7..b03d660 100644
--- a/tests/unit/test_risk_dropped_units.py
+++ b/tests/unit/test_risk_dropped_units.py
@@ -1,5 +1,5 @@
-"""Units of ``dropped_by_judge`` (consults 114, 115): the entry shape, title redaction, and packing
-that leaves the warnings exactly as the pre-change code packed them."""
+"""Units of ``dropped_by_judge`` (consults 114, 115) and ``unjudged`` (2026-10-08): the entry shape,
+title redaction, and packing that leaves the warnings exactly as the pre-change code packed them."""
 
 from __future__ import annotations
 
@@ -177,3 +177,56 @@ def test_pack_without_a_dropped_list_adds_no_field() -> None:
     for dropped in (None, []):
         out = rs._pack(_Deps(), _env(), 1000, [_item(1, 10)], dropped)
         assert "dropped_by_judge" not in out and "dropped_omitted" not in out
+
+
+# --------------------------------------------------------------------------- unjudged (2026-10-08)
+def test_unjudged_entry_shape_and_deterministic_why() -> None:
+    u = rs._unjudged(_cand("Bump app.js?v= on every frontend change", score=0.031), "timeout")
+    assert set(u) == {"clue", "title", "why", "source_project"} and u["clue"] == "v812"
+    assert u["why"].startswith(
+        "Retrieval found this lesson (LTV lists, score 0.031; a retrieval-only warning"
+    )
+    assert "no judge checked it (timeout)" in u["why"] and len(u["why"]) <= rs.WHY_MAX
+
+
+def test_unjudged_titles_pass_the_librarian_redaction() -> None:
+    secret = "ExampleSecret123"
+    u = rs._unjudged(_cand("Never ship " + "pass" + "word=" + secret + " in a config"), "timeout")
+    assert secret not in json.dumps(u) and "REDACTED" in u["title"], u
+
+
+def _cands(scores: list[float]) -> list[rs.RiskCandidate]:
+    out = []
+    for i, s in enumerate(scores):
+        c = _cand(f"lesson {i}", score=s)
+        c.version_id = 100 + i
+        out.append(c)
+    return out
+
+
+def test_unjudged_list_skips_warned_and_zero_scores_and_keeps_the_best_three() -> None:
+    cands = _cands([0.05, 0.0, 0.02, 0.03, 0.02, 0.01])
+    warned = rs._det(cands, "timeout", rs.TAU)
+    assert [v for v, _ in warned] == [100]
+    listed = rs._unjudged_list(cands, warned, "timeout")
+    # best det_score first, retrieval order breaks the 0.02 tie; 0.0 (no qualifying list) never
+    assert [v for v, _ in listed] == [103, 102, 104]
+
+
+def test_pack_unjudged_uses_its_own_fields_and_keeps_the_warnings() -> None:
+    env = {**_env(), "judged": False, "judge": "retrieval_only", "reason": "timeout"}
+    warnings = [_item(1, 20)]
+    out = rs._pack(
+        _Deps(),
+        dict(env),
+        4000,
+        list(warnings),
+        [_item(10 + i, 30) for i in range(3)],
+        names=rs.UNJUDGED_FIELDS,
+    )
+    assert out["warnings"] == warnings and len(out["unjudged"]) == 3 and out["unjudged_omitted"] == 0, out
+    assert "dropped_by_judge" not in out and "dropped_omitted" not in out, out
+    old = _old_pack(dict(env), 256, list(warnings))
+    small = rs._pack(_Deps(), dict(env), 256, list(warnings), [_item(10, 300)], names=rs.UNJUDGED_FIELDS)
+    assert (small["warnings"], small["omitted"]) == (old["warnings"], old["omitted"])
+    assert "unjudged" not in small and small.get("unjudged_omitted") in (1, None), small
