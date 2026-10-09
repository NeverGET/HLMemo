Round 2 (final) of the review of an HLMemo release candidate (consult 121: threat model, rubric and round-1 findings below). Working dir = a clean export of commit 9385d5e, read-only.
Round 1: Astra GO (one LOW on threat-model wording: uniqueness is counted within the chosen field, body first). Sol 6.1 NO-GO, one HIGH: the new span_in_title hint (22 o200k tokens) outgrew the longest existing hint (17), so the pessimistic ack grew 5 tokens per update and a valid 3-update request at token_budget 322 needed 337. Fix: the hint is now 16 tokens; a unit test pins that no hint outgrows the pre-change longest one; measured pessimistic_ack_entries is 80/236 tokens for 1/3 updates on the base, 85/251 on the round-1 candidate, 80/236 now.
Reply in <= 15 lines: verdict (GO / GO-WITH-FIXES / NO-GO) and any remaining HIGH/MEDIUM per the rubric with file:line and a concrete failing input.

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

## Round-1 findings
### Astra
**GO**

HIGH / MEDIUM bulgu yok.

LOW — `src/hlmemo/core/write_updates.py:203`: threat model’in #3 maddesi body-first sözleşmesiyle çelişiyor. Tetikleyici: title=`Cache TTL`, body=`Cache TTL is 60 seconds.`, span=`Cache TTL`; body eşleşmesi kabul edilir. Benzersizliğin seçilen alan içinde arandığını threat model’de netleştirin; global benzersizlik uygulamak mevcut body davranışını değiştirir.

(a) **Hayır.** Görünürlük, proje yetkisi, device scope ve kapalı/superseded hedef kontrolleri title aramasından önce uygulanıyor (`write_updates.py:312–400`). Mutasyon için `replacement_visibility` korunuyor (`217–218`); historical hedef hâlâ yalnızca link alıyor (`406–407`).

(b) **Body’de bulunan span için kabul/ret sonucu değişmiyor.** Tekrar, kelime sınırı ve NFC retleri korunuyor (`200–224`). Body’de bulunmayan title span’larının sonucu tasarım gereği değişiyor; mevcut `span_not_found` hint metni de değişiyor.

(c) **Hayır.** Title eşleşmesinde revise erken reddediliyor (`204–205`); title quote yalnızca whole-scope supersede linkine girebiliyor (`511–517`). Title yeniden yazılmıyor.

(d) **İki alanda birer kez bulunması kabul edilebilir:** body içinde unique sayılır. **Alanlar arasında eşleşme mümkün değil**; metinler birleştirilmiyor (`200–209`).

(e) **Evet.** Pessimistic bound, yeni hint dahil `REASONS` üzerinden dinamik hesaplanıyor (`639–658`); tüm reason/hint’leri kapsayan sınır testi geçti. Replay saklanan ack’i kullanmaya devam ediyor.

Doğrulama: aday export üzerinde **73 unit test geçti**; bağımsız guard incelemesi tamamlandı. DB entegrasyon/replay testlerini çalıştırmadım. Dosya değiştirilmedi.### Sol 6.1
**NO-GO**

**HIGH — Mevcut body-span isteklerinin sonucu değişiyor.** [src/hlmemo/core/write_updates.py:644](/private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/rev121/src/hlmemo/core/write_updates.py:644): yeni hint, pessimistic ack’i **5 token/update** büyütüyor; geçerli istekler bütçeden reddedilebiliyor.

Repro test şekli: aynı proje/cihaz kapsamındaki üç ayrı açık, görünür fact; tek carrier’da üç geçerli body alıntılı `supersede`; UUID `00000000-0000-4000-8000-000000000001`, `token_budget=322`. Gerçek ack ön-kontrolünde base **PASS**, aday **`E_BUDGET_TOO_SMALL`, min=337**. Hint’i eski maksimumun altında tutup bu bütçe regresyonunu test edin.

MEDIUM/LOW: Ek bulgu yok.

(a) **Hayır.** Başlık yolu görünürlük, proje, cihaz, current/open/superseded kontrollerini atlamıyor; historical hedefler yine yalnızca link alıyor.

(b) **Evet, bütçe nedeniyle.** Yukarıdaki HIGH geçerli body isteklerini etkiliyor. Span guard kararları aynı kalıyor.

(c) **Hayır.** Title quote yalnızca supersede/whole link’e gider; revise başlığı değiştirmez.

(d) **Evet, seçilen alan içinde unique sayılır:** title ve body’de birer occurrence varsa body yolu kabul edilir. Bu açık body-first sözleşmesidir; alanlar arasında eşleşme yapılmaz.

(e) **Evet.** Yeni ack kendi pessimistic bound’u içinde kalıyor; sorun bound’un büyümesi. Replay kayıtlı ack’i kullanıyor.

Doğrulama: **73 unit test geçti**, **4.224 guard diferansiyel vaka** ve **322→337** bütçe reproduksiyonu doğrulandı. DB/replay entegrasyonu çalıştırılmadı.
## Diff of the fix
diff --git a/src/hlmemo/core/write_updates.py b/src/hlmemo/core/write_updates.py
index 59946fa..d52600e 100644
--- a/src/hlmemo/core/write_updates.py
+++ b/src/hlmemo/core/write_updates.py
@@ -79,10 +79,10 @@ REASONS: dict[str, tuple[str | None, str]] = {
         "this item already links supersedes to that memory: drop that link or the update",
     ),
     "span_not_found": ("E_INVALID_ARG", "old_span is not in that memory's body: copy it verbatim"),
+    # review 121 (Sol): no hint may outgrow the longest one before it, or the pessimistic ack grows
     "span_in_title": (
         "E_INVALID_ARG",
-        "old_span is in the title, which revise does not change:"
-        " use mode supersede when the title is outdated",
+        "old_span is only in the title; revise keeps titles: use mode supersede",
     ),
     "span_not_unique": ("E_INVALID_ARG", "old_span occurs more than once in that memory: quote more words"),
     "span_word_boundary": ("E_INVALID_ARG", "old_span must start and end at word boundaries"),
diff --git a/tests/unit/test_write_updates.py b/tests/unit/test_write_updates.py
index 5513fca..12d3de5 100644
--- a/tests/unit/test_write_updates.py
+++ b/tests/unit/test_write_updates.py
@@ -507,3 +507,12 @@ def test_the_wire_schema_advertises_updates_and_the_description_guides_the_agent
     desc = TOOL_BY_NAME["memory.write"].description
     for word in ("updates", "clue", "old_span", "revise", "supersede", "verbatim", "rejected"):
         assert word in desc
+
+
+def test_r121_no_hint_outgrows_the_longest_hint_before_the_title_rule() -> None:
+    """Review 121 (Sol 6.1): the pessimistic ack sizes every update by the longest hint, so a longer new hint
+    made requests that fit a budget before (322 tokens, three body-span supersedes) need 337 after."""
+    from hlmemo.core.write_updates import REASONS
+
+    longest_before = Meter().count_text("replacement is too long: at most 3x old_span and 1000 characters")
+    assert max(Meter().count_text(h) for _, h in REASONS.values()) <= longest_before
