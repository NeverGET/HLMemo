## Verdict (DO-NOT-MERGE)

R3 yayınlanmamalı. D-085 dual review ve kök doğrulama birden fazla HIGH kusur buldu. İlgili suite yeşil (`81 passed, 56 subtests`), ancak kritik crash/state yollarını modellemiyor.

## Findings

| severity | file:line | trigger | fix |
|---|---|---|---|
| HIGH | `deploy/scripts/remote-deploy.sh:307-315`; `rollback.sh:123-136,232-244` | Diskte R3 env, çalışan container’larda R2 env varken deploy snapshot’ı R3 dosyasını R2’ye ait sayar; rollback R2’yi yükleyemediği R3 profilleriyle başlatır. Harness’ta yeniden üretildi. | Snapshot öncesi diskin non-secret release/mapping fingerprint’ini çalışan api ve librarian ile karşılaştır; uyuşmazlıkta dur. |
| HIGH | `remote-deploy.sh:49-61,395-403`; `release_state.py:88-111` | Başarılı R3’ün aynı-ref rerun’ı rollback çiftini R2→R3 yerine R3→R3 yapar ve R2 env snapshot’ını siler. | `revision == current_ref` durumunda mevcut rollback tuple/snapshot’ı koru; gerçek same-ref regression testi ekle. |
| HIGH | `rollback.sh:181-225,236-243` | Destructive DB restore sırasında kill sonrası retry, özgün R3 safety dump yerine yarım/R2 DB’yi yeniden snapshot’lar; sonraki recovery yanlış veriyi R3 altında açar. | İlk safety dump ve destructive phase’i DB değişmeden atomik state’e kaydet; retry’de aynı dump’ı kullan. |
| HIGH | `rollback.sh:71-95` | `rollback_in_progress`, `--accept-release` için running-revision guard’ını da kapatır; yarım rollback sırasında accept başarılı olup backup’ları siler. Harness’ta yeniden üretildi. | Accept sırasında in-progress’ı koşulsuz reddet ve daima `running == current_ref` doğrula. |
| HIGH | `remote-deploy.sh:372-403` | Yeni stack başladıktan fakat state publish edilmeden SIGKILL/interrupt olursa running=R3, state=R2 kalır; deploy/rollback rerun güvenli biçimde yakınsamaz. | Kesintiden önce durable deploy-attempt journal yaz; rerun’da publish’i tamamla veya kayıtlı tuple ile recovery yap. |
| MEDIUM | `check_librarian.py:74-83,254-289` | Manifest yalnız non-empty değer arar; yanlış fakat geçerli D-094 profilleri veya api/librarian arasında farklı mapping PASS eder. | Manifestte kesin R3 değerlerini tanımla ve iki container’ın tüm değerlerde birebir eşleşmesini iste. |
| MEDIUM | `remote-deploy.sh:307-315`; `release_state.py:108-111`; `rollback.sh:247-252` | Deploy failure veya state commit ile unlink arasındaki crash, API key içeren `.release-*` kopyalarını kalıcı ve kayıtsız bırakır. | Snapshot/pending-cleanup yollarını atomik state’te journal’la; lock altında idempotent cleanup ve fault-injection testleri ekle. |

## Release-safe

No — R2’nin R3 env ile başlatılabildiği, R2 rollback hedefinin kaybedilebildiği ve kesilen rollback’in yanlış DB’yi recovery edebildiği doğrulandı.