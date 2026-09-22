Yalnızca `fix/f15` worktree’sinde `src/**` ve `tests/**` değişti. Commit atılmadı.

- **fixed:** [middleware.py:286](/Users/cemalkurt/Projects/HLMemo-bake/fix-f15/src/hlmemo/server/middleware.py:286) — bütçe yalnız gelen baytlarla artıyor; büyük beyan hemen 413. Test: 70 saldırgan bağlantı sırasında gerçek 38,4 MB yazma **200**, **50 sürüm**.
- **fixed:** [config.py:163](/Users/cemalkurt/Projects/HLMemo-bake/fix-f15/src/hlmemo/config.py:163) — inactivity, ortalama hız ve toplam süre ayrıldı; 408 retryable kaldı. Test: ilk bayt beklemesi, duraklama, grace ve toplam süre regresyonları geçti.
- **fixed:** [middleware.py:292](/Users/cemalkurt/Projects/HLMemo-bake/fix-f15/src/hlmemo/server/middleware.py:292) — eşik aşılmadan diske geçiş, 64 KiB replay ve iptal sırasında güvenli dosya temizliği. Test: 12 istemci × 4 MiB için tracemalloc peak **13,28 MiB**; tek 38,4 MB parçada ek peak **0,14 MiB**.
- **fixed (R1 ortamı):** [config.py:175](/Users/cemalkurt/Projects/HLMemo-bake/fix-f15/src/hlmemo/config.py:175) — boş proxy varsayılanı korundu; repro dosyaları değiştirilmedi. Açık proxy ayarıyla 17 test geçti.

Kabul sonuçları:

- **17 R1 repro pass.**
- **Full suite on hlm_final twice:** iki koşuda da **465 passed, 4 skipped**; aynı 469 test, G2 dahil. Atlananlar üç canlı CLI ve opt-in worker restart testi.
- **ruff check + format --check on src tests/unit tests/integration tests/fixtures clean.**
- **G3/G4 once read-only:** Recall@5 **0,930**, p95 **265,6 ms**; `HARDWARE.md` değişmedi.
- **D1 abuse scenario:** 70 × declared 4 MiB, 1 byte / 5 s eşzamanlı 38 MB yazmayı engellemedi.

Yeni/değişen varsayılanlar:

```text
HLM_REQUEST_BODY_TIMEOUT_S=30
HLM_REQUEST_BODY_TOTAL_TIMEOUT_S=9000
HLM_REQUEST_BODY_MIN_RATE_BYTES_S=8192
HLM_REQUEST_BODY_RATE_GRACE_BYTES=262144
HLM_REQUEST_BODY_SPOOL_THRESHOLD_BYTES=1048576
```

Taban hızda 38,4 MB **4687,5 s**, 64 MiB **8192 s** sürer; ikisi de 9000 saniyeye sığar.

R1’de kullanılan ortam:

```sh
export HLM_MODELS_DIR=/Users/cemalkurt/Projects/HLMemo/models
PYTHONPATH=src:. HLM_TRUSTED_PROXY_IPS=172.18.0.0/16 HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_r1judge
```

Commit önerisi: `fix: bound body buffering and tolerate slow uploads`