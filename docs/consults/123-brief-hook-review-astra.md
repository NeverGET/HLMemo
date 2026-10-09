**NO-GO**

- **HIGH — `src/hlmemo/brief/fetch.py:377,390`:** Eski sunucu `superseded_by` alanını göndermiyorsa ve yerine geçen kaydın raw okuması deadline’a takılırsa, kısmi brief superseded kaydı gösteriyor. Yeniden üretildi: tam sonuç `[v21]`, `v20` → `superseded`; kısmi sonuç `[v20]`, `v21` → `unverified`. Eksik havuzda authoritative incoming bilgisi olmayan adayları göstermeyin.
- **MEDIUM — `src/hlmemo/cli/mcp_client.py:144`:** Gerçek `httpx2.ConnectTimeout` / `ReadTimeout`, builtin `TimeoutError` kontrolüne girmiyor; sonuç `timeout` yerine `error:network`. Ayrıca timeout için yasaklanan retry yoluna girebiliyor. Her iki exception ile doğrulandı.
- **MEDIUM — `src/hlmemo/cli/mcp_client.py:149`:** JSON hata zarfı içermeyen HTTP 503 yanıtı `error:server` yerine `error:network/HTTPStatusError` oluyor; kullanıcıya “server unreachable” gösteriliyor. 503 response ile doğrulandı.

Doğrulama: gerçek istemci cancellation testi ve iki yeni fetch testi doğrudan çalıştırılarak geçti. Standart pytest, zorunlu `HLM_TEST_DSN` bulunmadığından çalışmadı. Dosyalar değiştirilmedi.