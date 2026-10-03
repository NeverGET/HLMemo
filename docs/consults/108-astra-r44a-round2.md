## Verdict

**GO-with-fixes.** Astra low ve Sol xhigh incelemeleri aynı sonuca ulaştı: yeni HIGH veya veri/migration engeli bulunmadı; #3 sahibin kararına kalıyor.

1. **HIGH 1 — CLOSED.** `src/hlmemo/librarian/roles.py:399`: batch approval, soru kilitlerinden önce shared role lock alıyor; promotion sayımı ile commit arasına giremiyor. İki bağlantılı regresyon testi: `tests/integration/test_librarian_withdraw.py:509`. Ek düzeltme gerekmiyor.

2. **HIGH 2 — CLOSED.** `alembic/versions/0011_question_withdrawn.py:61` ve `:90`, `alembic/env.py:49`: downgrade, constraint değişiklikleri ve Alembic sürüm güncellemesi aynı transaction’da. Refusal/timeout committed `_v2` bırakmıyor. Contention testi: `tests/integration/test_migration_0011.py:161`. Ek düzeltme gerekmiyor.

3. **MEDIUM 3 — PARTIAL.** `deploy/scripts/rollback.sh:165` uyarıyor; `deploy/RUNBOOK.md:1123` manuel edge restorasyonunu tarif ediyor. Ancak `deploy/scripts/remote-deploy.sh:202` otomatik recovery’de eski modeli **edge uyarısı olmadan** başlatıyor; yeniden uygulamaya kadar IPv6 istemcileri ortak limit kovasına dönüyor. **Minimal fix:** otomatik recovery’ye aynı uyarıyı eklemek; manuel restorasyona kadar kalan riski owner açıkça kabul etmeli veya edge korunmalı.

4. **MEDIUM 4 — CLOSED.** `deploy/RUNBOOK.md:608`: 298/18/280 sayıları, ayrıklık, tam kapsama ve preview doğrulanıyor; eski “2 real” yönlendirmesi kaldırılmış. ID kontrolü beş bellek içi vakada beklenen sonucu verdi. Ek düzeltme gerekmiyor.

5. **MEDIUM 5 — CLOSED.** `deploy/RUNBOOK.md:580`: eski release’in readiness kontrolünü geçemeyeceği ve dump rollback’in sonraki bütün yazıları kaybettireceği açık. Ek düzeltme gerekmiyor.

Kilit regresyonu görülmedi: apply shared lock’u önce alıyor (`worker.py:1066`); expiry role lock almıyor ve `SKIP LOCKED` kullanıyor (`questions.py:499`); promotion soruları kilitlemiyor. Guard’ın home+recorded kümesi (`roles.py:200`) apply kapsamının alt kümesi: değişiklik yalnız fazla sayabilir. Downgrade validation boyunca exclusive tablo kilidi tutuyor; bu belgelenmiş operasyonel maliyet.

**Doğrulama sınırı:** PostgreSQL yarış/migration testleri incelendi, yeniden çalıştırılmadı; ortamda test bağımlılıkları ve `HLM_TEST_DSN` yok. Consult 107’nin tam threat model/rubric metni arşivde bulunmadığından kapsam verilen bulgulara dayandırıldı.