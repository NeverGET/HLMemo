## Verification

| Kontrol | Tam özet satırı |
|---|---|
| a — ruff check | `All checks passed!` |
| a — format | `92 files already formatted` |
| b — unit/fixtures | `199 passed in 19.30s` |
| c — integration | `99 passed in 19.82s` |
| d — G3/G4 | `4 passed in 46.49s` |

**G3 Recall@5: 0.930 ≥ 0.90. G4 p95: 289.8 ms ≤ 500 ms.**

`hlm_retr` öncesi/sonrası: **11.574 embedding**, korundu. (d) için silmeyi engelleyen geçici eklenti kullanıldı; donanım raporu `/tmp` altına yönlendirildi.

## N1 / N2 / N3: fixed

- **N1 — [middleware.py:124](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/server/middleware.py:124):** SSE başlıklarından önce commit, ardından doğrudan akış; ping yarışı ve SDK’nin yuttuğu commit hatası korunuyor. Kanıt: [test_mcp_sse_commits_before_headers_and_streams](/Users/cemalkurt/Projects/HLMemo/tests/integration/test_g6_commit_ack.py:43) ve [test_mcp_post_is_durable_when_first_ack_message_is_sent](/Users/cemalkurt/Projects/HLMemo/tests/integration/test_g6_commit_ack.py:116).
- **N2 — [write_service.py:673](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/core/write_service.py:673):** Yeni olaylarda `resolved.hash_version=2`; retry kayıtlı veya tarihsel olarak belirlenen algoritmayla karşılaştırılıyor. Kanıt: [test_retry_uses_stored_hash_version](/Users/cemalkurt/Projects/HLMemo/tests/integration/test_g6_write.py:190), write/close için eski replay ve farklı payload çatışmasını doğruluyor.
- **N3 — [app.py:175](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/server/app.py:175):** Uygulama başına kilit; çağıran iptal edilse de çalışan doğrulama kilidi koruyor. Kanıt: [test_concurrent_cold_readiness_loads_models_once](/Users/cemalkurt/Projects/HLMemo/tests/integration/test_g1_boot.py:346): sekiz eşzamanlı çağrı, tek yükleme, yanlış 503 yok.

## How I distinguish finite vs streaming responses

`http.response.start` mesajındaki normalize edilmiş `Content-Type: text/event-stream` akışı belirtir.  
Mevcut diğer yanıtlar sonlu kabul edilir; `Transfer-Encoding: chunked` tek başına akış göstergesi değildir.  
SSE başlıklarından önce commit edilir; sonlu yanıtlar tamamlanıp commit başarılı olana kadar tamponlanır.

## Anything still broken or any contract question

- Tarihsel sınır: tüm varsayılanları açıkça içeren sürümsüz round-8 close, eski normalize close’dan ayırt edilemez; legacy davranışı korunur.
- Sözleşme veya DDL değişmedi. Kapsam dışındaki dosyalara dokunmadım.
- Commit yapılmadı. Öneri: `fix: preserve SSE delivery, legacy retries and readiness concurrency`