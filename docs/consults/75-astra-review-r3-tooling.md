## Verdict (DO-NOT-MERGE)

Çift kritik inceleme, R3’ün kesinti ve tekrar çalıştırma güvenliğini doğrulamadı. Normal rollback env sıralaması ve D-065 pre-W0 engeli korunuyor; ancak aşağıdaki açıklar sürümü engelliyor. Salt okunur incelemede sözdizimi kontrolleri geçti; iki davranış bellek içinde yeniden üretildi. Docker/VM testleri çalıştırılmadı.

## Findings

| Severity | file:line | trigger | fix |
|---|---|---|---|
| HIGH | `deploy/scripts/remote-deploy.sh:395`; `deploy/scripts/release_state.py:108` | R3 state’i yayımlandıktan sonraki kesinti/hata üzerine aynı ref tekrar deploy edilirse `previous_ref=R3` olur ve R2 env snapshot’ı silinir. R2 rollback’i kaybolur. | Aynı-ref tekrarında mevcut rollback çiftini koru; yalnızca doğrulamayı tamamla. Regresyon testi ekle. |
| HIGH | `deploy/scripts/install_llm_env.sh:114`; `deploy/scripts/remote-deploy.sh:313` | Env kurulumu deploy kilidini almaz. Yarış halinde R2 çalışırken diskteki R3 env, R2’nin snapshot’ı olarak kaydedilebilir; rollback yüklenemeyen env ile başlar. Servis recreation kesintisi de farklı env’ler bırakabilir. | Kurulum, iki servisin geçişi ve doğrulamayı ortak kilit ve kalıcı, yeniden sürdürülebilir işlem altında yürüt; snapshot öncesi çalışan env’i doğrula. |
| MEDIUM | `deploy/scripts/check_librarian.py:268`; `deploy/scripts/remote-deploy.sh:430` | Kontrol diskteki env’in yalnızca varlığına bakar; servisler arasında yalnızca release etiketi karşılaştırılır. Aynı `r3` etiketiyle farklı fallback değerleri kabul edilir; disk/container sapması görülmez. | Diskteki hedef manifest değerlerini iki servisin etkin değerleriyle karşılaştır; eski container ve kısmi recreation testleri ekle. |
| MEDIUM | `deploy/scripts/check_librarian.py:273` | İki servis aynı bilinmeyen etiketi (`r4`/yazım hatası) taşıdığında otomatik kontrol bunları R2 interim sayar; gerekli R3 anahtarları olmadan geçebilir. | Interim istisnasını yalnızca etiketsiz env’e uygula; bilinmeyen etiketleri reddet. |
| MEDIUM | `deploy/scripts/release_state.py:108`; `deploy/scripts/rollback.sh:248` | State yazıldıktan sonra snapshot silinmeden crash olursa API anahtarı içeren dosyaların referansları kaybolur. Tekrar çalıştırma bunları temizleyemez. | Silinecek yolları kalıcı temizleme kuyruğunda tut; kilit altında tekrar dene. State-yazımı sonrası kesinti testleri ekle. |

## Release-safe

**No.** Aynı-ref retry R2’ye dönüşü yok ediyor; kilitsiz env geçişi yanlış rollback snapshot’ı oluşturabiliyor. Mevcut testler bu kesinti ve yarış durumlarını kapsamıyor.