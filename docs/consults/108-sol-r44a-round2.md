## Verdict

**GO-with-fixes.** HIGH kalmadı. Aşağıdaki iki MEDIUM düzeltilmeli veya son tur olduğu için owner tarafından açıkça kabul edilmeli.

1. **MEDIUM — R1 #3 PARTIAL:** Otomatik deploy recovery, eski Compose modelini başlatıp `edge` ağını sessizce kaybediyor; uyarı yalnız manuel rollback’te var ([rollback.sh:163](/private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/r44a-review2/deploy/scripts/rollback.sh:163), [remote-deploy.sh:161](/private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/r44a-review2/deploy/scripts/remote-deploy.sh:161), [RUNBOOK.md:1123](/private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/r44a-review2/deploy/RUNBOOK.md:1123)).
   **Senaryo:** ee89c86 üstüne deploy, writer’lar durduktan sonra hata → recovery başarılı görünür, Caddy ortak IPv6 bucket’a döner.
   **Minimal fix:** rendered rollback modelinde `edge` yoksa `remote-deploy.sh` aynı actionable uyarıyı versin; failed-deploy recovery testi ekleyin.

2. **MEDIUM — yeni RUNBOOK kusuru:** Son “kept” kontrolü yalnız `hlmemo.accepted_pending == 18` bakıyor ([RUNBOOK.md:642](/private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/r44a-review2/deploy/RUNBOOK.md:642), [test_runbook_withdraw.py:75](/private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/r44a-review2/tests/unit/test_runbook_withdraw.py:75)).
   **Senaryo:** `total=19`, `hlmemo={accepted_pending:18, approved:1}` veya başka projede 7 pending varken kontrol yanlışlıkla PASS basar. Gerçek promotion guard yine reddeder; bu yüzden HIGH değil.
   **Minimal fix:** `total == 18`, proje kümesi tam `{hlmemo}` ve sayımlar tam `{accepted_pending:18, approved:0}` şartlarını test edin.

3. **HIGH — R1 #1 CLOSED:** Batch kararı shared role kilidini question kilitlerinden önce alıyor ([roles.py:396](/private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/r44a-review2/src/hlmemo/librarian/roles.py:396)); promotion exclusive kilitte ([roles.py:89](/private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/r44a-review2/src/hlmemo/librarian/roles.py:89)). Apply/answer aynı sırada, expiry role kilidine dönmüyor; deadlock çevrimi yok. Home+recorded kapsamı gerçek kapsamın alt kümesi olduğundan guard yalnız fazla sayabilir ([roles.py:190](/private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/r44a-review2/src/hlmemo/librarian/roles.py:190)).

4. **HIGH — R1 #2 CLOSED:** Downgrade DDL/refusal tek migration transaction’ında ([0011_question_withdrawn.py:61](/private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/r44a-review2/alembic/versions/0011_question_withdrawn.py:61), [env.py:48](/private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/r44a-review2/alembic/env.py:48)); refusal/timeout `_v2` ve Alembic sürümünü birlikte geri alıyor.

5. **MEDIUM — R1 #4 CLOSED:** 298/18/280, ayrıklık ve tam kapsam kontrolleri mevcut ([RUNBOOK.md:608](/private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/r44a-review2/deploy/RUNBOOK.md:608)).

6. **MEDIUM — R1 #5 CLOSED:** Eski release readiness/head uyumsuzluğu ve post-dump veri kaybı doğru belgelenmiş ([RUNBOOK.md:580](/private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/r44a-review2/deploy/RUNBOOK.md:580)).

**Doğrulama:** 58 hedefli test geçti; withdraw/guard entegrasyonu 10/10, migration 0011 5/5, ayrıca rollback’in 2 alt vakası geçti. İzole test DB’si ve geçici `.venv` temizlendi.