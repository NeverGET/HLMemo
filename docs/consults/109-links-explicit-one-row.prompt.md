Review this small change to HLMemo's offline link proposer (`hlm links explicit`). Reply in <= 25 lines: verdict (GO / GO-WITH-FIXES / NO-GO), then findings ranked by severity (HIGH/MEDIUM/LOW) with file:line and a concrete input that triggers each.

Context: the proposer scans a project's current memory items for explicit textual declarations ("D-006 ... D-002'yi geçersiz kılar", "supersedes D-036") and proposes `supersedes` links; an operator reviews a dry run and the owner approves before any apply; applies are reversible (`--revert`). Links of scope `part` mark a quoted statement of the target as outdated; they never hide the target. The markdown importer stores one item per decision row (`D-NNN | date | status | text`).

Change: (1) `decision_rows()` treats an item whose body starts with a row start as a one-row decision log (previously >= 2 row starts were required, so single-row items were never indexed and no D-id resolved); (2) the Turkish target-list joiner accepts a case suffix after every D-id ("D-150'yi ve D-151'i").

Severity rubric: HIGH = a wrong link proposed from realistic text, or a correct declaration silently lost compared with before; MEDIUM = a plausible edge case producing a wrong or missing link; LOW = style/test gaps. Out of scope: the parser's general marker heuristics that this diff does not touch.

Question to answer explicitly: can the change make a non-decision item (a fact, doc chunk or episode whose body happens to start with "D-NNN |") a resolution target or declaring row in a way that produces a wrong link?

Diff:
commit 2b2d7b4b1d0a0fdc371792f78ce8c629ac0bcdf2
Author: Cemal Kurt <cem_al_96@hotmail.com>
Date:   Tue Oct 6 15:51:46 2026 +0300

    links explicit: one-row decision items are decision logs; TR target lists take a suffix on every D-id (D-249)
    
    The markdown importer stores one item per decision row, so decision_rows() (>= 2 row starts) indexed
    none of them and decision->decision declarations never resolved. An item whose body starts with a row
    start is now a one-row log; a row start after other text stays prose. 4 new tests; 218 link tests pass.
    
    Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
    Claude-Session: https://claude.ai/code/session_01FN9u4yDzVBC4DbCgo2QtKd

diff --git a/src/hlmemo/core/explicit_supersession.py b/src/hlmemo/core/explicit_supersession.py
index fd8c043..dc86037 100644
--- a/src/hlmemo/core/explicit_supersession.py
+++ b/src/hlmemo/core/explicit_supersession.py
@@ -75,7 +75,8 @@ _TR_SUFFIX = r"(?:['’][A-Za-zÇĞİÖŞÜçğıöşü]{1,7})?"
 _JOIN_RE = re.compile(
     r"^[`'\"*\s]*(?:\([^()\n]{0,100}\)[\s`]*)?(?:,|/|&|\band\b|\bund\b|\bsowie\b)[\s`'\"*]*", re.I
 )
-_TR_JOIN_RE = re.compile(r"^[`'\"*\s]*(?:/|\bve\b|\bile\b)[\s`'\"*]*$", re.I)
+# a case suffix may sit on every ref of the list ("D-150'yi ve D-151'i"), not only on the last one
+_TR_JOIN_RE = re.compile(rf"^[`'\"*\s]*{_TR_SUFFIX}[`'\"*\s]*(?:/|\bve\b|\bile\b)[\s`'\"*]*$", re.I)
 # between an explicit subject ref and its marker
 _SUBJ_GAP_RE = re.compile(
     r"^[`'\"*_)\]\s]*(?:['’]s\s+)?(?:(?:is|was|are|were|has|have|had|been|being|now|hereby|also|thereby|fully|"
@@ -305,9 +306,14 @@ def _refs(text: str, offset: int) -> list[Ref]:
 
 # --------------------------------------------------------------------------- declarations
 def decision_rows(body: str) -> list[tuple[str, int, int]]:
-    """``(D-id, start, end)`` of every decision row of a decision log (>= 2 row starts), else []."""
+    """``(D-id, start, end)`` of every decision row of a decision log, else [].
+
+    A decision log is an item with >= 2 row starts, or an item that IS one row: its body starts with
+    the row (the markdown importer writes one item per decision row, 2026-10-06). A single row start
+    after other text stays prose (a table row quoted in a document)."""
     starts = [(m.group(1), m.start()) for m in _ROW_RE.finditer(body)]
-    if len(starts) < 2:
+    one_row_item = len(starts) == 1 and not body[: starts[0][1]].strip()
+    if len(starts) < 2 and not one_row_item:
         return []
     return [
         (did, s, starts[i + 1][1] if i + 1 < len(starts) else len(body)) for i, (did, s) in enumerate(starts)
diff --git a/tests/unit/test_explicit_supersession.py b/tests/unit/test_explicit_supersession.py
index 064c9fb..fa336e8 100644
--- a/tests/unit/test_explicit_supersession.py
+++ b/tests/unit/test_explicit_supersession.py
@@ -342,3 +342,39 @@ def test_quotes_are_verbatim_and_bounded_for_long_rows() -> None:
     )
     (p,) = propose([d0, d1])
     assert len(p.quote) <= QUOTE_MAX and p.quote in d0.body and p.quote.startswith("D-036 |")
+
+
+def _row_item(vid: int, did: str, text: str) -> Doc:
+    """One decision row as the markdown importer stores it: one item per row, keyed `#D-NNN`."""
+    return doc(vid, f"decisions/DECISIONS.md#{did}", f"{did} | 2026-08-0{vid} | KABUL | {text}\n")
+
+
+def test_one_row_items_are_decision_rows_and_resolve() -> None:
+    d2 = _row_item(1, "D-002", "Kural A en az 200 işlem ister.")
+    d6 = _row_item(2, "D-006", "Kural A en az 120 işlem ister — D-002'yi geçersiz kılar.")
+    (p,) = propose([d2, d6])
+    assert (p.source_ref, p.target_ref, p.scope, p.marker) == ("D-006", "D-002", "part", "supersedes")
+    assert (p.source_logical_id, p.target_logical_id) == (d6.logical_id, d2.logical_id)
+    assert p.quote == d2.body.rstrip("\n") or p.quote in d2.body
+
+
+def test_one_row_item_retiring_two_rows() -> None:
+    d50, d51 = _row_item(1, "D-150", "Eski kural A."), _row_item(2, "D-151", "Eski kural B.")
+    for text in ("D-150 ve D-151'i", "D-150'yi ve D-151'i", "D-150/D-151'i"):
+        d54 = _row_item(3, "D-154", f"Yeni kural — {text} geçersiz kılar.")
+        assert {(p.source_ref, p.target_ref, p.scope) for p in propose([d50, d51, d54])} == {
+            ("D-154", "D-150", "part"),
+            ("D-154", "D-151", "part"),
+        }, text
+
+
+def test_one_row_item_partial_change_wording_declares_nothing() -> None:
+    d2 = _row_item(1, "D-002", "Kural A en az 200 işlem ister.")
+    d6 = _row_item(2, "D-006", "D-002'nin eşik kısmını değiştirir; gerisi geçerli.")
+    assert propose([d2, d6]) == []
+
+
+def test_a_row_start_after_other_text_is_not_a_decision_row() -> None:
+    quoted = doc(1, "docs/notes.md", "Tablodan bir satır:\nD-002 | 2026-08-01 | KABUL | Kural A 200.\n")
+    d6 = _row_item(2, "D-006", "Yeni eşik — D-002'yi geçersiz kılar.")
+    assert propose([quoted, d6]) == []
