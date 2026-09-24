## Verdict (FIX-NEEDED)

D-085 çift inceleme ve kök sondalar aynı sonuca ulaştı: credential ve guard tarafında açık HIGH kusurlar var; D-099 koşulu sağlanmıyor. 132 hedefli saf test geçti; salt-okunur ortamda Postgres entegrasyon kapıları yeniden çalıştırılamadı.

## Per-claim

1. **PARTIAL** — Altı regresyon reddediliyor ve aksansız Türkçe çalışıyor; fakat komut/alıntı/yol değişimleri hâlâ kabul edilebiliyor (`language.py:329-396,457-462`).
2. **PARTIAL** — Verilen örneklerin çoğu engelleniyor; `DB_PASSWORD=hunter2`, `gizli anahtar hunter2` ve `parola çilek` engeli aşıyor (`query_rewrite.py:100-124,338-342`).
3. **PARTIAL** — Stalled/503 primary, F task fallback’ına deadline içinde geçiyor; schema-invalid primary iki kez çağrılıp fallback denenmeden sonlanıyor (`query_rewrite.py:189-198,541-550`; `provider.py:490-515`).
4. **OK** — Deadline enqueue’dan mutlak; semaphore bekleyişini kapsıyor ve başarısız schedule `unavailable` döndürüyor (`query_rewrite.py:344-397`; `read_service.py:348-354`).
5. **OK** — Stable daily lineage, atomik 60/300 claim ve paylaşılan bütçede %20 sınırı doğru; token generation kotayı sıfırlamıyor, cihaz çoğaltma proje kotasına takılıyor (`query_rewrite.py:128-138,413-445`; `budget.py:79-115`).
6. **OK** — Demotion orijinal ve İngilizce terimlerin stabil birleşimini alıyor (`read_service.py:265,289-291,327`).
7. **PARTIAL** — Model-id kontrolü değişmemiş ve mevcut fallback kodu generic; satır filtresi multiline task-special-case’i kaçırabilir. Flag-off yüzey hash’leri geçti (`test_wf_task_fallbacks.py:356-377`; `test_pivot_surface.py:39-46`).

## New findings

| severity | file:line | trigger | fix |
|---|---|---|---|
| HIGH | `query_rewrite.py:100-124`; `redact.py:53-58,136-138` | `DB_PASSWORD=hunter2`, `gizli anahtar hunter2`, `parola çilek` için detector ve redactor sıfır eşleşme; ham değer primary/fallback’a çıkabilir. | Env/dotted/hyphenated secret adlarını kısa değerlerle yakala; soru istisnalarını dar allowlist yap; sıfır-call e2e testleri ekle. |
| HIGH | `language.py:329-396,457-462` | `./manage.py migrate`→`createsuperuser`, `bun run build`→`deno run build`, `"x"`→`x`, yeni `git log` ekleme ve `src/main`/`src/other` rollerini takas etme kabul ediliyor. | Tam komut spanını iki yönde koru; delimiter, sıra ve çokluğu doğrula; argüman sınırında reddet. |
| MEDIUM | `provider.py:490-515,560-615` | Schema-invalid primary iki çağrı tüketiyor; sağlıklı task fallback hiç denenmiyor. | Latency modunda ilk schema failure ile sonraki profile geç; background davranışını ayrı tut. |
| LOW | `test_wf_task_fallbacks.py:371-377` | Task adı ve fallback kullanımı farklı satırlardaysa genericity testi geçiyor. | Satır filtresi yerine AST/fonksiyon kapsamı kontrolü kullan. |
| HIGH | `FULL-VS-MAIN.patch:1,53-66` | Bu snapshot doğrudan alınırsa consult 66, D-099–101 ve prod-gate status kaydı siliniyor. | Güncel main’i üç yönlü merge/rebase et; karar kuyruğunun D-101’e kadar korunduğunu doğrula. |

## Ship-with-flag-ON

**No** — credential sızıntısı ve korunan komut/string değişimleri açık; ayrıca D-099, final incelemede açık HIGH bulunmamasını şart koşuyor.