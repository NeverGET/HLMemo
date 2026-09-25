## Verdict (GO-WITH-CHANGES)
1. **Clue biçimi: GO.** `v<vid>[.<chunk>]` mantıksal kimliği ve sabit expected_version’ı belirlemeli; ayrıca verilen çelişkili expected_version reddedilmeli.
2. **Per-item updates: GO.** Replacement yalnız taşıyan öğenin BODY’sinde aranmalı; başka batch öğesindeki eşleşme yeterli değil.
3. **Cut / tarihsel türler: CHANGES.** Geçmişe tarihli düzeltme mevcut write yetkisiyle tutarlı; bitemporal kayıt eski bilgiyi korur. Aşağıdaki iki sınır düzeltilmeli.

- **MEDIUM | cut kuralı + core/temporal.py:validate_interval |** Taşıyan öğe `valid_from=now+4 dakika` ile kabul edilir; fallback `cut=now` supersede hedefini yeni öğe başlamadan kapatır, dört dakikalık boşluk yaratır; revise ise gelecek bilgiyi erken etkinleştirir. **Değişiklik:** Gelecek başlangıçlı taşıyan öğenin mutasyon yapan update’ini bağımsız reddet; yeni öğeyi yaz.
- **MEDIUM | historical-kinds kontrolü + config.py:_revise_kinds |** Yapılandırma `episode` kabul ediyor; yalnız `revise.revisable` kullanmak “episode asla kapanmaz” garantisini bozar. **Değişiklik:** Episode korumasını yapılandırmadan bağımsız uygula; iki mod da link-only olsun.

Yetki ve stale-clue kontrolleri tasarımda yeterli: kilit sonrası görünürlük, tüm hedef projelerinde write, D-083 ve beklenen head kontrolü korunuyor.
Batch çatışma kontrolü tüm batch’in değişecek kimliklerini kapsadığında A↔B zinciri engellenir; yalnız önce işlenen öğelere bakılmamalı.
Tek sıralı logical-id kilit kümesi karşılıklı yazma deadlock’unu önler; item→policy sırası D-095 ile uyumlu.
Somut mutation kayıtlarının live/replay’de aynı sırayla uygulanmasında ayrı bir tutarsızlık bulmadım; semantik retler mutasyondan önce kesinleşmeli.
G-SURF: 32 token pay bir doğruluk kusuru değil, bakım açısından dar. Açıklamayı “Per-item updates: target clue, quoted old_span, mode revise or supersede; revise replacement must occur verbatim in body. Results: applied, linked, rejected.” biçiminde kısaltıp gate’i yeniden ölçün.
İnceleme sınırı: Export’taki DECISIONS.md D-104’te bitiyor; D-110/D-113/D-118 satırları yok. İlgili kod ve tasarım notu incelendi; 2968 ölçümü yeniden çalıştırılmadı.