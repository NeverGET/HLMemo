Tamamlandı; commit atılmadı.

**Çift model kurulumu doğrulandı:** readiness geçici ONNX modeli, query yolu ikinci model oluşturuyordu. Düzeltmeden sonra 1536 MiB smoke geçti: **20 query, OOMKilled=false, RestartCount=0**; Docker stats tepesi **956.2 MiB**, cgroup tepesi **1536 MiB**. Çift kurulum ile arena kullanımının ayrı katkıları A/B ölçülmedi. Test konteynerleri kaldırıldı.

Değişiklikler:

- [app.py:352](/Users/cemalkurt/Projects/HLMemo-bake/fix-body/src/hlmemo/server/app.py:352), [mcp_server.py:200](/Users/cemalkurt/Projects/HLMemo-bake/fix-body/src/hlmemo/server/mcp_server.py:200): lifespan’da tek embedder; readiness ve query paylaşımı.
- [embedder.py:187](/Users/cemalkurt/Projects/HLMemo-bake/fix-body/src/hlmemo/core/embedder.py:187), [config.py:143](/Users/cemalkurt/Projects/HLMemo-bake/fix-body/src/hlmemo/config.py:143), [worker/main.py:592](/Users/cemalkurt/Projects/HLMemo-bake/fix-body/src/hlmemo/worker/main.py:592): CPU arena kapalı; configurable thread default **2**.
- [compose.prod.yaml:80](/Users/cemalkurt/Projects/HLMemo-bake/fix-body/deploy/compose.prod.yaml:80), [RUNBOOK.md:140](/Users/cemalkurt/Projects/HLMemo-bake/fix-body/deploy/RUNBOOK.md:140): **320m spool tmpfs**, API **2560m**.
- [PHASE0-SPEC.md:349](/Users/cemalkurt/Projects/HLMemo-bake/fix-body/docs/decisions/PHASE0-SPEC.md:349): body-read formülü, admission/deadline davranışı, ayarlar ve ortak embedder kuralı.

Boyutlandırma: `(1536 + 320 + 64) × 1.25 = 2400 MiB ≤ 2560 MiB`. DB/API/worker/migration/Caddy limitleri: **2048/2560/1536/768/256 MiB**. Toplam **7 GiB**, sürekli servisler **6.25 GiB**; decimal 8 GB’a sığıyor.

Kabul sonuçları:

```text
R1: 17 passed in 8.48s
hlm_body normal suite: 479 passed in 103.92s (0:01:43)
[G3] overall: Recall@5 = 0.930 (93/100)
G4: p95=274.0 ms; 4 passed in 45.64s
ruff check: All checks passed!
ruff format --check: 125 files already formatted
deploy unittest: Ran 62 tests in 91.089s — OK
compose example config -q: exit 0
```

G3/G4 bir kez salt okunur çalıştı; `HARDWARE.md` birebir geri yüklendi. Komutlar, suite kapsamı ve ölçüm sınırları [doğrulama kaydında](/Users/cemalkurt/Projects/HLMemo-bake/fix-body/tests/OOM-VERIFICATION.md).