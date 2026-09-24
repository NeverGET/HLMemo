## Verdict (DO-NOT-MERGE)

Replay ayrışması ve geçerli içeriğin sonuçtan düşmesi hâlâ mümkün. Yalnızca statik inceleme yapıldı; test veya dosya değişikliği yapılmadı. Yeni observer kullanıcı-verisi mutasyonu, scope sızıntısı veya write path ile somut deadlock döngüsü bulmadım.

Aşağıdaki yollar `src/hlmemo/` altındadır.

## PROMOTION-READY

**no**

## Per-finding

1. **FIXED — Scope-churn stranding:** `librarian/roles.py:157`, `librarian/worker.py:1174`: genel yeniden değerlendirme, revision tetikleyicisi ve sweeper hedeflenen boşluğu kapatıyor.
2. **PARTIAL — Concurrent replay:** `librarian/worker.py:1066`: batch sıralaması düzelmiş; signal-only işler hâlâ ters sırayla yazabiliyor.
3. **PARTIAL — Partial supersession:** `core/supersession.py:92`: zincir sıralaması düzelmiş; aynı cümledeki geçerli bilgi yanlışlıkla demote edilebiliyor.
4. **PARTIAL — Tek event/job:** `librarian/worker.py:593`: terminal event tekilleştirilmiş; nonterminal `defer` event’leri D-062’nin literal koşulunu hâlâ ihlal ediyor. ADR değişikliği gerekiyor.
5. **FIXED — Connection envelope:** `librarian/worker.py:197`, `:363`, `:1323`: başlangıç kontrolü, sınırlı bağlantılar ve N büyüklüğündeki ledger pool zarfı koruyor; ölçümler yeniden çalıştırılmadı.
6. **FIXED — Cross-project re-plan:** `librarian/questions.py:388`, `librarian/worker.py:919`: capability kapsamı genişletilmiş; eksik yetki açıkça kaydediliyor.

## New findings

| severity | file:line | trigger | fix |
|---|---|---|---|
| High | `librarian/worker.py:650`; `librarian/actor.py:383` | Aynı version için iki signal-only re-plan: J1 E1’i ayırır, bekler; J2 E2’yi yazıp commit eder; J1 üzerine yazar. Canlı son değer E1, replay’de E2 olur. | Signal hedeflerini de event ID ayrılmadan önce tutarlı, sıralı kilitlere dahil et. |
| High | `core/supersession.py:92`; `core/retrieval.py:449` | Metin “API uses port 8080 and backups retain 30 days”, eski alıntı “API uses port 8080”, sorgu “backups retain”. Cümle alıntıyı içerdiğinden geçerli bilgi demote edilir; token bütçesi sonucundan düşebilir. | Eşleşmeyi alıntının kendi span’inde doğrula; belirsiz veya karma eşleşmede demotion uygulama. |
| Medium | `librarian/worker.py:1197`; `:815` | Pending widening sweeper tarafından seçilir, apply tarafından atlanır; taze owner cevabı gelmezse TTL’ye kadar her 300 saniyede yeni job/event oluşur. | D-058 propose-only widening’i otomatik release’den çıkar; yeniden owner cevabı gereğini açıkça göster. |