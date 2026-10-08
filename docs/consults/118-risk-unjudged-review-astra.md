**NO-GO**

- **HIGH — `src/hlmemo/core/retrieval.py:303,314–318`:** Regex gerçek frontmatter olmayan gövdeyi de kaldırıyor. Tetikleyici: yatay çizgiler arasında girintili kod veya Markdown listesi.
  Yeniden üreten test: `s = "---\n    print(123)\n---\nAfter"; assert preview_text(s, 0) == s`
  Test başarısız: gerçek çıktı `"After"`. Ayrıca `title: [unterminated` gibi geçersiz YAML da kaldırılıyor. Frontmatter’ı yalnızca bu satır biçimleriyle tanımak yeterli değil.

**(a)** Görünürlük bakımından **hayır**: `unjudged` aynı aday kümesinden geliyor; proje/device/isolation/current filtreleri ve judge çağrısından sonraki D-062 kontrolü uygulanıyor.

**(b)** İncelenen yollarda **değişiklik bulmadım**. Warnings önce aynı şekilde paketleniyor; ek alanlar kalan bütçeye sığdırılıyor. `verdict/warnings/omitted/judged` ve mevcut bütçe hatası davranışı korunuyor; limit aşımı bulmadım.

**(c)** Mutlak anlamda **evet**: kabul edilmiş JSON/çok sözcüklü quoted-value redaksiyon boşlukları sürüyor; mevcut `_warning` da ham başlık döndürüyor (`risk_service.py:267`). Yeni `unjudged` yolu `_safe_title` kullanıyor; kabul edilenler dışında yeni bir sızıntı doğrulamadım.

**(d)** Gövde kaybı **evet**, yukarıdaki HIGH ile doğrulandı. Süper-lineer backtracking bulmadım; incelenen alternatifler satır bazında ayrışıyor. Kapanışı olmayan 1k–8k girdilerde süre yaklaşık doğrusal arttı.

Doğrulama: aday dosyadan çıkarılan gerçek fonksiyonla karşı örnekler çalıştırıldı; bağımsız risk incelemesi yapıldı. Pytest, salt-okunur ortamda tiktoken geçici dizin gereksinimi nedeniyle collection aşamasında durdu; entegrasyon testleri çalıştırılmadı.