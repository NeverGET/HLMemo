**GO**

HIGH / MEDIUM bulgu yok.

LOW — `src/hlmemo/core/write_updates.py:203`: threat model’in #3 maddesi body-first sözleşmesiyle çelişiyor. Tetikleyici: title=`Cache TTL`, body=`Cache TTL is 60 seconds.`, span=`Cache TTL`; body eşleşmesi kabul edilir. Benzersizliğin seçilen alan içinde arandığını threat model’de netleştirin; global benzersizlik uygulamak mevcut body davranışını değiştirir.

(a) **Hayır.** Görünürlük, proje yetkisi, device scope ve kapalı/superseded hedef kontrolleri title aramasından önce uygulanıyor (`write_updates.py:312–400`). Mutasyon için `replacement_visibility` korunuyor (`217–218`); historical hedef hâlâ yalnızca link alıyor (`406–407`).

(b) **Body’de bulunan span için kabul/ret sonucu değişmiyor.** Tekrar, kelime sınırı ve NFC retleri korunuyor (`200–224`). Body’de bulunmayan title span’larının sonucu tasarım gereği değişiyor; mevcut `span_not_found` hint metni de değişiyor.

(c) **Hayır.** Title eşleşmesinde revise erken reddediliyor (`204–205`); title quote yalnızca whole-scope supersede linkine girebiliyor (`511–517`). Title yeniden yazılmıyor.

(d) **İki alanda birer kez bulunması kabul edilebilir:** body içinde unique sayılır. **Alanlar arasında eşleşme mümkün değil**; metinler birleştirilmiyor (`200–209`).

(e) **Evet.** Pessimistic bound, yeni hint dahil `REASONS` üzerinden dinamik hesaplanıyor (`639–658`); tüm reason/hint’leri kapsayan sınır testi geçti. Replay saklanan ack’i kullanmaya devam ediyor.

Doğrulama: aday export üzerinde **73 unit test geçti**; bağımsız guard incelemesi tamamlandı. DB entegrasyon/replay testlerini çalıştırmadım. Dosya değiştirilmedi.