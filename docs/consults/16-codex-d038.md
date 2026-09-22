Altı madde düzeltildi. Commit atılmadı; `src/**` ve diğer worktree’ye dokunulmadı.

- **N1 fixed:** [remote-deploy.sh:156](/Users/cemalkurt/Projects/HLMemo/deploy/scripts/remote-deploy.sh:156) — SHA bazlı immutable image ve başarılı cutover sonrası atomik env güncellemesi. Test: B başarısız → restore → A image’ı ve A Alembic’i.
- **N2 fixed:** [deploy.sh:95](/Users/cemalkurt/Projects/HLMemo/deploy/scripts/deploy.sh:95) — runner sunucuda hedef SHA’dan alınıyor. Testler: doğru runner, runner içermeyen ref’in erken reddi, eski runner kilit uyumu.
- **N3 fixed:** [deploy.sh:35](/Users/cemalkurt/Projects/HLMemo/deploy/scripts/deploy.sh:35) — PID/heartbeat, ayrı komut kontrolleri, SSH dahil varsayılan 1800 saniye timeout. SIGKILL, setsid hatası ve takılan SSH testleri geçti.
- **N4 fixed:** [remote-deploy.sh:121](/Users/cemalkurt/Projects/HLMemo/deploy/scripts/remote-deploy.sh:121) — Compose modeli değişmişse build/backup/stop öncesi reddediliyor; aşamalı geçiş RUNBOOK’ta. Yan etkisiz ret testi geçti.
- **5 fixed:** [backup.sh:42](/Users/cemalkurt/Projects/HLMemo/deploy/backup/backup.sh:42) — benzersiz isimler; retention korunuyor. Aynı saniyede farklı upload anahtarları ve doğru son yedeğin tutulması doğrulandı.
- **6 fixed:** [tests/deploy](/Users/cemalkurt/Projects/HLMemo/tests/deploy) — assertion’lar korunarak Ruff borcu temizlendi.

Son kapı satırları:

```text
G-D1: (no output; exit 0)
PASS G-D2: healthy stack; TLS ready=200, unknown=404; DB has no host ports and only internal networking
PASS G-D3: HTTPS initialize + tools/list returned all five memory tools; project deploy-smoke reused; device revoked
PASS G-D4: HTTPS memory.raw returned the identical payload after database wipe and restore
G-D5 validate: Success! The configuration is valid.
G-D5 fmt: (no output; exit 0)
G-D6: (no output; exit 0)
G-D7: 11:30PM INF no leaks found
Ran 47 tests in 80.385s
OK
All checks passed!
7 files already formatted
```

Fake-SSH sonuçları:

```text
Deployment ready: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb (localhost)
PASS failed B after build -> restore.sh: running=sha256:old-image; alembic=sha256:old-image (release A)
```

[15 yeni testin listesi ve doğrulama raporu](/Users/cemalkurt/Projects/HLMemo/deploy/D038-GATE-RESULTS.md:73).

`bake-astra down -v` tamamlandı; konteyner/hacim kalmadı. Gerçek VPS/SSH daemon, OOM, DNS/ACME/IPv6, systemd ve gerçek S3 doğrulanmadı. Mevcut sunucularda atomik env yazımı için RUNBOOK’taki `/etc/hlmemo` sahiplik adımı gerekli.

Commit önerisi: `fix: harden deploy release recovery and observation`