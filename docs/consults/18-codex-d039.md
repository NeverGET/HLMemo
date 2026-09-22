Tamamlandı; commit atılmadı. `deploy/`, `Makefile`, `tests/deploy/` değişmedi.

- **N1 fixed:** [app.py:164](/Users/cemalkurt/Projects/HLMemo-bake/fix-d039/src/hlmemo/server/app.py:164) — single-flight, 1 saniye cache ve semaphore; test: 500 eşzamanlı `/ready`, pencere başına tek bağlantı, havuzlar etkilenmiyor.
- **N3 fixed:** [middleware.py:335](/Users/cemalkurt/Projects/HLMemo-bake/fix-d039/src/hlmemo/server/middleware.py:335) — reserved pool yalnız constant-time admin eşleşmesine açık; test: iki revoke rotasında saldırı altında **10/10** başarı, eşzamanlı self-revoke doğrulandı.
- **N2 fixed:** [middleware.py:140](/Users/cemalkurt/Projects/HLMemo-bake/fix-d039/src/hlmemo/server/middleware.py:140) — 256 MiB global/128 MiB istemci bütçesi, minimum hız, retryable 503 ve cleanup; test: trickle saldırısı, iptal/hata yolları ve SDK üzerinden **38,4 MB** yazma.
- **N4 fixed:** [config.py:172](/Users/cemalkurt/Projects/HLMemo-bake/fix-d039/src/hlmemo/config.py:172) — varsayılan proxy güveni boş; test: geçersiz CIDR açıklayıcı readiness 503 üretiyor.
- **N5 fixed:** [write_queries.py:210](/Users/cemalkurt/Projects/HLMemo-bake/fix-d039/src/hlmemo/db/write_queries.py:210) — yalnız düzeltme aralığıyla kesişen bağlantılar yükleniyor; test: 30 revizyon, tarihsel raw, backdated survivors ve birebir replay.
- **Spec fixed:** [PHASE0-SPEC.md:288](/Users/cemalkurt/Projects/HLMemo-bake/fix-d039/docs/decisions/PHASE0-SPEC.md:288) — survivor/fingerprint payload’ları, cursor alanları, ayarlar ve edge limiti eşitlendi; bağımsız kod/spec incelemesi tamamlandı.
- **D-037 fixed:** [DECISIONS.md:42](/Users/cemalkurt/Projects/HLMemo-bake/fix-d039/docs/decisions/DECISIONS.md:42) — durum **PROPOSED**; kayıt doğrulandı.

Kabul sonuçları:

```text
R1: 17 passed in 5.35s
hlm_d039 #1: 437 passed, 4 skipped in 756.11s
hlm_d039 #2: 437 passed, 4 skipped in 156.16s
ruff check: All checks passed!
ruff format --check: 105 files already formatted
G3 Recall@5 = 0.930
G4 p95 = 274.3 ms
G3/G4: 4 passed in 45.84s
```

G2 dahil; dört skip canlı G7×3 ve opt-in O2×1. G3/G4 bir kez read-only çalıştırıldı; `HARDWARE.md` birebir geri yüklendi.

Deploy tarafında uygulanacak kesin değerler:

- Compose `frontend` subnet: **`172.30.39.0/24`**
- API: **`HLM_TRUSTED_PROXY_IPS=172.30.39.0/24`**
- Caddy `request_body` içinde: **`max_size 64MiB`** — `67108864` bayt.

[Doğrulama kaydı](/Users/cemalkurt/Projects/HLMemo-bake/fix-d039/docs/consults/18-d039-source-followup.md). Önerilen commit mesajı: `fix: bound readiness and request resource usage`.