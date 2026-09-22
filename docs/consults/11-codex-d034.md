Altı kusur düzeltildi. Commit oluşturulmadı; `bake-astra` ve hacimleri `down -v` ile kaldırıldı.

- **D11 fixed:** [backup.sh:38](/Users/cemalkurt/Projects/HLMemo/deploy/backup/backup.sh:38) — ayrı pre-upgrade dizini, açık prune komutu (varsayılan 5), eşleşen ref/dump kaydı ve rollback talimatları.
- **D13 fixed:** [deploy.sh:148](/Users/cemalkurt/Projects/HLMemo/deploy/scripts/deploy.sh:148) — canlı yedek, nonfatal S3 hatası, fd `flock`; stop sonrası hatalarda önceki imaj/veritabanıyla kurtarma.
- **D01 fixed:** [cloud-init.yaml.tftpl:54](/Users/cemalkurt/Projects/HLMemo/deploy/terraform/hetzner/cloud-init.yaml.tftpl:54) — dizinler önce; `/run/sshd`, güvenli SSH yeniden yükleme ve hatada provisioning’e devam.
- **D09 fixed:** [compose.prod.yaml:123](/Users/cemalkurt/Projects/HLMemo/deploy/compose.prod.yaml:123) — üretimde host-IP kaldırıldı, UDP 443 eklendi; AAAA belgelendi.
- **D05 fixed:** [compose.prod.yaml:10](/Users/cemalkurt/Projects/HLMemo/deploy/compose.prod.yaml:10) — prod/app/api/db/backup env ayrımı; API sırları yalnız API’de, S3 hiçbir konteynerde değil.
- **D02 fixed:** [probe.py:93](/Users/cemalkurt/Projects/HLMemo/deploy/scripts/probe.py:93) — sabit `deploy-smoke`, mevcut projeyi kullanma, cihaz iptali. SQL proje sayısı: **1**.

Tüm kapılar geçti. Son çıktı satırları:

```text
G-D1: çıktı yok; exit 0
PASS G-D2: healthy stack; TLS ready=200, unknown=404; DB has no host ports and only internal networking
PASS G-D3: HTTPS initialize + tools/list returned all five memory tools; project deploy-smoke reused; device revoked
PASS G-D4: HTTPS memory.raw returned the identical payload after database wipe and restore
D13 PASS: failed S3 upload; live stack unchanged; local pre-upgrade dump valid; stale mkdir lock ignored
G-D5: Success! The configuration is valid.
G-D6: çıktı yok; exit 0
G-D7: 10:45PM INF no leaks found
```

G-D3 son iki çalışmada aynı sonucu verdi; Terraform fmt sessiz exit 0. D11 retention dâhil **18 test: `OK`**. Cloud-init: `Valid schema /tmp/cloud-init.yaml`.

Gerçek VPS açılışı, public IPv6/ACME, gerçek S3 ve SSH deployment doğrulanmadı. Otomatik rollback snapshot sonrası yazıları geri alır; RUNBOOK’ta açıklandı. Ayrıntılı kanıt: [GATE-RESULTS.md](/Users/cemalkurt/Projects/HLMemo/deploy/GATE-RESULTS.md).