## Verdict

**FIX-NEEDED.** İki HIGH, bir MED bulgu doğrulandı. İnceleme statikti; test, Docker, ağ ve `docs/private/` kullanılmadı. İstenen model çifti seçilemediğinden bu sonuç astra+sol dual-review onayı değildir.

## Findings

Dosya yolları `src/hlmemo/` altındadır.

| severity | file:line | issue | fix |
|---|---|---|---|
| HIGH | `core/research_service.py:467`; `librarian/privacy.py:135` | **Taslak üzerinden gate atlanıyor.** Answer sonrasında kaynak V supersede edilir ve ortak projesinin librarian politikası kapatılırsa privacy gate önce `NOT_CURRENT` döndürür. `research_service.py:384` bunu yetki kaybı saymaz. Check yeniden kurulurken V excerpt’lerden çıkarılır, fakat `draft.draft()` içindeki alıntısı/handle’ı korunur; gate ID’leri V’yi içermez (`:469`). Policy-off içerik yeniden sağlayıcıya gönderilir. | Taslağın tüm kaynaklarını gate’e dahil edin. Kaynak çıkarıldığında ondan türemiş taslak/metinleri de atın veya yeniden oluşturun; `NOT_CURRENT` diğer yetki kayıplarını maskelememeli. |
| HIGH | `core/research_service.py:590` | **Plan çağrısıyla transaction/kilit örtüşüyor.** `search_only` arka planda çalışırken `plan` başlıyor. Arama transaction’ındaki `fresh_ctx`, `db/risk_queries.py:247` üzerinden advisory ve cihaz `FOR SHARE` kilitlerini alıyor. Yavaş arama sırasında provider isteği gönderilebilir; kilitler arama bitene kadar tutulur. API ve direct yolları etkilenir. | Aramayı tamamlayıp transaction’ı kapattıktan sonra plan çağrısını başlatın. |
| MED | `core/research_service.py:398`, `:444` | **Her-attempt gate türetilmiş metnin tüm kaynaklarını kapsamıyor.** Dış gate `self.sent` kümesini kontrol ederken retry/fallback precheck yalnızca mevcut `ids` listesini alıyor. Verify tüm cevabı gönderiyor, ancak yalnızca destek alıntılarının ID’lerini denetliyor. Cevabı etkilemiş diğer bir kaynağın yetkisi dış kontrolden sonra veya denemeler arasında kaldırılırsa metni yeniden gönderilebilir. | Türetilmiş metnin tüm kaynaklarını her denemenin precheck’inde kontrol edin; kaynak yetkisi kaybolduğunda gönderimi durdurun. |

## Not a finding

- VIEW dışındaki hit’ler filtreleniyor; `_drill` ayrıca VIEW üyeliğini doğruluyor.
- L2 özetleri yalnızca bütün üyeleri VIEW içindeyken gösteriliyor; başlangıçta superseded/tombstoned üyeler görünürlüğü engelliyor.
- Sistem özetleyicisi class/device kapsamlarını ve policy-off ortak projeleri dışlıyor; her denemede üyeleri yeniden kontrol ediyor.
- `detach(hold_s)` bağlantıyı bıraktıktan sonra deadline’ı uzatıyor; açık transaction ile direct kullanım reddediliyor.
- Yapılandırılmış support/primary/related handle’ları gösterilmiş excerpt’lerle sınırlı; final kontrol güncel, aktif ve yetkili kaynakları tutuyor.
- Finalde saptanan yetki kaybı answer/claims/quotes/related ve üretilmiş queries’yi temizliyor; kullanıcının sorusu korunuyor.
- `query_parts` ve internal drill bellek event/version/access-event/job yazmıyor. Ancak “ledger dışında hiç yazma yok” ifadesinin istisnası, middleware’in `devices.last_seen_at` güncellemesi (`server/middleware.py:460`).
- Cache migration’ında version FK bulunmuyor; incelenen replay/G6 yolunda engel saptanmadı.