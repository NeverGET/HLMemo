## Verdict

**NO-GO** — Astra/Sol incelemeleri birleştirildi. F1’de yanlış literal kabulü, F2’de desteklenmeyen ana cümlenin yeniden eklenmesi çalıştırılarak doğrulandı.

1. **HIGH — `src/hlmemo/librarian/tasks/research.py:2091` — URL değiştiriliyor.**  
   Kaynak: `Use the URL https://example.com/?filter=a|b&role=admin exactly.`  
   Cevap: `Use https://example.com/?filter=b&role=admin.` → `_prose_keep`: **kept**. Gerçek query değeri alternatif notasyon sanılıyor.  
   **Minimal düzeltme:** URL query/fragment bölümlerini expansion dışında bırak; karşıörneği regresyon testine ekle.

2. **HIGH — `src/hlmemo/librarian/tasks/research.py:2077` — İndeksleme yeni identifier üretiyor.**  
   Kaynak: ``Read `arr[index]`.``; cevap: ``Read `arrindex`.`` → **kept**. Ayrıca `foo[bar[baz]]` → `foo`, `foobar`, `foobarbaz`.  
   **Minimal düzeltme:** genel `x[word]` dönüşümünü kaldır veya açık optional-path biçimiyle sınırla; iç içe/dengesiz parantezleri reddet.

3. **HIGH — `src/hlmemo/librarian/tasks/research.py:3045`; `src/hlmemo/core/research_service.py:1729` — Restate yanlış anlamı geri ekleyebiliyor.**  
   Kaynak: `` `HLM_RESEARCH_ENABLED` enables the librarian. The release remains manual.``  
   Taslak: ``Decision `D-077` disables the librarian. The release remains manual.`` İlk cümle düşüyor.  
   Restate: `` `HLM_RESEARCH_ENABLED` disables the librarian.`` → `restate_ok=True`; final cevap yanlış cümleyi içeriyor, `main_repaired=True`.  
   Bu, mevcut literal doğrulamasının anlamsal sınırı; F2 bunu yeni otomatik ekleme yoluna taşıyor.  
   **Minimal düzeltme:** onarılan ana cümlede birebir kaynak cümlesi şartı koy veya eklemeden önce anlamsal destek denetimi uygula.

4. **MEDIUM — `src/hlmemo/librarian/tasks/research.py:2556` — Bağımsız cümle siliniyor.**  
   Desteksiz bir cümleden sonra gelen, kaynakta birebir bulunan `This API supports pagination.` da düşüyor; paragraf sınırı korumuyor.  
   **Minimal düzeltme:** yalnız açık geri gönderim kalıplarını ele; “This API” gibi bağımsız isimli başlangıçları koru.

5. **MEDIUM — `src/hlmemo/librarian/tasks/research.py:887` — Önizleme gerçek içeriği gizliyor.**  
   `VERIFIED backups are corrupt.\nRestore must remain disabled.` girdisi yalnız ikinci cümleye dönüşüyor.  
   **Minimal düzeltme:** migration notlarını belgelenmiş tarih/ayraç biçimiyle eşleştir; yalnız `VERIFIED` başlangıcını yeterli sayma.

6. **MEDIUM, bilinen — `src/hlmemo/core/research_service.py:353` — Birleşik pencerenin handle’ı eksik kapsamlı.**  
   `[v1.1, v1.3]` → `v1.1`, fakat metin aralığı `0..4`. Aynı handle’ı açmak 3–4. chunk’lardaki alıntıyı getirmiyor.  
   **Minimal düzeltme:** katkıda bulunan handle’ları kaynak olarak koru veya tek handle’ın temsil edemediği pencereleri ayrı tut.

**Diğer kontroller:** A/B birleşiminde kayıp davranış, restate bütçe bypass’ı veya yeni kapsam sızıntısı bulunmadı. `MAIN_REPAIR` yokken `True`; absent/absent fingerprint eşitliği doğrulandı. Prompt içeriği değiştiği için eski prose cassette anahtarları değişir; claims/cite korunuyor, prose replay ayrıca doğrulanmalı.

**Doğrulama sınırı:** R4.2’nin 19 birim testi geçti; yukarıdaki karşıörnekler ayrıca çalıştırıldı. Prod başlangıcı, Docker entegrasyonu ve rollback çalıştırılmadı; geniş testler salt okunur ortamın geçici dosya/Docker kısıtlarına takıldı. Dosyalar değiştirilmedi.