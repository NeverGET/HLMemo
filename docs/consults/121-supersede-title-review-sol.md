**NO-GO**

**HIGH — Mevcut body-span isteklerinin sonucu değişiyor.** [src/hlmemo/core/write_updates.py:644](/private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/rev121/src/hlmemo/core/write_updates.py:644): yeni hint, pessimistic ack’i **5 token/update** büyütüyor; geçerli istekler bütçeden reddedilebiliyor.

Repro test şekli: aynı proje/cihaz kapsamındaki üç ayrı açık, görünür fact; tek carrier’da üç geçerli body alıntılı `supersede`; UUID `00000000-0000-4000-8000-000000000001`, `token_budget=322`. Gerçek ack ön-kontrolünde base **PASS**, aday **`E_BUDGET_TOO_SMALL`, min=337**. Hint’i eski maksimumun altında tutup bu bütçe regresyonunu test edin.

MEDIUM/LOW: Ek bulgu yok.

(a) **Hayır.** Başlık yolu görünürlük, proje, cihaz, current/open/superseded kontrollerini atlamıyor; historical hedefler yine yalnızca link alıyor.

(b) **Evet, bütçe nedeniyle.** Yukarıdaki HIGH geçerli body isteklerini etkiliyor. Span guard kararları aynı kalıyor.

(c) **Hayır.** Title quote yalnızca supersede/whole link’e gider; revise başlığı değiştirmez.

(d) **Evet, seçilen alan içinde unique sayılır:** title ve body’de birer occurrence varsa body yolu kabul edilir. Bu açık body-first sözleşmesidir; alanlar arasında eşleşme yapılmaz.

(e) **Evet.** Yeni ack kendi pessimistic bound’u içinde kalıyor; sorun bound’un büyümesi. Replay kayıtlı ack’i kullanıyor.

Doğrulama: **73 unit test geçti**, **4.224 guard diferansiyel vaka** ve **322→337** bütçe reproduksiyonu doğrulandı. DB/replay entegrasyonu çalıştırılmadı.