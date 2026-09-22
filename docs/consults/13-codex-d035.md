Düzeltmeler tamamlandı; commit atılmadı. `bake-astra` için `down -v` tamamlandı, konteyner/volume kalmadı.

1. **fixed:** [deploy.sh:23](/Users/cemalkurt/Projects/HLMemo/deploy/scripts/deploy.sh:23) — betik dosya olarak yükleniyor; çocuk süreçlerin stdin’i kapalı. Stdin okuyan pg_dump/migrate ile ilk kurulum ve upgrade regresyonu geçti.
2. **fixed:** [restore.sh:49](/Users/cemalkurt/Projects/HLMemo/deploy/backup/restore.sh:49) — restore sonrasında migration zorunlu; başarısızsa writer’lar kapalı. Eski şema ve migration hatası testleri geçti.
3. **fixed:** [common.sh:45](/Users/cemalkurt/Projects/HLMemo/deploy/scripts/common.sh:45) — eksik environment güvenli işleniyor. Boş ve yorum-only dosyalar gerçek Compose ile test edildi.
4. **fixed:** [remote-deploy.sh:173](/Users/cemalkurt/Projects/HLMemo/deploy/scripts/remote-deploy.sh:173) — API/Caddy iç kontrolü ayrı; dış HTTPS hatası rollback yapmıyor. İki iç hata ve dış hata yolları geçti.
5. **fixed:** [deploy.sh:32](/Users/cemalkurt/Projects/HLMemo/deploy/scripts/deploy.sh:32), [remote-deploy.sh:9](/Users/cemalkurt/Projects/HLMemo/deploy/scripts/remote-deploy.sh:9) — detached çalışma, log/status, EXIT temizliği. İstemci süreç grubunu öldürme, SSH gözlemci hatası ve erken hazırlık hatası testleri geçti.
6. **fixed:** [remote-deploy.sh:120](/Users/cemalkurt/Projects/HLMemo/deploy/scripts/remote-deploy.sh:120) — rollback yeni env düzeniyle render ediliyor, eski image ID’leri sabitleniyor. Legacy-layout ve eksik konteyner testleri geçti.
7. **fixed:** [common.sh:54](/Users/cemalkurt/Projects/HLMemo/deploy/scripts/common.sh:54) — varsayılan `/var/backups/hlmemo`; repo içi/symlink/safety hedefleri reddediliyor. Üç path regresyonu geçti.
8. **fixed:** [cloud-init:67](/Users/cemalkurt/Projects/HLMemo/deploy/terraform/hetzner/cloud-init.yaml.tftpl:67) hatalı drop-in’i siliyor; [RUNBOOK:124](/Users/cemalkurt/Projects/HLMemo/deploy/RUNBOOK.md:124) kesin IPv6 ayarlarını belgeliyor; [remote-deploy.sh:180](/Users/cemalkurt/Projects/HLMemo/deploy/scripts/remote-deploy.sh:180) markerları koruyor, başarılı deploy sonrası son 5 dump’ı tutuyor. Cloud-init, retention ve recovery testleri geçti.

**32/32 test geçti.** [14 yeni testin tam listesi ve kanıtlar](/Users/cemalkurt/Projects/HLMemo/deploy/GATE-RESULTS.md:77).

Kapıların son dolu satırları:

```text
G-D1: çıktı yok; exit 0
G-D2: PASS G-D2: healthy stack; TLS ready=200, unknown=404; DB has no host ports and only internal networking
G-D3: PASS G-D3: HTTPS initialize + tools/list returned all five memory tools; project deploy-smoke reused; device revoked
G-D4: PASS G-D4: HTTPS memory.raw returned the identical payload after database wipe and restore
G-D5: Success! The configuration is valid.
G-D5 fmt / G-D6: çıktı yok; exit 0
G-D7: 11:10PM INF no leaks found
Tests: Ran 32 tests in 47.561s / OK
Stdin: Ran 1 test in 3.476s / OK
Fake SSH E2E: Deployment ready: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb (localhost)
```

Gerçek VPS olmadan SSH daemon kopması, cloud-init/ssh.socket, public DNS/ACME, IPv6 kaynak IP’si, systemd timer ve S3 doğrulanmadı. SIGKILL/güç kesintisinde trap çalışamaz; sonraki kilitli deploy eski gizli dosyaları temizler.