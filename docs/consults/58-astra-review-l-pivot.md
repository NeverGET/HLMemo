## Verdict (FIX-NEEDED)
Statik incelemede 0009’un upgrade/down sırası, W0a dağıtımı veya rendition baytlarının replay/export yolu için somut kusur bulmadım. Migration kilitsiz değil; mevcut tablolardaki kısa FK kilitlerinin beklemesi 3 saniyeyle sınırlı. Birleşim öncesinde token koruması, proje dışlama politikası ve D-086 uyumu düzeltilmeli. Tek-vote füzyonu korunuyor; ancak DF ve kaynak sınırı hatalı. Dosya değiştirilmedi; yüksek kapsamadaki gecikme ölçümleri yeniden çalıştırılmadı.

## Findings
| severity | file:line | trigger (concrete scenario) | fix |
|---|---|---|---|
| HIGH | `src/hlmemo/core/language.py:200,265` | `/Data/Config.py` → `/data/config.py` ve `-5` → `5` dönüşümleri kabul ediliyor; aynı denetim rewrite ve rendition için kullanılıyor. | Korunan tokenları büyük/küçük harf, işaret ve noktalama dahil birebir karşılaştır; gevşek eşleştirmeyi kaldır. |
| HIGH | `src/hlmemo/librarian/tasks/translate.py:148,206`; `src/hlmemo/librarian/privacy.py:122` | A+B kapsamlı öğede B `librarian_cross_project=exclude` olduğunda translate gate yalnız `librarian=off` denetliyor; metin sağlayıcıya gönderilebiliyor. | İlk denetimde ve her sağlayıcı denemesinde D-083’ün “yalnız tam olarak dışlanan proje içinde” kuralını uygula. |
| HIGH | `src/hlmemo/librarian/worker.py:324,362`; `src/hlmemo/db/replay.py:312` | Sürekli bütçe/kesici hatası sınırsız authoritative defer olayı üretir; terminal failure da canonical `job:<key>` kimliğini kullanmaz. | Translate entegrasyonundan önce D-086 worker/replay düzeltmesini al: systemic hand-back yalnız job satırı, ≤4 defer, tek terminal olay. |
| HIGH | `src/hlmemo/core/retrieval.py:381` | Kabul edilen olumsuzluk tersine çevirmesi, rendition baskın olduğunda özgün kanıtın yerine preview olarak gösterilir. `translation:true` anlam hatasını önlemez. | Preview daima özgün metin olsun; çeviri yalnız açıkça ayrılmış yardımcı ipucu olsun. |
| MEDIUM | `src/hlmemo/core/term_stats.py:105,111` | Aynı terim özgün birimde ve çevirisinde bulununca DF iki kez artar; payda sabit kaldığından yararlı terimler yaygın sayılıp elenir. | DF’yi özgün birim kimlikleri üzerinden birleşim/distinct olarak hesapla; rendition parçalarını bağımsız belge sayma. |
| MEDIUM | `src/hlmemo/core/retrieval.py:259`; `src/hlmemo/core/read_service.py:342` | Yalnız aynı kaynaktan beş aday varsa ertelenenler geri eklenir ve ilk beş yine sınırı aşar; alternatifler fetch kesiminin ötesindeyse backfill yapılamaz. | Çeşitlendirmeyi kesimden önce uygula; yeterli alternatif yoksa ilk beşi ihlal eden adaylarla doldurma. |
| MEDIUM | `src/hlmemo/librarian/tasks/translate.py:141`; `src/hlmemo/ops/renditions.py:101` | Renditions kapatıldıktan sonra bekleyen translate işleri sağlayıcı çağırıp rendition yazar; backfill de kapalıyken iş oluşturabilir. | Enqueue ve execution yollarında bayrağı denetle; işleri yeniden açılana kadar beklet. “Flag-off byte-identical” iddiasını daralt. |
| HIGH | `src/hlmemo/core/read_service.py:306`; `src/hlmemo/db/rendition_queries.py:77,115,150` | Yüksek kapsamada sıralı ek lexical/title sorguları ve exact vector taramaları; bildirilen p95 515/541 ms ile 500 ms kapısı geçilmiyor. | Renditions kapalı kalsın; önce ek rendition vector taramasını kapatan lexical-only seçeneği ölç, kalite ve gecikme kapılarını yeniden doğrula. |

## Positions
(a) **English previews:** Özgün metin gösterilmeli; çeviri yardımcı ipucu olmalı.
(b) **Observer renditions:** Mevcut sözleşmeyle uygun değil: otomatik olarak etkin indekse yazmak sıralamayı ve kanıt sunumunu değiştirir; D-074/D-076 için açık ADR istisnası veya etkinleştirilmeyen gölge indeks gerekir.
(c) **Latency mitigation:** En ucuz güvenli önlem bayrağı kapalı tutmak; ilk optimizasyon deneyi ek rendition vector taramasını kaldırmaktır.

## Decision-log text
English pivot özgün metni tek kanıt kaynağı olarak korur; çeviriler preview yerine yardımcı ipucudur.
Korunan tokenlar birebir korunur; DF özgün birimleri tek sayar; dışlama politikası her sağlayıcı denemesinde uygulanır.
Translate D-086 olay sözleşmesine uyar; rendition bayrağı enqueue ve execution yollarını birlikte durdurur.
Observer’da etkin rendition indekslemesi açık ADR onayı gerektirir; yüksek kapsamada kalite ve p95 ≤500 ms kapıları geçilmeden etkinleştirilmez.