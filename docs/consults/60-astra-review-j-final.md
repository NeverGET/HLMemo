## Verdict (FIX-NEEDED)

Signal-only replay yarışı kapanmış; fakat yeni SQL filtresi eski proposal biçimindeki kabul edilmiş cevapları strand ediyor. Clause kontrolü de aynı terim geçerli bölümde tekrarlandığında yanlış demotion yapabiliyor. Değişen üretim dosyalarında lint temiz; 14 unit test fonksiyonu doğrudan geçti. Integration ve performans gate’leri yeniden doğrulanmadı. Dosya değiştirilmedi; yeni deadlock, scope leak veya observer mutasyonu bulunmadı.

## PROMOTION-READY (no)

D-077 sağlanmıyor: `mutation` biçimindeki eski accepted answer’lar promotion/release/sweeper seçiminden dışlanıyor.

## Per-item

1. **FIXED** — `src/hlmemo/librarian/worker.py:626,1058,1085`: signal/proposal hedefleri sıralı kilitlenip sonra event ID ayrılıyor. Diğer incelenen çatışan mutation yollarında açık yarış bulunmadı.
2. **FIXED** — `src/hlmemo/librarian/roles.py:167`; `worker.py:827,1211`; `questions.py:230`: widening otomatik uygulanmıyor; açık owner cevabı ve bütün projelerde write yetkisi gerekiyor.
3. **FIXED** — `src/hlmemo/librarian/worker.py:566,593,1106`: D-086 event sınırları korunuyor. Queued `run_after/last_error` farkları bilinçli olarak kayboluyor; replay işi daha erken çalıştırabilir. Terminal kayıtlar alanları sabitliyor; maskeleme authoritative durum/attempt farklarını gizlemiyor.
4. **PARTIAL** — `src/hlmemo/core/supersession.py:103,106,142,148`: predicate yeni⇒eski sağlıyor, fakat tüm sıralama için garanti vermiyor; `[1,2,3,4]` örneğinde baseline `[3,2,4,1]`, yeni `[1,3,2,4]` olabiliyor. Cycle kapanış kenarı atlanıyor ve bütün hit’ler korunuyor; budget içinde kalmaları garanti değil. Aşağıdaki clause kusuru sürüyor.

## New findings

| severity | file:line | trigger | fix |
|---|---|---|---|
| HIGH | `src/hlmemo/librarian/roles.py:168` | Desteklenen eski `{"mutation":…}` proposal’ında `actions` yok: `NOT(NULL @> …)` NULL olur. Observer’da kabul edilen cevap hiçbir release yoluna seçilmez; tekrar cevap da reddedilir. | Widen filtresini NULL-safe yap; `proposal_actions()` ile her iki biçimi değerlendir. Legacy promotion/sweeper regresyonu ekle. |
| MEDIUM | `src/hlmemo/core/supersession.py:106` | Metin `API uses port 8080 and backups use port 9090.`, alıntı `API uses port 8080`, sorgu `port`: sonuç **True**. `- span_terms`, geçerli bölümdeki `port` kanıtını siliyor. | Alıntı dışındaki eşleşmeleri koru; iki bölümde de geçen query teriminde demotion uygulama. |