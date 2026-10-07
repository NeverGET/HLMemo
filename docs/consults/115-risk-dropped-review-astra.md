**NO-GO**

- **HIGH — `src/hlmemo/core/risk_service.py:252–263`:** Başlık filtresi, librarian redaksiyonundan daha dar. `password=ExampleSecret123` içeren başlık `_dropped()` çıktısında aynen kalıyor; `Redactor` aynı parolayı gizliyor. Normal yazma filtresi bu girdiyi kabul ediyor.
  **Repro testi:** Görünür, `score >= TAU` bir adayın başlığına bu değeri koy; judge `none` döndürsün. `dropped_by_judge[0].title` içinde parolanın bulunmadığını doğrula: mevcut kodda başarısız olur. Başlıkta librarian redaksiyonunu uygula.

- **MEDIUM — `src/hlmemo/core/risk_service.py:288–301`:** Yeni boş alanlar uyarılardan önce bütçe tüketiyor. Doğrulanan örnek: `_env()`, `[_item(1,176)]`, bütçe `256`; `dropped=None` ile uyarı 242 tokenda sığıyor, `dropped=[]` ile eleniyor (`omitted: 0 → 1`). Yeni alanların mevcut uyarıları dışlamaması gerekiyor.

- **LOW — `docs/decisions/DECISIONS.md:2354`:** `warnings`/`omitted` değişmez iddiası, yukarıdaki bütçe sınırında yanlış.

**(a)** Hayır; görünürlük açısından bypass bulmadım. Aynı aday evreni, privacy-denied dışlaması ve güncel yetki/kapsam/durum kontrolü uygulanıyor.  
**(b)** `warnings` değişebilir; `verdict` ve `judged` hesaplaması değişmiyor.  
**(c)** Yeni `why` deterministik ve güvenli; `title` redaksiyonun gizleyeceği sırları sızdırabilir.  
**(d)** Tam token ölçümü ve paketleme sayaçları doğru; ancak boş alanların maliyeti uyarı önceliğini bozuyor. `dropped_omitted`, yalnızca seçilmiş ilk üç adayın bütçe nedeniyle elenenlerini sayıyor.

Doğrulama: commit `5c5a71f`, temiz worktree; altı birim testi doğrudan çağrılarak geçti, iki regresyon bellekte yeniden üretildi. Salt okunur kapsam nedeniyle DB entegrasyon testleri çalıştırılmadı.