Round 2 (final) of the review of an HLMemo release candidate (consult 118: threat model, rubric and round-1 findings below). Working dir = a clean export of commit 1d65aa3, read-only.
Round 1 found two HIGH and one MEDIUM, all in `preview_text` (src/hlmemo/core/retrieval.py): code, a list or a task list between two horizontal rules was stripped as frontmatter; a comment line inside real frontmatter blocked the skip. The fix: the block must start with a `key:` or `#` line and contain a top-level key the importers write (title, name, date, tags, source_path, valid_from, kind, description, metadata, hlm_export, logical_id); comment lines are allowed. The reviewers found nothing in `unjudged` (visibility, verdict/warnings/budget, redaction).
Reply in <= 20 lines: verdict (GO / GO-WITH-FIXES / NO-GO), then any remaining HIGH/MEDIUM per the rubric with file:line and a concrete input that fails (a HIGH needs the shape of a reproducing test). Focus on preview_text: can it still drop body text that is not frontmatter, miss a real importer-written frontmatter block, or backtrack super-linearly?

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

## Round-1 findings
### Astra
**NO-GO**

- **HIGH — `src/hlmemo/core/retrieval.py:303,314–318`:** Regex gerçek frontmatter olmayan gövdeyi de kaldırıyor. Tetikleyici: yatay çizgiler arasında girintili kod veya Markdown listesi.
  Yeniden üreten test: `s = "---\n    print(123)\n---\nAfter"; assert preview_text(s, 0) == s`
  Test başarısız: gerçek çıktı `"After"`. Ayrıca `title: [unterminated` gibi geçersiz YAML da kaldırılıyor. Frontmatter’ı yalnızca bu satır biçimleriyle tanımak yeterli değil.

**(a)** Görünürlük bakımından **hayır**: `unjudged` aynı aday kümesinden geliyor; proje/device/isolation/current filtreleri ve judge çağrısından sonraki D-062 kontrolü uygulanıyor.

**(b)** İncelenen yollarda **değişiklik bulmadım**. Warnings önce aynı şekilde paketleniyor; ek alanlar kalan bütçeye sığdırılıyor. `verdict/warnings/omitted/judged` ve mevcut bütçe hatası davranışı korunuyor; limit aşımı bulmadım.

**(c)** Mutlak anlamda **evet**: kabul edilmiş JSON/çok sözcüklü quoted-value redaksiyon boşlukları sürüyor; mevcut `_warning` da ham başlık döndürüyor (`risk_service.py:267`). Yeni `unjudged` yolu `_safe_title` kullanıyor; kabul edilenler dışında yeni bir sızıntı doğrulamadım.

**(d)** Gövde kaybı **evet**, yukarıdaki HIGH ile doğrulandı. Süper-lineer backtracking bulmadım; incelenen alternatifler satır bazında ayrışıyor. Kapanışı olmayan 1k–8k girdilerde süre yaklaşık doğrusal arttı.

Doğrulama: aday dosyadan çıkarılan gerçek fonksiyonla karşı örnekler çalıştırıldı; bağımsız risk incelemesi yapıldı. Pytest, salt-okunur ortamda tiktoken geçici dizin gereksinimi nedeniyle collection aşamasında durdu; entegrasyon testleri çalıştırılmadı.### Sol
NO-GO

HIGH

- [src/hlmemo/core/retrieval.py:302](</private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/rev118/src/hlmemo/core/retrieval.py:302>) — `preview_text` sıradan Markdown görev listesini frontmatter sanıp siliyor.
- Repro: `s="---\n- [ ] rotate database\n- [x] verify backup\n---\nBody continues"`; `assert preview_text(s, 0) == s`.
- Gerçekte yalnızca `"Body continues"` dönüyor. PyYAML listeyi geçersiz YAML olarak reddediyor; unsplit import yolu bunu gövde olarak saklıyor ([build.py:165](</private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/rev118/src/hlmemo/importers/build.py:165>)). Bu, rubricteki body-text-loss HIGH.

MEDIUM

- [retrieval.py:302](</private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/rev118/src/hlmemo/core/retrieval.py:302>) — Geçerli frontmatter içindeki üst-seviye yorum (`# generated`) stripping’i engelliyor; “leading YAML block” vaadiyle çelişiyor ([BACKLOG.md:96](</private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/rev118/docs/status/BACKLOG.md:96>)).

LOW

- Yok.

Yanıtlar

- (a) Hayır; `unjudged` aynı görünür/current/active aday kümesinden geliyor ve judge çağrısı sonrası aynı D-062 re-check’ten geçiyor.
- (b) Hayır; 3.000 diferansiyel bütçe vakasında verdict/warnings/omitted/judged ve budget-error davranışı değişmedi; `budget.used ≤ limit`.
- (c) Yeni sızıntı yolu yok; `unjudged.title` `_safe_title` kullanıyor. Ancak önceden kabul edilen JSON-style/quoted multi-word Redactor boşlukları hâlâ unredacted geçebilir.
- (d) Evet, yukarıdaki HIGH ile body text düşebilir; super-linear backtracking bulmadım, regex büyümesi lineer.

Doğrulama: risk packing unit testleri 26/26 geçti; preview reproduksiyonu doğrudan çalıştırıldı. DB integration suite, adanmış test DSN olmadığı için çalıştırılmadı.
## Diff of the fix (round 1 → candidate)
diff --git a/src/hlmemo/core/retrieval.py b/src/hlmemo/core/retrieval.py
index b9d8d1f..3fc376f 100644
--- a/src/hlmemo/core/retrieval.py
+++ b/src/hlmemo/core/retrieval.py
@@ -298,9 +298,15 @@ def term_matches(text: str, terms: Sequence[str]) -> list[tuple[int, int]]:
     return out
 
 
-#: a YAML frontmatter block opening an item: ``---``, then key, indented, list or blank lines, ``---``
+#: a YAML frontmatter block opening an item: ``---``, a ``key:`` or ``#`` comment line, then key, indented,
+#: list, comment or blank lines, ``---``. It counts only with a top-level key the importers write (review
+#: 118: prose, code or a task list between two horizontal rules is body text)
 _FRONTMATTER_RE = re.compile(
-    r"\A---[ \t]*\n(?:[A-Za-z_][\w-]*:.*\n|[ \t]+\S.*\n|-[ \t].*\n|[ \t]*\n){1,40}?---[ \t]*(?:\n|\Z)"
+    r"\A---[ \t]*\n((?:[A-Za-z_][\w-]*:.*\n|#.*\n)"
+    r"(?:[A-Za-z_][\w-]*:.*\n|[ \t]+\S.*\n|-[ \t].*\n|#.*\n|[ \t]*\n){0,39}?)---[ \t]*(?:\n|\Z)"
+)
+_FRONTMATTER_KEY_RE = re.compile(
+    r"^(?:title|name|date|tags|source_path|valid_from|kind|description|metadata|hlm_export|logical_id):", re.M
 )
 
 
@@ -312,7 +318,7 @@ def preview_text(text: str, ordinal: int) -> str:
     if ordinal != 0:
         return text
     m = _FRONTMATTER_RE.match(text)
-    if m is None:
+    if m is None or not _FRONTMATTER_KEY_RE.search(m.group(1)):
         return text
     rest = text[m.end() :].lstrip("\n")
     return rest if rest.strip() else text
diff --git a/tests/unit/test_d055_retrieval.py b/tests/unit/test_d055_retrieval.py
index a926c18..7e1c4a8 100644
--- a/tests/unit/test_d055_retrieval.py
+++ b/tests/unit/test_d055_retrieval.py
@@ -617,6 +617,19 @@ _BODY = "## Mistake\nThe serving container lacked the healthcheck label, so the
         ("---\ntitle: t\n---\n", 0, "---\ntitle: t\n---\n"),  # nothing but the block: kept
         ("---\ntitle: t\nno closing line", 0, "---\ntitle: t\nno closing line"),
         (_BODY, 0, _BODY),
+        # review 118 (Astra): code or a list between two horizontal rules is body, not frontmatter
+        ("---\n    print(123)\n---\nAfter", 0, "---\n    print(123)\n---\nAfter"),
+        ("---\n- one\n- two\n---\nAfter", 0, "---\n- one\n- two\n---\nAfter"),
+        ("---\nNote: a prose line\n---\nAfter", 0, "---\nNote: a prose line\n---\nAfter"),
+        # review 118 (Sol): a task list between two rules is body; a comment inside real frontmatter is fine
+        (
+            "---\n- [ ] rotate database\n- [x] verify backup\n---\nBody continues",
+            0,
+            "---\n- [ ] rotate database\n- [x] verify backup\n---\nBody continues",
+        ),
+        ("---\n# generated\ntitle: t\ndate: 2026-05-01\n---\nBody", 0, "Body"),
+        ("---\ntitle: t\n# generated\ntags: [a]\n---\nBody", 0, "Body"),
+        ("---\n# Heading only\n---\nAfter", 0, "---\n# Heading only\n---\nAfter"),
     ],
 )
 def test_preview_text_skips_a_leading_yaml_block_of_chunk_zero(
@@ -634,3 +647,13 @@ def test_a_hit_preview_starts_after_the_frontmatter(meter: Meter) -> None:
     hit = render_hit(meter, f, PREVIEW_TOK, terms)
     assert "swap script" in hit["preview"], hit["preview"]
     assert "title:" not in hit["preview"] and "source_path" not in hit["preview"], hit["preview"]
+
+
+def test_preview_text_is_linear_on_long_unclosed_input() -> None:
+    import time
+
+    for n in (2_000, 64_000):
+        text = "---\ntitle: t\n" + "  x\n" * n
+        t0 = time.perf_counter()
+        assert preview_text(text, 0) == text
+        assert time.perf_counter() - t0 < 0.5, n
