Deploy düzeltmeleri tamamlandı; commit yapılmadı. **Phase 0 kapanışı hâlâ engelli:** main, D-039 proxy desteğini içermediği için gerçek istemci-IP testi FAIL.

- **fixed:** [compose.prod.yaml:157](/Users/cemalkurt/Projects/HLMemo/deploy/compose.prod.yaml:157), [api.env.example:7](/Users/cemalkurt/Projects/HLMemo/deploy/api.env.example:7) — subnet, güvenilen proxy ve frontend’e özel rota; yapılandırma testleri PASS.
- **fixed:** [Caddyfile:29](/Users/cemalkurt/Projects/HLMemo/deploy/Caddyfile:29) — MCP 64MiB, REST 64KiB. Geçici uygulama sınırı yükseltilerek 5 MB’ın Caddy’den geçtiği doğrulandı; eski SDK 413 döndürdü.
- **fixed:** [remote-deploy.sh:186](/Users/cemalkurt/Projects/HLMemo/deploy/scripts/remote-deploy.sh:186) — protokol 3 şartı, SHA etiketi ve başlangıç öncesi kontroller; eski runner/yanlış etiket regresyonları PASS.
- **fixed:** [retention.py:3](/Users/cemalkurt/Projects/HLMemo/deploy/backup/retention.py:3) ve deploy Python dosyaları — lint/format temiz.
- **fixed:** [deploy.sh:31](/Users/cemalkurt/Projects/HLMemo/deploy/scripts/deploy.sh:31) — observer hatalarından önce newline; timeout testi PASS.
- **fixed:** [bootstrap.sh:109](/Users/cemalkurt/Projects/HLMemo/deploy/bootstrap.sh:109) — sağlayıcı bağımsız kurulum, dry-run, güvenli SSH devri; Ubuntu’da kurulum/tekrar çalıştırma/gerçek SSH/geri-alma PASS.
- **fixed:** [compose.prod.yaml:49](/Users/cemalkurt/Projects/HLMemo/deploy/compose.prod.yaml:49), [RUNBOOK.md:129](/Users/cemalkurt/Projects/HLMemo/deploy/RUNBOOK.md:129) — sürekli toplam 5,25 GiB, migration dahil 6 GiB; PostgreSQL değerleri çalışan konteynerde doğrulandı.

Kapıların son satırları:

```text
G-D1: (no output; exit 0)
PASS G-D2: healthy stack; TLS ready=200, unknown=404; DB has no host ports and only internal networking
PASS G-D3: HTTPS initialize + tools/list returned all five memory tools; project deploy-smoke reused; device revoked
PASS G-D4: HTTPS memory.raw returned the identical payload after database wipe and restore
G-D5: Success! The configuration is valid.
G-D6: (no output; exit 0)
G-D7: 12:25AM INF no leaks found
```

Unittest: **62 test, OK**. Ruff: `All checks passed!` / `25 files already formatted`. Terraform fmt ve shellcheck temiz. Stdin fake-SSH:

```text
Deployment ready: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb (localhost)
BOOTSTRAP_CONTAINER_CHECKS_OK (systemctl mocked; firewall inactive)
BOOTSTRAP_RERUN_AND_ROLLBACK_OK
```

Systemd denemesi mount izinlerinden başarısız oldu. Gerçek VPS’de systemd/Docker daemon, aktif firewall, fail2ban/unattended-upgrades, ACME/IPv6/S3 doğrulanmadı.

`bake-astra down -v` tamamlandı; geçici konteyner/volume kalmadı. Ayrıntılı kanıt: [FINAL-GATE-RESULTS.md](/Users/cemalkurt/Projects/HLMemo/deploy/FINAL-GATE-RESULTS.md).