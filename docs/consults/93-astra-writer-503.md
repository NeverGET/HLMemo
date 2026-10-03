## Verdict

**GO-with-fixes** — Google’daki olay için $0 makul; mevcut eşleştirici bunu bütün sağlayıcılara güvenle genelleyemez.

1. **MEDIUM — Sağlayıcıdan bağımsız ücretsiz işlem garantisi yok.** `src/hlmemo/librarian/provider.py:1173`  
   HTTP 503 + hata nesnesi + eksik usage, üretim başlamadığını kanıtlamaz. Bir gateway, upstream işleminden sonra bu biçimde hata döndürebilir; eksik usage sıfır kullanım değildir.  
   Google’ın [billing belgesi](https://ai.google.dev/gemini-api/docs/billing#am-i-charged-for-failed-requests) başarısız 400/500 yanıtlarında token ücreti alınmadığını söylüyor; bu, Google için dayanak sağlar, tüm OpenAI-compatible servisler için değil.  
   [OpenRouter](https://openrouter.ai/docs/api/reference/errors-and-debugging) kabul sonrası hataları HTTP 200 içinde taşıdığını açıklıyor; mevcut kod bunları doğru biçimde worst-case tutuyor. Ancak aynı belge, çıktı oluşmadan prompt ücretinin doğabileceğini de belirtiyor. Billed bir gerçek HTTP 503 örneği doğrulamadım.  
   **Minimal fix:** $0 istisnasını belgelenmiş sağlayıcı sözleşmesine dayanan, varsayılanı kapalı bir profil politikasıyla etkinleştirin; bilinmeyen profiller worst-case kalsın.

2. **MEDIUM — Eşleştirici “provider’s OWN error” koşulunu doğrulamıyor.** `src/hlmemo/librarian/provider.py:159–162`  
   Çalıştırdığım doğrudan kontrolde aşağıdakilerin tamamı HTTP 503 için `True` döndü:
   - `{"error":{}}`
   - `{"error":{"code":500,"usage":{"completion_tokens":100}}}`
   - `{"error":{"code":503},"usageMetadata":{"totalTokenCount":100},"candidates":[{}]}`
   
   Bunlar gerçek faturalama vakası değil, kabul sınırının fazla geniş olduğunun yeniden üretimi. Native Google kullanım/çıktı alanları da korunmuyor.  
   **Minimal fix:** Profilin tanımladığı hata zarfını ve hata kodunu doğrulayın; kullanım/çıktı kanıtı veya belirsiz alan yapısı varsa worst-case uygulayın.

3. **LOW — D-017: Google biçimi ortak koda taşınmış.** `src/hlmemo/librarian/provider.py:149–159`  
   Model, URL veya mesaj metni sabitlenmemiş; ancak Google için eklenen liste zarfı ve buna bağlanan faturalama varsayımı bütün profillerde etkin.  
   **Minimal fix:** Ortak JSON ayrıştırıcı kalabilir; kabul edilen zarf ve ücretsiz hata politikası profilden gelsin.

4. **LOW — Tek retry ayrı, ölçülecek bir değişiklik olmalı.** `src/hlmemo/librarian/provider.py:898`  
   Bu settlement düzeltmesine zorunlu retry eklemeyin. İsteğe bağlı tek retry, hızlı ve doğrulanmış ücretsiz 503 sonrasında yararlı olabilir; fakat başarılı ikinci çağrı writer’ın tam süresini kullanıp fallback zamanını tüketebilir. Ayrıca kota ve aşırı yük baskısı artar.  
   **Minimal fix:** Profil üzerinden opt-in; bir retry, jitter, `Retry-After` uyumu, yeniden bütçe rezervasyonu ve fallback için korunmuş zaman. Süre yetmiyorsa doğrudan fallback.

5. **LOW — Negatif ve uçtan uca test kapsamı eksik.** `tests/unit/test_writer_unavailable.py:145`, `:169`  
   İkinci prose testi yalnızca bütçe eşitsizliğini kontrol ediyor; ikinci çağrıyı gerçekleştirmiyor.  
   **Minimal fix:** Yukarıdaki karşı örnekleri, aynı nesnede `choices`, `usage:null`, bilinmeyen profil ve 529 settlement testlerini ekleyin. İkinci prose çağrısını gerçekten çalıştırın; HOUR/DAY rezervasyonunun serbest kaldığını doğrulayın. Retry eklenirse deadline/cancellation ve azami iki deneme testleri gerekir.

Doğrulama: diff ve çağrı yolu incelendi; üç eşleştirici karşı örneği çalıştırıldı. Pytest/integration suite çalıştırılmadı.