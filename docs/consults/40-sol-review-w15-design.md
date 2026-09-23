1. **ACCEPT B.** `hlm.export` yalnızca doğrudan CLI çağrısı için kaydedilsin; `tools/list` dışında kalması CC-4’ü korur. Cursor proje, görünüm, filtreler ve ilk sayfada dondurulan `valid_at/known_at` değerlerine bağlanmalı; her sayfada yetki yeniden denetlenmeli.

2. **CHANGE — kaynak anahtarı sahipliğini veritabanında tekil bir projeksiyonla koruyun.** Advisory lock yarışları sıralar, fakat bütün yazma yollarının aynı kontrolü uyguladığına dair kalıcı güvence vermez. Aynı logical item’ın birden fazla güncel zaman dilimine izin verin.

3. **ACCEPT.** İçerik hash’i mtime değişimini doğru biçimde atlar. Aynı `request_id` ile yeniden gönderimde *birebir aynı payload* kullanılmalı; farklı metadata ile eşzamanlı gönderim `E_REQUEST_ID_CONFLICT` üretirse manifest yeniden okunmalı.

4. **ACCEPT.** Gelecek tarihli kaydı reddetmek kanıtı kaybetmeden görünür hata verir. Aynı tarih ve slug’a sahip başlıklar için anchor çakışması deterministik biçimde çözülmeli.

5. **CHANGE — mevcut projelerin backfill’ini DDL kilidi bırakıldıktan sonra idempotent, proje başına transaction’larla yapın.** Tokenizer ve chunker çalışırken migration’ın tablo kilidini tutması dağıtımda gereksiz uzun blokaj yaratır. Yeni proje oluşturma akışındaki aynı transaction kalsın.

6. **CHANGE — düz `source_key` + `CHECK … NOT VALID`, ardından `VALIDATE` tercih edin.** Mevcut tabloda `source` NULL olduğundan rewrite gerekmez; yeni yazımlar, replay ve survivor kopyaları anahtarı aynı ifadeyle doldurmalı. İndeks **unique olmamalı**: aynı logical item’ın birden fazla güncel temporal dilimi olabilir.

**Dal reddi nedenleri:** `hlm.export` yetki/snapshot sınırını aşarsa, farklı logical item’lar aynı güncel kaynak anahtarını tutabilirse, ya da G6 replay `source`/`code_refs`/skeleton card’ı birebir kuramazsa reddederim. `code_refs`, replay sırasında `memory_versions` ile birlikte truncate edilip yeniden kurulmalı.