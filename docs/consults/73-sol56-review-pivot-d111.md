## Verdict (DO-NOT-MERGE)

Bağımsız `gpt-6-astra low` ve `gpt-5.6-sol xhigh` incelemeleri aynı release-blocker’ı doğruladı: quote tarayıcısı korunan içeriği payload’a çıkarıyor ve kabul edilen rewrite bu içeriği değiştirebiliyor. D-099 kriter 3 sağlanmıyor.

## Per-claim

1. **OK** — NFC, invisible/Cf reddi, mixed/fullwidth/math maskeleme, fixed-point credential kontrolü ve placeholder-looking girdi doğrulandı: `src/hlmemo/core/rewrite_guard.py:155-230,332-344`; `src/hlmemo/librarian/query_rewrite.py:174-202`.
2. **PARTIAL** — Kapalı apostrof suffix listesi ve `error'qzxv` reddi doğru; fakat quote scanner iç apostrofu kapanış sanıyor ve glued/escaped quote içeriklerini sızdırıyor: `src/hlmemo/core/rewrite_guard.py:409-450`.
3. **PARTIAL** — Doğrudan word-glue ve placeholder adjacency korunuyor; fakat protected tokenın clitic/inflected tekrarı kabul ediliyor: `src/hlmemo/core/rewrite_guard.py:612-680`.
4. **OK** — Queue wait yalnız nedensel olarak bütçeyi kısalttığında muaf; test edilen `1e-9` bekleme sayılıyor: `src/hlmemo/librarian/provider.py:123-136,763-783`.
5. **OK** — Missing env FAIL; R3 mode rewrite ON/cap OFF gerektiriyor; unmarked R2 env yalnız etiketli D-108 interim olarak geçiyor: `deploy/scripts/check_librarian.py:232-343`.
6. **OK** — Snapshot, restore-before-start, failure recovery, interrupted rerun, superseded snapshot ve older-runner yolları tutarlı: `deploy/scripts/remote-deploy.sh:307-315,395-403`; `deploy/scripts/rollback.sh:120-252`; `tests/deploy/test_llm_env_release.py:96`.

## New findings

| severity | file:line | trigger | fix |
|---|---|---|---|
| HIGH · in-scope | `rewrite_guard.py:430-433` | `‘İstanbul’un üretim bağlantısı’ neden reddediliyor?` → payload `⟦P1⟧ üretim ⟦P2⟧…`; `Why is ⟦P1⟧ production ⟦P2⟧ rejected?` kabul edilip quoted literal değiştirilir. `f'…'` ve escaped `\"` de aynı sınıfta. | Aktif quote içindeki dilsel apostrofu kapanış sayma; escaped/prefixed quote taraması ve NFC/NFD regresyonları ekle. |
| MEDIUM · in-scope | `rewrite_guard.py:295,612-675` | Hidden `worker`; `Why does worker's ⟦P1⟧ stop?` kabul edilip `worker's worker` olur. `workers` ve `İstanbul`→`Istanbul` da geçer. Orijinal sorgunun da aranması etkiyi retrieval bozulmasıyla sınırlar. | Hidden/output karşılaştırmasını `fold()` ile birleştir; kapalı English clitic/inflection tabanlarını da repeat olarak reddet. |

Kanıt: property sayıları `2,000/8,080/2,000`; unit+fixtures `801 collected, 800 passed, 1 skipped`. Deploy’de `146/147` geçti; tek eksik clean exportta `.git` olmamasıydı. Integration, G3 ve live acceptance yeniden çalıştırılmadı.

## Ship-with-flag-ON (R3)

**No.** Korunan quote içeriğinin provider payload’ına çıkması ve kabul edilmiş rewrite ile değişebilmesi açık HIGH bulgudur.