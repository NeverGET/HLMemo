## Verdict (FIX-NEEDED)

İki bağımsız inceleme üç kusuru doğruladı; TEMPORAL ve CLOSE açılmamalı. 30 saf test geçti, ancak DB concurrency/replay ve performans testleri çalıştırılmadı. Export’ta D-101 bulunmadığından isteğinizdeki sözleşme esas alındı. Gelecekteki güvenilir tarih, geçerlilik aralığındaysa kabul ediliyor; mevcut D-089 metni bunu yasaklamıyor.

## Findings

| severity | file:line | trigger (concrete scenario) | fix |
|---|---|---|---|
| HIGH | `src/hlmemo/librarian/reversal.py:92` | Aynı batch’te Q1 ve Q2, A’yı kapatır. Worker Q2’nin kapanışını tekrar olduğu için çıkarır, ikisini de applied yapar. Q2’yi geri almak Q1’in kapanışını açar; Q1 ve supersedes bağlantısı aktif kalır. | Mutasyonları soru kimliğine bağlayın; ortak kapanış bağımlılıklarını denetleyerek yalnız ilgili etkiyi geri alın. |
| HIGH | `src/hlmemo/librarian/actor.py:317` | Bitişi sonlu aktif segmentte approval-close `retimable=False` olur. Sonraki batch kilidi beklemesi olay zamanını ilerletir fakat kapanış eski zamanda kalır; geçerlilik erken kesilir. | Tüm kilitlerden sonraki son saatle kapanışı materialize edin; etkilenen segmentleri ve survivor aralıklarını yeniden hesaplayın. |
| HIGH | `src/hlmemo/librarian/evidence.py:351` | Alıntı tarihleri model yönünü desteklerken iki evidenced `valid_from` ters yönü gösterir. Kod `dates` kabulüyle D-101’in zorunlu reddini atlar. | Her iki güvenilir tarih kaynağının veto hakkını ayrı uygulayın; çatışmada supersession üretmeden owner’a contradiction sorusu yöneltin. |

## Safe-to-enable

TEMPORAL: **Hayır** — D-101 kanıt çatışması reddi eksik.  
CLOSE: **Hayır** — geri alma başka sorunun kapanışını bozabiliyor; approval zamanı her aralıkta korunmuyor.  
Expansion: **Evet, observer ablasyonu için** — ayrı kotalarda bütünlük kusuru bulunmadı; üretim etkinleştirmesi kalite/maliyet gate’lerine bağlı.