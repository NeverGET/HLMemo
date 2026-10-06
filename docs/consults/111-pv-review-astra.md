**NO-GO**

1. **HIGH — PV-1 ham event alanlarını kaçırıyor.** `src/hlmemo/core/write_service.py:481`, `:1219`: yalnızca title/body/tags taranıyor; `updates[].replacement`, `source.path`, `describes[]`, `client` taranmadan `payload.request` içine kaydediliyor.
   Tetikleyici: `S="ghp_"+"A"*36`; temiz item’a `updates:[{item:1,expected_version:1,old_span:"old",mode:"revise",replacement:S}]` ekle. Update reddedilse bile taşıyıcı write ve ham payload kaydedilebilir.
   Repro testi: mevcut hedefi oluştur; bu write’ı commit et; update’in reddedildiğini ve event JSON’unda `S` bulunduğunu doğrula. `source.path:S` de doğrudan örnektir.

2. **HIGH — Hata sanitizasyonu secret sızdırabiliyor; açık önceden mevcut.** `src/hlmemo/core/write_models.py:421`, `:437`, `:472`.
   Tetikleyici: temiz item’a anahtarı `S` olan fazladan alan eklemek, `S` değerini mesaj/details içinde döndürüyor; `body:[S]` ise zincirlenen `ValidationError` exception string’ine sızdırıyor.
   Repro testi: `parse_request` hatasını yakala; `S in json.dumps(err.as_error())` ve ikinci girdide `S in str(err.__cause__)` doğrula. Her iki yol bağımsız çalıştırıldı.

3. **HIGH — Meşru JWT dokümantasyon örnekleri reddediliyor.** `src/hlmemo/core/secret_guard.py:25`.
   Tetikleyici: body içinde standart JWT örneği `<jwt.io public sample token, elided for gitleaks>`.
   Repro testi: “JWT format örneği: …” gövdeli meşru write’ın kabulünü bekle; mevcut kontrol `secret_pattern/jwt` döndürüyor. Saf fonksiyonla doğrulandı; 1.629 örneklik ölçüm bu sınıfı kapsamıyor.

4. **HIGH — Büyük/küçük harf varyantları PV-5’i atlıyor.** `src/hlmemo/core/write_models.py:60`.
   Tetikleyici: `kind:"lesson", tags:["Active","resolved"]`; karşılaştırma harfe duyarlı.
   Repro testi: bu item’ın `lesson_status_conflict` ile reddini bekle; mevcut `parse_request` kabul ediyor. Saf fonksiyonla doğrulandı.

(a) PV tarafından tamamen reddedilen write için yeni item/event/request kaydı veya metin içeren sunucu logu bulmadım; ancak reddedilen alt-update’in secret’ı başarılı taşıyıcının event’inde kalabilir.
(b) Evet: fazladan alan adından message/details’e ve ham ValidationError üzerinden exception string’ine; yakalanan ToolError için sunucu loguna yazım gözlenmedi.
(c) Hayır: beş kontrol replay’den erişilmiyor; incelenen diff tarihsel replay yolunu değiştirmiyor. DB üzerinde byte-identical testi yeniden çalıştırılmadı.
(d) PV-1: replacement/source.path/describes/client; PV-5: tags harf varyantları; PV-2: sahte source, açıkça tanımlanmış import muafiyetinden yararlanıyor. PV-3/PV-4 için sıradan desteklenen girdi bypass’ı bulunmadı.
(e) Evet: kapalı hata kodları ve HTTP eşlemesi değişmemiş.

Doğrulama: temiz worktree, HEAD `77d3404`; kod incelemesi ve salt okunur Python reproduksiyonları. Pytest, `HLM_TEST_DSN` tanımlı olmadığından çalışmadı.