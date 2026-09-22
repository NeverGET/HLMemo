## Verification results

- **a:** `All checks passed!` / `90 files already formatted`
- **b:** `199 passed in 18.90s`
- **c:** `83 passed in 17.37s`
- **d:** `8 passed in 148.32s (0:02:28)`
  - **G3 Recall@5: 0.930**
  - **G4 p95: 273.2 ms**
  - G2: 1000 çağrı, sıfır bütçe aşımı.
- **e:** `docker compose config -q`: çıktı yok, exit **0**. Build: `Image hlmemo:dev Built` **iki kez**, exit **0**.

Ek doğrulama: `Image hlmemo:test Built`; image içinde `pytest 9.1.1`. Ağ ve host mount’u olmadan model hashleri, ONNX inference ve tokenizer kontrolü **PASS**. `hlm_retr` içindeki **11.574 embedding korundu**.

## D-027 item status table

| Item | Status | Proving test or gap |
|---|---|---|
| S1 | VERIFIED | `test_raw_survivor_does_not_leak_restricted_correction` |
| S1b | VERIFIED | `test_payload_item_links_filtered_by_endpoint_authz`; cursor sayfaları dahil |
| C4 | VERIFIED | `test_stale_chunk_scope_cannot_hide_authorized_version`; G3/G4 |
| C2 | VERIFIED | `test_rebuild_with_forward_link_reference`, `test_rebuild_with_cyclic_batch_links` |
| C3 | VERIFIED | `test_rebuild_identical_after_spanning_correction` |
| S2 | VERIFIED | `test_link_to_hidden_target_uniform_not_found` |
| C5 | VERIFIED | `test_verbatim_idempotency_over_mcp`; write ve call_the_day |
| C1 | VERIFIED | `test_mcp_commit_failure_rolls_back_before_ack`; ayrıca iptal testi |
| C6 | VERIFIED | `test_stale_lease_rolls_back_copied_vectors_then_new_owner_completes` |
| S4 | VERIFIED | Render edilmiş Compose JSON: tüm DB portlarında `host_ip == 127.0.0.1` |
| O1 | VERIFIED | Readiness bağımlılık testleri, modeller dahil build, offline inference/tokenizer testi |
| O2 | WEAK | `unless-stopped` doğrulandı; gerçek crash/restart testi ve ilerleme izlemesi yok |
| O3 | VERIFIED | Test image build’i, pytest smoke ve altı güvenli DB-bootstrap testi |

## What I changed to finish the work

- [write_service.py:189](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/core/write_service.py:189): `payload.request` ve hash artık aynı verbatim isteği koruyor.
- [handlers.py:54](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/server/tools/handlers.py:54): Her iki yazma handler’ı `raw=args` geçiriyor.
- [replay.py:85](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/db/replay.py:85): Replay, normalize edilmiş değerleri `resolved.write.items` üzerinden kullanıyor.
- [read_service.py:460](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/core/read_service.py:460): Keyfî 64-adım sınırı yerine döngü tespiti.
- [read_queries.py:239](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/db/read_queries.py:239): GIN kullanımını koruyan sorgu sınırı; yakalanan **5279.4 ms** p95 regresyonu giderildi.
- [test_g5_read_leaks.py:197](/Users/cemalkurt/Projects/HLMemo/tests/integration/test_g5_read_leaks.py:197): Yetkili writer, doğru proje beklentileri ve geçerli sayfalama bütçesi.
- [middleware.py:108](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/server/middleware.py:108): Sonlu yanıtların tamamı dış commit sonrasına erteleniyor.
- [app.py:129](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/server/app.py:129): Readiness hash bütünlüğünü, inference ve tokenizer’ı doğruluyor.
- [Dockerfile:27](/Users/cemalkurt/Projects/HLMemo/Dockerfile:27): Tokenizer cache’i ve varsayılan model bake’i.
- [compose.yaml:17](/Users/cemalkurt/Projects/HLMemo/compose.yaml:17): Baked modelleri örten host mount’ları kaldırıldı.
- [bootstrap_test_db.py:17](/Users/cemalkurt/Projects/HLMemo/tests/bootstrap_test_db.py:17): Yalnız `hlm_verify` için güvenli oluşturma.
- [test_worker_fencing.py:19](/Users/cemalkurt/Projects/HLMemo/tests/integration/test_worker_fencing.py:19): İstenmeyen survivor oluşturan test tarihleri düzeltildi.
- Mevcut lint/format hataları temizlendi; donmuş fixture çıktısının birebir korunduğu test edildi.

## Remaining defects or contract questions for the orchestrator

- O2’nin crash/restart ve ilerleme izleme açığı sürüyor.
- S4 çalışan DB container’ına uygulanmadı; yapılandırma doğrulandı. API/worker başlatılmadı, G7 yeniden çalıştırılmadı.
- Yeni sözleşme kararı gerekmiyor. Commit/stash/reset yapılmadı. Ayrıntılı kanıt: [round-8 raporu](/Users/cemalkurt/Projects/HLMemo/docs/consults/08-codex-finish-verification.md).