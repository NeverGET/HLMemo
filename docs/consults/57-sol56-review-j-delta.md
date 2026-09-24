## Verdict (DO-NOT-MERGE)

D-077 hard prerequisite’i hâlâ sağlanmıyor; ayrıca bağımsız bir replay sapması var. Statik inceleme yapıldı; test/değişiklik yok. Yeni observer mutation veya scope leak bulmadım.

## PROMOTION-READY (no)

## Per-finding

1. **FIXED** — Önceki scope-churn dizisi: `roles.py:116-202`, `write_service.py:1247-1262`, `worker.py:1174-1222`.
2. **FIXED** — Önceki batch yarışı; batch kilitleri event-id’den önce: `roles.py:303-309`, `worker.py:967-993,1061-1067`.
3. **PARTIAL** — Demotion artık `n_fetch` sonrası/statement-aware; false demotion ve cycle sorunu sürüyor: `read_service.py:255-262`, `supersession.py:82-128`.
4. **PARTIAL** — Terminal event tekilleşti, fakat consumed retry başına `defer` eventi sürüyor; literal D-062 ihlal: `worker.py:555-568,593-619`.
5. **FIXED** — N=3 bağlantı zarfı hard-cap ve startup kontrolüyle korunuyor: `worker.py:197-254,287-347,1322-1335`.
6. **FIXED** — Re-plan tüm subject projelerini kapsıyor veya kayıtlı reddediliyor: `questions.py:357-413`, `worker.py:895-949`.

## New findings

| severity | file:line | trigger | fix |
|---|---|---|---|
| High | `worker.py:809-817,991-992` | Observer’da kabul edilen `widen_scope`, promotion sonrası apply tarafından atlanır; sweeper sonsuza dek yeniden kuyruklar. | Yetkili widen apply yolu veya terminal supersede/refusal. |
| High | `worker.py:650,1066,1110`; `actor.py:377-387` | Aynı version’a iki signals-only job: event 10 geç commit edip event 11’i ezer; replay 11’i bırakır. | Signal subject’lerini event-id öncesi sıralı kilitle. |
| Medium | `worker.py:560-593`; `db/replay.py:293-298` | Systemic hand-back `run_after/last_error` değiştirir fakat event yazmaz; rebuild farklıdır. | Kaydet veya ADR ile non-authoritative yap. |
| Medium | `supersession.py:88-92`; `read_service.py:284-285` | Aynı cümlede valid clause eşleşmesi outdated quote’u yanlış demote edebilir. | Quote içi query-evidence/clause eşleşmesi kullan. |
| Medium | `supersession.py:116-128` | Cycle düğümleri unrelated hit’lerin sonuna taşınır ve bütçeden düşebilir. | Cycle edge’lerini yok say veya SCC sırasını koru. |