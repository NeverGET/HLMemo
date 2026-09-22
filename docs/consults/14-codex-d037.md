15 madde düzeltildi. Commit yapılmadı; `deploy/`, `Makefile` ve `tests/deploy/` değiştirilmedi.

1. **fixed:** [write_service.py:668](/Users/cemalkurt/Projects/HLMemo-bake/fix-d037/src/hlmemo/core/write_service.py:668) — kilit öncesi yetkilendirme, sonrasında tekrar kontrol; gizli/olmayan hedef contention testleri.
2. **fixed:** [write_models.py:80](/Users/cemalkurt/Projects/HLMemo-bake/fix-d037/src/hlmemo/core/write_models.py:80) — kartlarda yalnız `device_scope="all"`; validation ve legacy-row testleri.
3. **fixed:** [write_queries.py:101](/Users/cemalkurt/Projects/HLMemo-bake/fix-d037/src/hlmemo/db/write_queries.py:101) — advisory beklemesi request bütçesini kullanıyor; conflict/replay/session testleri.
4. **fixed:** [write_service.py:770](/Users/cemalkurt/Projects/HLMemo-bake/fix-d037/src/hlmemo/core/write_service.py:770), [read_queries.py:477](/Users/cemalkurt/Projects/HLMemo-bake/fix-d037/src/hlmemo/db/read_queries.py:477) — eski kart kaynakları supersede ediliyor, bağlantılar zamansal filtrelenip sayfalanıyor; 30-close ve kayıpsız pagination testleri.
5. **fixed:** [write_service.py:780](/Users/cemalkurt/Projects/HLMemo-bake/fix-d037/src/hlmemo/core/write_service.py:780) — link survivor segmentleri kaydedilip replay ediliyor; spanning-correction testi.
6. **fixed:** [write_service.py:902](/Users/cemalkurt/Projects/HLMemo-bake/fix-d037/src/hlmemo/core/write_service.py:902) — survivor `last_access_at` kaydediliyor; eşzamanlı drilldown/replay testi.
7. **fixed:** [devices.py:121](/Users/cemalkurt/Projects/HLMemo-bake/fix-d037/src/hlmemo/server/devices.py:121) — fingerprint çakışmasında rastgele yeni kimlik; revoke/yeniden kayıt testi.
8. **fixed:** [middleware.py:68](/Users/cemalkurt/Projects/HLMemo-bake/fix-d037/src/hlmemo/server/middleware.py:68) — güvenilir proxy üzerinden IP, bounded TTL/LRU; proxy spoofing/eviction testleri.
9. **fixed:** [app.py:162](/Users/cemalkurt/Projects/HLMemo-bake/fix-d037/src/hlmemo/server/app.py:162) — ayrı readiness bağlantısı; dolu havuzda anonim/bearer testleri.
10. **fixed:** [app.py:297](/Users/cemalkurt/Projects/HLMemo-bake/fix-d037/src/hlmemo/server/app.py:297) — ayrılmış admin kapasitesi; flood altında revoke **10/10 başarılı**.
11. **fixed:** [middleware.py:172](/Users/cemalkurt/Projects/HLMemo-bake/fix-d037/src/hlmemo/server/middleware.py:172) — inactivity timeout ve 300 saniyelik toplam sınır; yavaş ilerleyen body testi.
12. **fixed:** [middleware.py:355](/Users/cemalkurt/Projects/HLMemo-bake/fix-d037/src/hlmemo/server/middleware.py:355) — REST hatalarında bağlantı korunuyor; aynı PostgreSQL PID testi.
13. **fixed:** [pool.py:26](/Users/cemalkurt/Projects/HLMemo-bake/fix-d037/src/hlmemo/db/pool.py:26), [middleware.py:164](/Users/cemalkurt/Projects/HLMemo-bake/fix-d037/src/hlmemo/server/middleware.py:164) — PG17 transaction timeout, 16 KiB kayıt sınırı, retryable 408; üçünün regresyonları.
14. **fixed:** [app.py:285](/Users/cemalkurt/Projects/HLMemo-bake/fix-d037/src/hlmemo/server/app.py:285) — başlangıçta model pin kontrolü, readiness `embed_config`; mismatch testleri.
15. **fixed:** [mcp_server.py:255](/Users/cemalkurt/Projects/HLMemo-bake/fix-d037/src/hlmemo/server/mcp_server.py:255) — desteklenen SDK ayarıyla ortak 64 MiB sınır; gerçek taşıma üzerinden maksimum Unicode testleri.

Kabul sonuçları, sırasıyla R1, tam süit ×2, Ruff, format ve G3/G4:

```text
17 passed in 5.28s
409 passed in 49.64s
409 passed in 48.61s
All checks passed!
100 files already formatted
4 passed in 45.87s
```

G3 **Recall@5=0.930**, G4 **p95=276.2 ms**. Tek koşu salt-okunur yapıldı; `HARDWARE.md` geri yüklendi.

Sözleşme: §3’te 50×64.000 korunarak SDK/uygulama sınırı 64 MiB oldu; kaçışlı Unicode’u karşılıyor. §1.1/§3/§4.4 bağlantı filtreleme/sayfalama güncellendi. [Doğrulama kaydı](/Users/cemalkurt/Projects/HLMemo-bake/fix-d037/docs/consults/14-d037-verification.md). Opus incelemesi bu turda yapılmadı.