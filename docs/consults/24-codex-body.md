Kod uygulandı; commit yapılmadı, `deploy/` değişmedi.

- [middleware.py:141](/Users/cemalkurt/Projects/HLMemo-bake/fix-body/src/hlmemo/server/middleware.py:141): trusted-proxy IP/IPv6 `/64` anahtarı; istemci başına **16** okuyucu, fazlasına bütçe ayrılmadan retryable **429**.
- [middleware.py:514](/Users/cemalkurt/Projects/HLMemo-bake/fix-body/src/hlmemo/server/middleware.py:514): grace kaldırıldı; süre ihlalinde retryable **408**, yanıt gönderilmeden rezervasyon ve spool bırakılıyor.
- [config.py:160](/Users/cemalkurt/Projects/HLMemo-bake/fix-body/src/hlmemo/config.py:160), [app.py:380](/Users/cemalkurt/Projects/HLMemo-bake/fix-body/src/hlmemo/server/app.py:380): yapılandırılabilir uvicorn concurrency **512**, keep-alive **5 s** ve spool dizini.

Formül: `elapsed ≤ BODY_BASE_S + received_bytes / MIN_RATE`; varsayılanlar **30 s**, **8192 B/s**, inactivity **30 s**. Toplam tavan `30 + declared_or_max_bytes / 8192`: **38.400.000 byte için 4717,5 s**, 64 MiB için **8222 s**. Ayrı 9000 s ayarı kaldırıldı.

[Regresyon testleri](/Users/cemalkurt/Projects/HLMemo-bake/fix-body/tests/unit/test_final_body.py:415):

- `test_under_grace_trickle_times_out_and_releases_before_error_send`
- `test_seventeenth_body_read_gets_429_before_reservation`
- `test_1028_under_grace_connections_from_two_clients_cannot_hold_budget`
- `test_legitimate_38mb_write_at_64kib_per_second`
- `test_25_second_pause_mid_upload_survives`

Gerçek TCP D1 testi `test_declared_length_tricklers_do_not_block_real_38mb_write` da geçti.

Kabul çıktıları — R1, `hlm_body` iki tam koşu, ardından salt-okunur G3/G4:

```text
17 passed in 5.54s
524 passed, 4 skipped, 33 subtests passed in 835.90s (0:13:55)
524 passed, 4 skipped, 33 subtests passed in 247.24s (0:04:07)
4 passed in 46.33s
All checks passed!
120 files already formatted
```

G3 **Recall@5=0.930**, G4 **p95=280.8 ms**. Dört skip: canlı CLI/O2 opt-in testleri. `HARDWARE.md` özgün hash’iyle geri yüklendi.

Deploy: `HLM_REQUEST_SPOOL_DIR` hedefinde **320 MiB = 335.544.320 byte** tmpfs/volume ayırın (`size=320m`); varsayılan dizin sistem temp dizini.

**Kalan:** PHASE0-SPEC, açık dosya izin listesiyle çeliştiği ve ek izin yanıtı gelmediği için düzenlenmedi.