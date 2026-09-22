Uygulandı; `fix/auth` üzerinde commit atılmadı.

- [middleware.py:269](/Users/cemalkurt/Projects/HLMemo-bake/fix-auth/src/hlmemo/server/middleware.py:269): kimliğe göre body sınırı ve ayrı, sınırlı admin kabul alanı.
- [middleware.py:535](/Users/cemalkurt/Projects/HLMemo-bake/fix-auth/src/hlmemo/server/middleware.py:535): kısa auth lease, tek kilitsiz SELECT ve iptal güvenli bağlantı iadesi.
- [app.py:373](/Users/cemalkurt/Projects/HLMemo-bake/fix-auth/src/hlmemo/server/app.py:373): eksik modellerde crash yerine readiness 503; sonradan tek paylaşılan embedder.
- [Caddyfile:35](/Users/cemalkurt/Projects/HLMemo-bake/fix-auth/deploy/Caddyfile:35): üç proxy’de `keepalive 4s`.
- [PHASE0-SPEC.md:349](/Users/cemalkurt/Projects/HLMemo-bake/fix-auth/docs/decisions/PHASE0-SPEC.md:349), [RUNBOOK.md:172](/Users/cemalkurt/Projects/HLMemo-bake/fix-auth/deploy/RUNBOOK.md:172): yeni davranış belgelendi.

Gate sırası: **16-slot kabul → normal pool ≤250 ms → `SET LOCAL statement_timeout='250ms'` + tek SELECT → bağlantıyı bırak → body/bütçe → authoritative in-transaction `resolve`.** Trusted: 64 MiB/rate formülü; register: 16 KiB; diğer ungated istekler: 64 KiB. Admin rezerv yolu korunuyor.

Doğrulanan testler:

- `test_burst_then_trickle_without_trusted_bearer_never_reads_body`
- `test_gate_one_short_select_releases_connection_before_body`
- `test_revoke_between_gate_and_transaction_is_rejected`
- `test_declared_length_tricklers_do_not_block_real_38mb_write`
- `test_legitimate_38mb_write_at_64kib_per_second`
- `test_missing_models_lifespan_serves_not_ready_with_reason`

Tam suite **bir kez** koşuldu. Bulduğu yedi başarısızlık giderildi; tamamını kapsayan nihai hedefli koşu geçti.

```text
R1: 17 passed in 9.13s
Full: 7 failed, 554 passed, 4 skipped, 41 subtests passed in 3134.09s (0:52:14)
Final targeted: 81 passed in 46.95s
All checks passed!
127 files already formatted
[G3] overall: Recall@5 = 0.930 (93/100)
G4 p95=268.7 ms
4 passed in 46.01s
Ran 62 tests in 92.667s
OK
```

G3/G4 `hlm_retr` üzerinde salt okunur çalıştırıldı. `HARDWARE.md` aynen geri yüklendi.