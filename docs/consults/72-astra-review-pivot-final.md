## Verdict (FIX-NEEDED)

Astra-low ve Sol-xhigh incelemeleri birleştirildi: payload gizliliği, restore ve rollback kapsamında açık HIGH kusurlar var. 91 hedefli test geçti; bir G-SURF testi salt okunur ortamın geçici dosya kısıtına takıldı. 778 test, performans ve canlı kabul ölçümleri yeniden doğrulanmadı. Dosyalar değiştirilmedi.

## Per-claim

1. **PARTIAL** — Maskeleme var; apostrof, alıntı ve restore sınırları aşılabiliyor: `src/hlmemo/core/rewrite_guard.py:70,145,289,371`.
2. **PARTIAL** — Credential kontrolleri var; Unicode görünmez karakterle sözlükten bir sır gönderilebiliyor: `src/hlmemo/librarian/query_rewrite.py:166`.
3. **PARTIAL** — Admission ve concurrency=4 mevcut; breaker muafiyeti bekleme miktarını kullanmıyor: `src/hlmemo/librarian/query_rewrite.py:107,454`; `src/hlmemo/librarian/provider.py:747`.
4. **OK** — Varsayılan 1.0 ve fusion bağlantısı doğrulandı: `src/hlmemo/config.py:259`; `src/hlmemo/core/read_service.py:277`.
5. **PARTIAL** — Installer ON yapıyor, cutover kontrolü doğruluyor; R2 rollback env’i geri yüklemiyor: `deploy/scripts/install_llm_env.sh:200`; `deploy/scripts/check_librarian.py:218`; `deploy/scripts/rollback.sh:114`.
6. **OK (kod incelemesi)** — Kota/pay/lineage/deadline korunmuş; demotion union, schema fallthrough ve fallback mevcut: `src/hlmemo/librarian/query_rewrite.py:347,475,511,656`; `src/hlmemo/core/read_service.py:303`; `src/hlmemo/librarian/provider.py:515,668`. Flag-off yüzey eşitliği testi geçti (`tests/unit/test_pivot_surface.py:39`); DB entegrasyonu yeniden çalıştırılmadı.

## New findings

Tablodaki bütün bulgular **in-scope**; politika genişletmesi değildir. `\n` gerçek satır sonunu, `\u200b` sıfır genişlikli karakteri gösterir.

| severity | file:line | trigger | fix |
|---|---|---|---|
| HIGH — in-scope | `src/hlmemo/core/rewrite_guard.py:145` | `hata'myprivatevalue neden oluyor ve nasıl düzeltilir?`: gate geçiyor, redaction=0; keyfî sır eki payload’da tamamen açık. Aynı kontrol çıktıdaki `error'qzxv` enjeksiyonunu da kabul ediyor. | Apostroflu kelimenin tamamını veya doğrulanmış dil ekini kontrol et; belirsiz tokenı bütünüyle maskele. |
| HIGH — in-scope | `src/hlmemo/core/rewrite_guard.py:70` | `"başlangıç\nüretim bağlantı\nson"`: alıntının ortasındaki `üretim bağlantı` payload’a açık çıkıyor. | Çok satırlı alıntı/backtick spanlarını bütünüyle koru. |
| HIGH — in-scope | `src/hlmemo/core/rewrite_guard.py:289` | `XX"foo"hata` → `⟦P1⟧hata`: korunan teknik tokenın alt dizisi açığa çıkıyor. | Alıntı bölünürken çevreleyen tokenın koruma durumunu koru. |
| HIGH — in-scope | `src/hlmemo/librarian/query_rewrite.py:166` | `pa\u200brola çilek neden çalışmıyor ve nasıl düzeltilir?`: gate geçiyor, redaction=0; sır değeri `çilek` gönderiliyor. | Credential taramasında görünmez Unicode/format karakterlerini normalize et veya sorguyu engelle; sıfır-call regresyonu ekle. |
| HIGH — in-scope | `src/hlmemo/core/rewrite_guard.py:371,389` | `why is ⟦P1⟧production failing` kabul ediliyor; korunan `config.py`, `config.pyproduction` oluyor. | Placeholder çevresindeki token sınırlarını doğrula; modelin eklediği bitişik metni reddet. |
| MEDIUM — in-scope | `src/hlmemo/librarian/provider.py:747` | Mikrosaniyelik `queue_wait_s>0`, provider/precheck süresiyle 1.5 saniyenin altına düşen gerçek timeout’u breaker hesabından çıkarıyor. | Muafiyeti bekleme miktarının gerçekten oluşturduğu bütçe kaybına bağla. |
| HIGH — in-scope | `deploy/scripts/rollback.sh:114` | R3 env kurulduktan sonra rollback, R2 imajını güncel env ile başlatıyor; R2’de bulunmayan profiller yüklenemiyor. | Sürüme bağlı env snapshot’ını önceki imaj başlamadan geri yükle; başarısız rollback kurtarmasında R3 env’ini geri getir. |

## Ship-with-flag-ON (R3)

**No.** Yeniden üretilen gizlilik ve restore kusurları D-099 kriter 3’ü ihlal ediyor; R2 rollback yapılandırması da tutarlı değil.