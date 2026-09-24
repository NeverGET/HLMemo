## Verdict (DO-NOT-MERGE)

Signal yarışı ve `widen_scope` döngüsü kapanmış; yeni deadlock, scope leak veya D‑074 observer mutasyonu bulmadım. Ancak legacy W2a cevapları promotion sırasında kalıcı olarak strand olabiliyor ve cycle sıralaması D‑087’nin “6a96ba1’den daha kötü değil” garantisini ihlal ediyor. DB suite’leri read-only ortamda yeniden çalıştırılmadı; cycle kusuru saf repro ile doğrulandı.

## PROMOTION-READY (no)

Legacy `proposal.mutation` biçimli `accepted_pending` cevaplar hiçbir release yolu tarafından seçilmiyor.

## Per-item

1. **FIXED** — Signal/proposal hedefleri event-id’den önce sıralı kilitleniyor: `src/hlmemo/librarian/worker.py:625`, `:1058`, `:1085`.
2. **FIXED** — `widen_scope` promotion/release/sweeper ve batch apply dışında; yalnız açık owner answer uygular: `src/hlmemo/librarian/roles.py:156`, `worker.py:827`, `questions.py:175`.
3. **PARTIAL** — Runtime event semantiği doğru (`worker.py:555`), fakat queued-job dump maskesi event-recorded backoff sapmasını da gizleyebilir: `tests/integration/_librarian_fixtures.py:244`.
4. **PARTIAL** — Predicate `new ⇒ 6a96ba1` doğru (`core/supersession.py:86`), fakat cycle ordering bütçede daha fazla demotion üretebilir: `core/supersession.py:136`.

## New findings

| severity | file:line | trigger | fix |
|---|---|---|---|
| High | `librarian/roles.py:166`; `tasks/apply_batch.py:48` | Legacy non-widen `proposal.mutation`: `proposal->'actions'` NULL yapar; SQL üç-değerli mantıkla satırı promotion, release ve sweeper’dan sonsuza dek çıkarır. | `COALESCE` kullan veya filtreyi `proposal_actions()` üzerinden uygula; legacy accepted-pending regresyonu ekle. |
| Medium | `core/supersession.py:136`; `core/retrieval.py:449` | Hits `[3, unrelated-9, 1]` ve karşılıklı `3→1/1→3` linkleri yeni kuralda `[9,1,3]`, 6a96ba1’de `[9,3,1]`; dar bütçede hit 3 kaybolur. | Cyclic SCC içindeki tüm ordering edge’lerini yok sayıp özgün interleaving’i koru; tight-budget testi ekle. |
| Medium | `tests/integration/_librarian_fixtures.py:244`; `_w2b_fixtures.py:189`; `_write_fixtures.py:126` | Job-specific consumed backoff hâlâ queued iken event-recorded `run_after/last_error` yanlış replay edilse tüm dump’lar bunu maskeler. | Yalnız systemic hand-back’i maskele; consumed-backoff satırlarını ham alanlarla karşılaştır. |