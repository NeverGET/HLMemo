Tamamlandı; değişiklikler commit edilmeden bırakıldı.

`deploy/` altında Compose, Caddy, env örneği, backup/restore ve timer, Terraform/cloud-init, SSH deploy, TLS/MCP smoke ve restore drill scriptleri oluşturuldu. Makefile’a `deploy-*` hedefleri eklendi. [Runbook](/Users/cemalkurt/Projects/HLMemo-bake/astra-r2/deploy/RUNBOOK.md) ve [doğrulama kaydı](/Users/cemalkurt/Projects/HLMemo-bake/astra-r2/deploy/GATE-RESULTS.md) hazır.

**G-D1–G-D7 geçti:**

| Gate | Çalıştırılan komut | Son çıktı |
|---|---|---|
| G-D1 | `docker compose -f deploy/compose.prod.yaml --env-file deploy/.env.prod.example config -q` | Çıktı yok; exit 0 |
| G-D2 | `HLM_ENV_FILE=/private/tmp/hlmemo-bake-astra.env bash deploy/scripts/smoke_tls.sh` | `PASS G-D2: healthy stack; TLS ready=200, unknown=404; DB has no host ports and only internal networking` |
| G-D3 | `HLM_ENV_FILE=/private/tmp/hlmemo-bake-astra.env bash deploy/scripts/smoke_mcp.sh` | `PASS G-D3: HTTPS initialize + tools/list returned all five memory tools using an approved device` |
| G-D4 | `HLM_ENV_FILE=/private/tmp/hlmemo-bake-astra.env HLM_ALLOW_DESTRUCTIVE_DRILL=1 bash deploy/scripts/drill_backup_restore.sh` | `PASS G-D4: HTTPS memory.raw returned the identical payload after database wipe and restore` |
| G-D5 | `terraform -chdir=deploy/terraform/hetzner init -backend=false && terraform -chdir=deploy/terraform/hetzner validate && terraform fmt -check -recursive deploy/terraform` | `Success! The configuration is valid.` |
| G-D6 | `shellcheck deploy/**/*.sh` | Çıktı yok; exit 0 |
| G-D7 | `gitleaks dir deploy --no-banner` | `9:52PM INF no leaks found` |

Ek olarak, boyut sınırını aşan authenticated MCP isteği **413** döndürdü.

`bake-astra` yığını `down -v` ile kaldırıldı; geçici test env/yedekleri silindi. Dev yığınına dokunulmadı.

Gerçek VPS/cloud-init, SSH deployment, public ACME issuance, S3 yükleme ve Linux timer çalışması doğrulanmadı. Uzun süreli SSE akışı ayrıca test edilmedi; mevcut MCP uygulaması JSON yanıtlıyor. Cloud kaynağı veya commit oluşturulmadı.

Önerilen commit mesajı: `feature: add production deployment tooling`