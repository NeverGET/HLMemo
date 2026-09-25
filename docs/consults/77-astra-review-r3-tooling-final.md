## Verdict (DO-NOT-MERGE)

Astra-low ve Sol-xhigh incelemeleri kritik kesinti yollarında birleşiyor: tekrar çalıştırma kilitlenebiliyor ve recovery yanlış kalıcı image seçimi bırakabiliyor. Salt okunur incelemede shell sözdizimi ve bellek içi kontroller çalıştırıldı; Docker/VM doğrulaması yapılmadı.
## Per-claim

1. **PARTIAL** — Fingerprint bütçe/guard alanlarını dışlıyor; rollback eksik librarian’ı atlıyor (`llm_env_release.py:85`, `rollback.sh:199`).
2. **OK** — Aynı-ref doğrulaması çifti koruyor; self-pair reddediliyor (`remote-deploy.sh:347`, `release_state.py:147`).
3. **OPEN** — Kilit var; diğer yarım işlemlerle journal çakışması engellenmiyor (`install_llm_env.sh:190`).
4. **PARTIAL** — İlk dump/destructive işaretleri doğru; restore boyunca ortak DB kilidi yok (`rollback.sh:256`, `rollback.sh:274`).
5. **OK** — Yarım rollback reddediliyor, running=current zorunlu (`rollback.sh:90`).
6. **PARTIAL** — Journal migration öncesinde; recovery kalıcı image seçimini düzeltmiyor (`remote-deploy.sh:561`, `remote-deploy.sh:202`).
7. **PARTIAL** — Kesin mapping ve servis eşitliği kontrol ediliyor; diskte eksik primary/default fallback karşılaştırmadan muaf (`check_librarian.py:355`).
8. **OK** — Bilinmeyen etiketler reddediliyor (`check_librarian.py:467`).
9. **PARTIAL** — State’ten çıkarılan kopyalar kuyruklanıyor; ilk snapshot yaratımında kayıt öncesi kesinti penceresi var (`remote-deploy.sh:496`).
10. **PARTIAL** — 10/2/1, guard ve koruma/reset doğru; korunmuş yanlış anahtar doğrulanmıyor (`deploy/llm.env.example:60`, `install_llm_env.sh:124`, `check_librarian.py:267`).
## New findings

| severity | file:line | trigger | fix |
|---|---|---|---|
| HIGH | `deploy/scripts/install_llm_env.sh:196` | Deploy journal’ından sonra kill → install R2 image’ını R3 env ile başlatıp `env_switch` bırakır → deploy bunu reddedip R2 checkout’a döner → install artık pre-R3 diye reddeder. | Install öncesinde deploy/rollback journal’larını reddet; preflight reddinde checkout değiştirme. |
| HIGH | `deploy/scripts/remote-deploy.sh:285` | Kalıcı HLM_IMAGE=R3 yazıldıktan, publish öncesinde kill; retry doğrulaması başarısız olunca recovery R2’yi başlatır ama diskte R3 kalır. Sonraki recreate yanlış sürümü açar. | Recovery tamamlanmadan kalıcı image seçimini de önceki sürüme döndür. |
| HIGH | `deploy/scripts/remote-deploy.sh:366`; `deploy/scripts/rollback.sh:277` | Resume recovery veya rollback, `.operation.flock` tutmadan DB’yi değiştirir; eşzamanlı `backup/restore.sh` aynı DB’yi yeniden değiştirebilir. | Dump–restore–restart–recovery boyunca ortak işlem kilidini tut. |
| MEDIUM | `deploy/scripts/llm_env_release.py:85`; `deploy/scripts/rollback.sh:199` | Yalnız bütçe/guard değişikliği provenance kontrolünden geçer; eksik librarian da karşılaştırılmaz. | Tüm non-secret manifest alanlarını kapsa; iki servisin varlığını zorunlu tut. |
| MEDIUM | `deploy/scripts/remote-deploy.sh:496`; `deploy/scripts/rollback.sh:204` | Snapshot oluşturulduktan, `record-pending` öncesinde kill: anahtarlı kopya kayıtsız kalır. | Deterministik hedefi kopyalamadan önce journal’a kaydet; geçici kopyaları da temizle. |
| MEDIUM | `deploy/scripts/check_librarian.py:163`; `deploy/scripts/install_llm_env.sh:125` | Korunan iptal edilmiş/yanlış nonempty anahtar PASS alabilir; probe kimlik doğrulamaz, HTTP 401 bile reachable sayılır. | Anahtar için ayrı, secret sızdırmayan authenticated doğrulama ekle. |
| HIGH | `docs/status/R3-REHEARSAL.md:100`; `docs/status/R3-REHEARSAL.md:138` | Prova rewrite ON bekliyor; mevcut manifest OFF istiyor. Rollback öncesi manuel R2 env kurulumu yeni provenance kontrolünü başarısız kılıyor. | Provayı mevcut kapsamla güncelle; env dönüşünü rollback snapshot mekanizmasına bırak. |
## Release-safe

**No.** Journal kilitlenmesi, eksik recovery ve DB restore yarışı giderilmeden R3 güvenle yayımlanamaz.