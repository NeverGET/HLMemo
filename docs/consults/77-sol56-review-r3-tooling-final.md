## Verdict (DO-NOT-MERGE)

Zorunlu iki D-085 incelemesi ve kök doğrulaması aynı sonuca ulaştı: 88 seçili test geçse de fault-injection karşı örnekleri DB bütünlüğü, yarım-stack publication ve env provenance açıklarını doğruladı.

## Per-claim

1. **OPEN** — fingerprint eksik ve iki servisi zorunlu kılmıyor (`deploy/scripts/llm_env_release.py:84`, `deploy/scripts/rollback.sh:196`); 2. **OK** — verify-only/self-pair guard doğru (`deploy/scripts/remote-deploy.sh:345`, `deploy/scripts/release_state.py:145`).
3. **PARTIAL** — tek başına resume ediyor, diğer operation journal’larını reddetmiyor (`deploy/scripts/install_llm_env.sh:183`); 4. **PARTIAL** — marker sırası doğru, fakat safety/operation-lock ömrü güvensiz (`deploy/scripts/rollback.sh:209`, `deploy/backup/backup.sh:29`).
5. **OK** — rollback sırasında ve running mismatch’te accept reddediliyor (`deploy/scripts/rollback.sh:80`); 6. **OPEN** — retry yarım stack yayımlayabiliyor veya yanlış persistent image bırakıyor (`deploy/scripts/remote-deploy.sh:281`, `deploy/scripts/remote-deploy.sh:366`).
7. **OPEN** — ek fallback ve okunamayan disk fail-open (`deploy/scripts/check_librarian.py:96`, `deploy/scripts/check_librarian.py:115`); 8. **OK** — yalnız etiketsiz env interim (`deploy/scripts/check_librarian.py:431`).
9. **OPEN** — snapshot/temp orphan pencereleri var (`deploy/scripts/llm_env_release.py:45`, `deploy/scripts/remote-deploy.sh:496`); 10. **PARTIAL** — cap/guard doğru, fakat yanlış non-empty key PASS ediyor (`deploy/llm.env.example:56`, `deploy/scripts/install_llm_env.sh:112`).

## New findings

| severity | file:line | trigger | fix |
|---|---|---|---|
| HIGH | `remote-deploy.sh:393-397,568-569` | API yeni revizyona geçtikten, worker geçmeden kill; retry yalnız API’ye bakıp state’i publish eder. | Resume’da tüm stack için `up --wait`; her zorunlu servisin tekil, healthy ve doğru revision olduğunu kanıtla. |
| HIGH | `rollback.sh:256-282`; `remote-deploy.sh:366`; `backup.sh:29-32` | Rollback/deploy recovery sırasında timer backup veya restore DB drop/restore ile yarışır. | Parent operation lock’u safety dump öncesinden validation sonuna kadar tutsun. |
| HIGH | `rollback.sh:230-261`; `retention.py:39-42` | Destructive kill sonrası günlük rotation safety dump’ı siler; retry DB’yi restore etmeden journal’ı temizler. | Safety dump’ı rotation dışına taşı; eksik dump’ta current stack’i başlatma/journal temizleme. |
| HIGH | `install_llm_env.sh:183-211` | Unfinished deploy/rollback üzerine installer girip ikinci journal açar; retry’lar karşılıklı bloke olur. | Operation journal’larını atomik ve karşılıklı dışlayan state geçişleri yap. |
| HIGH | `remote-deploy.sh:189-206,285,366-402` | Image env’e yazıldıktan sonra kill; recovery eski stack’i açsa da `prod.env` yeni image’da kalır. | Attempt temizlenmeden önce persistent image’i previous değere geri yazıp doğrula. |
| HIGH | `llm_env_release.py:84-156`; `rollback.sh:197-203` | Guard/cap drift’i veya eksik librarian provenance’dan geçer. | Tüm anlamlı non-secret alanları kapsa; tam bir api ve librarian zorunlu olsun. |
| HIGH | `R3-REHEARSAL.md:80-102,136-141`; `RUNBOOK.md:674` | Checklist rewrite ON, boş tooling diff, caps 60 ve rollback öncesi R2-env reinstall bekliyor; güncel scriptler bunları reddeder. | Checklist’i rewrite OFF, yeni tooling scope, otomatik env restore ve installer-öncesi cap kararıyla yenile. |
| MEDIUM | `check_librarian.py:96-131,345-388` | Ek `HLM_FALLBACK_PROFILE__*` veya okunamayan `--llm-env-file` PASS eder. | Tüm dinamik override’ları allow-list ile doğrula; açık disk yolu okunamıyorsa fail. |
| MEDIUM | `llm_env_release.py:45-80`; `install_llm_env.sh:151-169` | Copy→pending veya temp-write sırasında SIGKILL secret orphan bırakır; source symlink izlenir. | Creation öncesi journal, her lock holder’da temp sweep ve `O_NOFOLLOW`/owner/mode kontrolü. |
| MEDIUM | `install_llm_env.sh:112-132`; `check_librarian.py:163-175,265-270` | Korunan yanlış key `key_set=true`; unauthenticated 401/403 probe “reachable” sayılır. | Journal temizlenmeden bounded authenticated doğrulama veya açık operator attestation iste. |

## Release-safe

**No.** DB recovery yarışları, eksik safety dump’ta yanlış başarı ve yarım-stack publication düzelmeden R3 güvenle yayımlanamaz.