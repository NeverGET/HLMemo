Review a release candidate of HLMemo (MCP memory server): a supersede update may quote the target's TITLE; revise gets a precise `span_in_title` hint; the card-size error names the 420 aim. Working dir = a clean export of the candidate (commit bc7156a), read-only. The threat model and severity rubric, written before this review, follow; then the diff.
Reply in <= 30 lines: verdict (GO / GO-WITH-FIXES / NO-GO), then findings HIGH/MEDIUM/LOW per the rubric with file:line and a concrete trigger (a HIGH needs the shape of a reproducing test). Answer explicitly: (a) can a title match bypass any guard the body path applies (visibility, closed/superseded/historical target, project, device scope)? (b) can any request that passes or fails today with a body span change outcome? (c) can a part-scope link get a title quote, or a revise change a title? (d) can a span count as unique while it occurs in both title and body, or match across them? (e) does the ack stay within its pessimistic bound?

# Supersede by a title quote, precise span hints, card-size aim: threat model and severity rubric (before review)

Written 2026-10-09, before the change is reviewed. Trigger: the third test-drive notes (BACKLOG "Third test-drive
findings"). A writer superseded an item whose title held the outdated claim; quoting the title gave `span_not_found`
because `old_span` is matched only against the body (`core/write_updates.py` → `librarian/revise.py`). The protocol
says supersede is for "the whole memory or its title is outdated", so the refusal contradicted the rule it enforces.

## The change
1. **Supersede accepts a title quote.** With `mode: supersede`, a span not found in the body is matched against the
   target's title with the same rules (NFC, byte-exact, exactly one occurrence, no word cut). On a match the link is
   written as before: scope `whole`, `quote` = the span, plus `quote_in: "title"`. A span found in the body takes the
   body path unchanged.
2. **Revise names the title.** With `mode: revise`, a span found only in the title is refused with a new reason
   `span_in_title` and a hint to use supersede; `span_not_found` now says it searched the body.
3. **Card size.** `E_CARD_TOO_LARGE` names the protocol aim (≤ 420) next to the hard limit (512).
4. No tool description or schema change (tools/list stays at 2998/3000).

## What is protected
- **Append-only history and the supersede contract:** a supersede closes exactly the targeted item and records why;
  nothing else changes. Part-scope quotes must stay re-checkable against the body (`span_quoted_once`).
- **Grounding:** an update must cite text that really is in the target (no invented spans); the same NFC, uniqueness
  and word-boundary rules apply to title and body.
- **Visibility and authority:** the target must be readable and writable by the caller, in its project and device
  scope, as today (VISIBILITY_GUARDS). Historical (link-only) targets behave as today.
- **Replay and idempotency:** a replayed request returns the stored ack; the ack sizing still bounds every hint.
- **Revise semantics:** revise still changes only the body; titles are never rewritten by this release.

## Failure modes to look for
1. A title match that bypasses a guard the body path applies (visibility, closed or superseded target, historical
   target, cross-project target, device scope).
2. A title span accepted for revise, or a part-scope link created with a title quote (it would then fail the body
   re-check and could hide or mis-scope the link).
3. A span that occurs once in the title and once in the body counted as unique, or matched across the title/body
   boundary.
4. A change in the outcome of any request that passes or fails today for a body span.
5. The ack exceeding its pessimistic size bound with the new hint.

## Severity rubric
- **HIGH:** a supersede that closes an item the caller could not supersede today through a body span (visibility,
  project, scope, state); a part-scope link with a title quote; any change in the result of an existing body-span
  request; a revise that rewrites a title; a replay that returns a different ack.
- **MEDIUM:** a wrong reason or hint, an ack over its size bound, docs contradicting the code, a title match with
  rules looser than the body's.
- **LOW:** wording, comments, test gaps without a reachable failure.

## Diff (src, tests, docs) since the base
diff --git a/docs/USAGE.md b/docs/USAGE.md
index 0507558..056c9bc 100644
--- a/docs/USAGE.md
+++ b/docs/USAGE.md
@@ -223,16 +223,22 @@ An agent that writes a memory which CORRECTS one it read (a `memory.query` / `me
 - `mode: revise` replaces ONLY `old_span` (verbatim, exactly once, on word boundaries, not the whole
   memory) with `replacement`, which must occur verbatim in THIS item's body (omitted: the body itself, if
   it is one statement). The target gets a new version byte-identical outside the span; the old text stays
-  valid before the cut (the item's `valid_from`, else now).
+  valid before the cut (the item's `valid_from`, else now). `old_span` is looked up in the target's BODY:
+  a span found only in its title is `span_in_title` (revise never changes the title; supersede when the
+  title is outdated), one found nowhere is `span_not_found`.
 - `mode: supersede` closes the whole memory at the cut and links `supersedes` from the new item. Its
-  `old_span` too must occur exactly once, on word boundaries (it may be the whole memory).
+  `old_span` too must occur exactly once, on word boundaries (it may be the whole memory). When the body
+  does not hold it at all, the same rules (NFC, exactly once, word boundaries) apply to the target's TITLE:
+  a memory whose title went stale can be superseded by quoting the title (the link is scope `whole` and
+  records `quote_in: "title"`). A span found in the body is always judged there.
 - A revise or a close needs THIS item to be visible wherever the target is (its projects include the
   target's, its `device_scope` is `all` or the target's); else `replacement_visibility`.
 - Historical records (episodes, session notes, decision/ADR rows) are never rewritten: both modes only add
   a `supersedes` link (`linked`, scope `part` for revise, `whole` for supersede), after the same `old_span`
   rules (and, for revise, the replacement rules). Kinds that may be changed:
   `HLM_LIBRARIAN_REVISE_KINDS` (default `fact,lesson,doc_chunk`).
-- The ack lists each update as `applied`, `linked` or `rejected` (`code`, `reason`, a fixed `hint`). Undo one:
+- The ack lists each update as `applied`, `linked` or `rejected` (`code`, `reason`, a fixed `hint`; the
+  reasons and hints are `REASONS` in `src/hlmemo/core/write_updates.py`). Undo one:
   `python -m hlmemo.ops librarian revert-update <write event> --item I --update K --reason ..` (ONE
   compensating event; refused while a later change depends on it).
 
diff --git a/src/hlmemo/core/write_service.py b/src/hlmemo/core/write_service.py
index 2be10ec..299d6f3 100644
--- a/src/hlmemo/core/write_service.py
+++ b/src/hlmemo/core/write_service.py
@@ -731,12 +731,16 @@ async def _check_content(conn: AsyncConnection, deps: WriteDeps, plans: list[_Pl
         it = p.item
         p.token_count = await _cpu(deps.meter.count_text, it.body)
         if p.is_card and p.token_count > CARD_MAX_TOKENS:
+            from hlmemo.brief.assemble import CARD_TOKENS  # what the brief shows: the protocol's aim
+
             raise ToolError(
                 "E_CARD_TOO_LARGE",
-                f"items[{p.index}]: project card body is {p.token_count} tokens (max {CARD_MAX_TOKENS})",
+                f"items[{p.index}]: project card body is {p.token_count} tokens"
+                f" (max {CARD_MAX_TOKENS}; aim for ≤ {CARD_TOKENS}, protocol R17)",
                 index=p.index,
                 tokens=p.token_count,
                 max=CARD_MAX_TOKENS,
+                aim=CARD_TOKENS,
             )
         if it.device_scope.startswith("device:"):
             if not await q.device_scope_target_ok(conn, int(it.device_scope.split(":", 1)[1])):
diff --git a/src/hlmemo/core/write_updates.py b/src/hlmemo/core/write_updates.py
index 8685607..59946fa 100644
--- a/src/hlmemo/core/write_updates.py
+++ b/src/hlmemo/core/write_updates.py
@@ -26,7 +26,10 @@ update). A revise (also a historical one, which becomes a part-scope link) quote
 (``span_not_whole``) and passes the replacement rules (``REPLACEMENT_GUARDS``). A MUTATING update (a
 revise or a closing supersede) needs a carrying item visible wherever the target is
 (``replacement_visibility``): a narrower carrier would change the memory for readers who can never
-see why. A supersede quoting the whole memory is allowed (it IS the whole-item update).
+see why. A supersede quoting the whole memory is allowed (it IS the whole-item update). A span absent
+from the body is looked up in the TITLE: a supersede may quote the outdated title (the same span rules
+over the NFC title; a whole-scope link with ``quote_in: "title"``), a revise is rejected
+``span_in_title`` (it never changes the title, and a part-scope quote must stay in the body).
 
 Order inside the write transaction (consult 74; J/D-095): ``parse`` (pure) → ``resolve`` BEFORE
 any lock (§4.4 (a) visibility; a hidden or unknown target is ``not_found`` and never locked) →
@@ -75,7 +78,12 @@ REASONS: dict[str, tuple[str | None, str]] = {
         "E_INVALID_ARG",
         "this item already links supersedes to that memory: drop that link or the update",
     ),
-    "span_not_found": ("E_INVALID_ARG", "old_span is not in that memory: copy it verbatim"),
+    "span_not_found": ("E_INVALID_ARG", "old_span is not in that memory's body: copy it verbatim"),
+    "span_in_title": (
+        "E_INVALID_ARG",
+        "old_span is in the title, which revise does not change:"
+        " use mode supersede when the title is outdated",
+    ),
     "span_not_unique": ("E_INVALID_ARG", "old_span occurs more than once in that memory: quote more words"),
     "span_word_boundary": ("E_INVALID_ARG", "old_span must start and end at word boundaries"),
     "span_whole": ("E_INVALID_ARG", "old_span is (almost) the whole memory: use mode supersede"),
@@ -156,12 +164,18 @@ def update_guards(
     old_scope: str,
     new_projects: Any,
     new_scope: str,
+    old_title: str | None = None,
 ) -> tuple[Any, str | None]:
     """The D-118 guards of one update, pure: ``(Check, None)`` when every guard of its path holds,
     else ``(None, reason)`` of the first failed one (the order of the module doc). ``historical`` =
     the target is link-only (``revise.revisable``). The replacement is searched ONLY in the carrying
     item's body; an omitted one is the whole body, only when that is one statement and passes the
-    length ratio (else ``replacement_required``)."""
+    length ratio (else ``replacement_required``).
+
+    ``old_span`` is looked up in the target's body. Only when it does not occur there at all is
+    ``old_title`` consulted: a supersede may quote the outdated TITLE (the same ``SPAN_GUARDS`` over
+    the NFC title; the passing ``Check`` has ``quote_in == "title"``, a whole-scope link), a revise
+    is told that it never changes the title (``span_in_title``)."""
     from hlmemo.librarian import revise as rv
 
     revise = mode == "revise"
@@ -169,17 +183,31 @@ def update_guards(
     # only a text revision needs the stored body itself NFC (its span offsets index it); a close or
     # a link is grounded in the NFC text like the quote (byte-exact, no other normalisation)
     body = old_body if revise and not historical else rv.nfc(old_body)
-    chk = rv.check(
-        old_body=body,
-        old_span=old_span,
-        replacement=carrier_body.strip() if implied else (replacement or ""),
-        new_body=carrier_body,
-        old_projects=old_projects,
-        old_scope=old_scope,
-        new_projects=new_projects,
-        new_scope=new_scope,
-    )
-    failed = chk.failed_of(SPAN_GUARDS + (PART_GUARDS if revise else ()))
+
+    def run(text: str) -> tuple[Any, list[str]]:
+        chk = rv.check(
+            old_body=text,
+            old_span=old_span,
+            replacement=carrier_body.strip() if implied else (replacement or ""),
+            new_body=carrier_body,
+            old_projects=old_projects,
+            old_scope=old_scope,
+            new_projects=new_projects,
+            new_scope=new_scope,
+        )
+        return chk, chk.failed_of(SPAN_GUARDS + (PART_GUARDS if revise else ()))
+
+    chk, failed = run(body)
+    span = rv.nfc(old_span)
+    title = rv.nfc(old_title or "")
+    if failed and failed[0] == "old_span_unique" and not rv.occurrences(body, span):
+        if revise:
+            return None, "span_in_title" if rv.occurrences(title, span) else "span_not_found"
+        if not rv.occurrences(title, span):
+            return None, "span_not_found"
+        # a supersede quoting the outdated title: the shared old_span rules over the NFC title
+        chk, failed = run(title)
+        chk.quote_in = "title"
     if not failed and revise:
         if implied and rv.statement_count(carrier_body) > 1:
             return None, "replacement_required"
@@ -192,8 +220,7 @@ def update_guards(
         return chk, None
     first = failed[0]
     if first == "old_span_unique":
-        n = len(rv.occurrences(body, rv.nfc(old_span)))
-        return None, "span_not_found" if n == 0 else "span_not_unique"
+        return None, "span_not_unique"  # occurs, but not exactly once (zero is handled above)
     return None, _GUARD_REASON[first]
 
 
@@ -240,6 +267,7 @@ class Update:
     touched: set[int] = field(default_factory=set)
     action: str | None = None  # revise | close | link
     chk: Any = None
+    quote_in: str = "body"  # body | title (a supersede quoting the outdated title)
     records: list[dict[str, Any]] = field(default_factory=list)
 
     @property
@@ -369,10 +397,12 @@ class WriteUpdates:
                 old_scope=head.device_scope,
                 new_projects=carrier.project_ids,
                 new_scope=carrier.item.device_scope,
+                old_title=head.title,
             )
             if chk is None:
                 u.reject(reason or "span_not_found")
                 continue
+            u.quote_in = chk.quote_in
             if historical is not None:  # link-only in both modes
                 u.action, u.reason = "link", historical
             elif u.spec.mode == "supersede":
@@ -477,6 +507,14 @@ class WriteUpdates:
                 u.status = "applied"
             else:
                 u.status = "linked"
+            props: dict[str, Any] = {
+                "by": "writer",
+                "mode": u.spec.mode,
+                "scope": "part" if u.action == "link" and u.spec.mode == "revise" else "whole",
+                "quote": rv.nfc(u.spec.old_span),
+            }
+            if u.quote_in == "title":  # only a supersede (scope whole): part quotes are body text
+                props["quote_in"] = "title"
             u.records.append(
                 {
                     "op": "link_insert",
@@ -488,12 +526,7 @@ class WriteUpdates:
                     "project_id": home.project_id,
                     "project_ids": [int(p) for p in carrier.project_ids],
                     "device_scope": carrier.item.device_scope,
-                    "props": {
-                        "by": "writer",
-                        "mode": u.spec.mode,
-                        "scope": "part" if u.action == "link" and u.spec.mode == "revise" else "whole",
-                        "quote": rv.nfc(u.spec.old_span),
-                    },
+                    "props": props,
                     "valid_from": link_from,  # a close: where the target stops
                     "valid_to": None,
                     "assessed": {},
diff --git a/src/hlmemo/librarian/revise.py b/src/hlmemo/librarian/revise.py
index 830a2c5..8dbb03b 100644
--- a/src/hlmemo/librarian/revise.py
+++ b/src/hlmemo/librarian/revise.py
@@ -133,12 +133,16 @@ def visible_to(
 @dataclass(slots=True)
 class Check:
     """The verdict of ``check``: every guard's result (``None`` = not evaluable because an earlier
-    guard failed), the span offsets into the stored old body and the NFC replacement."""
+    guard failed), the span offsets into the stored old body and the NFC replacement. ``quote_in``
+    is where the span was found: ``body``, or ``title`` for a write-time supersede that quotes the
+    outdated title (``write_updates.update_guards``; its offsets index the NFC title and are never
+    used to change text)."""
 
     guards: dict[str, bool | None] = field(default_factory=dict)
     start: int | None = None
     end: int | None = None
     replacement: str = ""
+    quote_in: str = "body"
 
     @property
     def ok(self) -> bool:
diff --git a/tests/integration/test_g6_write.py b/tests/integration/test_g6_write.py
index 4b76b18..b376761 100644
--- a/tests/integration/test_g6_write.py
+++ b/tests/integration/test_g6_write.py
@@ -459,6 +459,8 @@ async def test_card_too_large(connect, world, deps) -> None:
             )
         await conn.rollback()
         assert _err(ei).code == "E_CARD_TOO_LARGE" and _err(ei).details["max"] == 512
+        # the protocol's aim (what the brief shows) is named next to the hard limit
+        assert _err(ei).details["aim"] == 420 and "(max 512; aim for ≤ 420, protocol R17)" in _err(ei).message
         assert await count(conn, "events") == 0 and await count(conn, "memory_versions") == 0
         # 512 tokens or fewer is fine
         ok = await write(
diff --git a/tests/integration/test_superseded_read.py b/tests/integration/test_superseded_read.py
index 2eb740c..57f7b18 100644
--- a/tests/integration/test_superseded_read.py
+++ b/tests/integration/test_superseded_read.py
@@ -33,7 +33,9 @@ from tests.integration.test_write_updates import (  # noqa: F401 - fixtures by i
     SPAN,
     _current,
     _device,
+    _links,
     _old,
+    _replay_identical,
     _rows,
     _upd,
     _write,
@@ -127,6 +129,64 @@ async def test_raw_names_the_whole_superseder_of_a_closed_item(connect, world, d
     assert (await _raw(connect, world, read_deps, new["version_id"]))["superseded_by"] == []
 
 
+async def test_a_supersede_quoting_the_outdated_title_closes_and_is_named_on_raw(
+    connect, world, deps, read_deps
+) -> None:  # noqa: ANN001
+    """A writer whose target's TITLE is what went stale quotes the title ("Cache settings" occurs in
+    no body line): the supersede applies, closes the old item at the cut, its link is whole-scope
+    with ``quote_in: "title"``, and memory.raw of the old version names the superseder (no quote: a
+    whole entry). A revise quoting the title is told to supersede instead."""
+    title = OLD[0]
+    assert title not in OLD[1]
+    old = await _old(connect, world, deps)
+    ack = await _write(
+        connect, world.ctx_a, MAIN, [item(*NEW_TTL, updates=[_upd(old, title, replacement=REPL)])], deps
+    )
+    (u,) = ack["updates"]
+    assert (u["status"], u["code"], u["reason"]) == ("rejected", "E_INVALID_ARG", "span_in_title")
+    assert "supersede" in u["hint"]
+    assert [r["body"] for r in _current(await _rows(connect, old["logical_id"]))] == [OLD[1]]
+    ack = await _write(
+        connect,
+        world.ctx_a,
+        MAIN,
+        [
+            item(
+                "Cache replaced",
+                "The cache was removed; responses are not cached.",
+                valid_from=D_EFF.isoformat(),
+                updates=[_upd(old, title, mode="supersede")],
+            )
+        ],
+        deps,
+    )
+    (new,) = ack["versions"]
+    assert ack["updates"] == [{"index": 0, "update": 0, "status": "applied", "mode": "supersede"}]
+    (survivor,) = _current(await _rows(connect, old["logical_id"]))
+    assert (survivor["body"], survivor["vf"], survivor["vt"]) == (OLD[1], D_OLD, D_EFF)  # closed at the cut
+    ((src, dst, dst_v, rel, _vf, props),) = await _links(connect)
+    assert (src, dst, dst_v, rel) == (new["logical_id"], old["logical_id"], old["version_id"], "supersedes")
+    assert props == {
+        "by": "writer",
+        "mode": "supersede",
+        "scope": "whole",
+        "quote": title,
+        "quote_in": "title",
+    }
+    out = await _raw(connect, world, read_deps, old["version_id"])
+    assert out["superseded_by"] == [
+        {
+            "logical_id": new["logical_id"],
+            "version_id": new["version_id"],
+            "scope": "whole",
+            "valid_from": out["superseded_by"][0]["valid_from"],
+            "valid_to": None,
+        }
+    ]
+    assert datetime.fromisoformat(out["superseded_by"][0]["valid_from"].replace("Z", "+00:00")) == D_EFF
+    await _replay_identical(connect)
+
+
 async def test_raw_marks_part_scope_and_a_revision_names_the_revised_version(
     connect, world, deps, read_deps
 ) -> None:  # noqa: ANN001
diff --git a/tests/integration/test_write_updates.py b/tests/integration/test_write_updates.py
index a2987ad..7af9d08 100644
--- a/tests/integration/test_write_updates.py
+++ b/tests/integration/test_write_updates.py
@@ -304,6 +304,7 @@ async def test_the_replacement_must_be_in_the_carrying_body_not_another_batch_it
     ("upd", "reason"),
     [
         ({"old_span": "90 seconds"}, "span_not_found"),
+        ({"old_span": OLD[0]}, "span_in_title"),  # the title: revise never changes it
         ({"old_span": "cache"}, "span_not_unique"),
         ({"old_span": "0 seconds"}, "span_word_boundary"),
         ({"replacement": "900 seconds"}, "replacement_not_in_body"),
diff --git a/tests/unit/test_write_updates.py b/tests/unit/test_write_updates.py
index 2564a27..5513fca 100644
--- a/tests/unit/test_write_updates.py
+++ b/tests/unit/test_write_updates.py
@@ -120,7 +120,9 @@ def test_a_non_nfc_target_cannot_be_span_revised() -> None:
 
 
 def test_the_replacement_is_searched_only_in_the_carrying_body() -> None:
-    """Consult 74 #2: the title is not searched (unlike B-real) and nothing else of the batch is."""
+    """Consult 74 #2: the carrying item's title is not searched for the replacement (unlike B-real)
+    and nothing else of the batch is. (The TARGET's title only grounds a supersede's old_span: see
+    the title tests below.)"""
     assert guards(carrier_body="Cache TTL raised.", replacement="300 seconds") == (
         None,
         "replacement_not_in_body",
@@ -213,6 +215,84 @@ def test_r96_sol3_a_closing_supersede_needs_a_carrier_as_visible_as_its_target(k
     assert "widen" in hint and "supersede" not in hint  # supersede is no way around it any more
 
 
+# --------------------------------------------------------------------------- the target's title
+TITLE = "Ops defaults: cache TTL, nightly backups, Tuesday deploys"
+
+
+@pytest.mark.parametrize("historical", [False, True])
+def test_a_supersede_may_quote_the_outdated_title(historical: bool) -> None:
+    """A writer superseding a memory whose TITLE is outdated quotes the title: when the span is not
+    in the body at all, the shared old_span rules run over the NFC title and the update passes as a
+    whole-memory update (``quote_in == "title"``)."""
+    chk, reason = ug("supersede", historical, old_span="Tuesday deploys", old_title=TITLE)
+    assert reason is None and chk.quote_in == "title"
+    assert (chk.start, chk.end) == (TITLE.index("Tuesday deploys"), len(TITLE))  # offsets into the title
+    # a body quote stays a body quote, also when the title holds it too
+    chk, reason = ug("supersede", historical, old_span="Backups run nightly", old_title=TITLE)
+    assert reason is None and chk.quote_in == "body"
+    chk, reason = ug("supersede", historical, old_span="cache TTL", old_title="The cache TTL is 60 seconds")
+    assert reason is None and chk.quote_in == "body"
+    # without a title the old behaviour holds
+    assert ug("supersede", historical, old_span="Tuesday deploys") == (None, "span_not_found")
+
+
+@pytest.mark.parametrize("historical", [False, True])
+def test_a_revise_quoting_the_title_is_told_to_supersede(historical: bool) -> None:
+    """A revise changes only the body (and a part-scope quote must stay in it): a span found only in
+    the title is ``span_in_title``, whose hint names supersede; a span found nowhere stays
+    ``span_not_found``, whose hint says it looks in the body."""
+    kw = {"replacement": "300 seconds", "old_title": TITLE}
+    assert ug("revise", historical, old_span="Tuesday deploys", **kw) == (None, "span_in_title")
+    assert ug("revise", historical, old_span="Ops default", **kw) == (None, "span_in_title")  # any hit
+    assert ug("revise", historical, old_span="Memcached", **kw) == (None, "span_not_found")
+    code, hint = REASONS["span_in_title"]
+    assert code == "E_INVALID_ARG" and "title" in hint and "supersede" in hint
+    assert "body" in REASONS["span_not_found"][1]
+
+
+@pytest.mark.parametrize("historical", [False, True])
+@pytest.mark.parametrize(
+    ("title", "span", "reason"),
+    [
+        (TITLE, "Memcached", "span_not_found"),  # neither body nor title
+        (TITLE, "Ops default", "span_word_boundary"),  # cuts "defaults"
+        (TITLE, "efaults", "span_word_boundary"),
+        ("Ops defaults and Ops owners", "Ops", "span_not_unique"),  # twice in the title
+        ("Café hours", "Cafe hours", "span_not_found"),  # no folding beyond NFC
+    ],
+)
+def test_a_title_quote_passes_the_same_shared_rules(
+    title: str, span: str, reason: str, historical: bool
+) -> None:
+    assert ug("supersede", historical, old_span=span, old_title=title) == (None, reason)
+
+
+def test_a_title_quote_is_nfc_byte_exact_and_still_needs_a_visible_carrier() -> None:
+    nfc, nfd = "Café hours", "Café hours"
+    assert nfc != nfd
+    for title, span in ((nfd, nfc), (nfc, nfd)):
+        chk, reason = ug("supersede", old_span=span, old_title=title)
+        assert reason is None and chk.quote_in == "title"
+    # review 96 Sol #3 holds for a title quote: a closing supersede never narrows the readers
+    assert ug("supersede", old_span="Tuesday deploys", old_title=TITLE, new_scope="device:7") == (
+        None,
+        "replacement_visibility",
+    )
+    assert ug("supersede", True, old_span="Tuesday deploys", old_title=TITLE, new_scope="device:7")[1] is None
+
+
+def test_a_body_span_follows_the_body_path_even_when_the_title_would_pass() -> None:
+    """Found in the body (even twice, or cutting a word), the body's verdict stands: the title is
+    consulted only for a span the body does not hold at all."""
+    title = "Deploys on Tuesdays"
+    assert ug("supersede", old_span="on", old_title=title) == (None, "span_not_unique")
+    assert ug("revise", old_span="on", replacement="300 seconds", old_title=title) == (
+        None,
+        "span_not_unique",
+    )
+    assert ug("supersede", old_span="eploys go", old_title="eploys go") == (None, "span_word_boundary")
+
+
 # --------------------------------------------------------------------------- review 96 Astra #2
 class _ChainConn:
     """A fake connection over a restore chain: version (link) ``k`` is superseded and the copy a
