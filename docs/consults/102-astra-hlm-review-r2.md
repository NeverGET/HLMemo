## Verdict

**GO-with-fixes** — Astra low + Sol xhigh değerlendirmeleri birleştirildi. Doğrulanmış HIGH yok; kalan düzeltmeler token’ın güvenli kullanımı ve üretim prosedürüyle ilgili.

1. **MEDIUM — Owner-token’ın ortamdan sızmasını önleyen kullanım talimatı eksik.**  
   `src/hlmemo/cli/hlm.py:939`, `src/hlmemo/cli/launch.py:76`, `docs/USAGE.md:120`. CLI token’ı yalnız `HLM_OWNER_TOKEN` ortam değişkeninden okuyor; ayrı bir güvenli depolama mekanizması yok. Üç `hlm` launcher’ı token’ı siliyor; kayıtlı MCP yapılandırmaları yalnız bearer taşıyor (`cli/mcp_register.py:28`). Ancak genel shell ortamına export edilirse doğrudan başlatılan ajan token’ı miras alıp kendi HTTP çağrısını yapabilir.  
   **Minimal fix:** Sırrı yalnız review sürecine sağlayan kullanım yöntemini belgeleyin; genel export, shell başlangıç dosyası ve agent MCP yapılandırmasına koymayın. Stripping, aynı kullanıcıyla okunabilen sır dosyalarını korumaz.

2. **LOW — Üretim provisioning/rotation prosedürü eksik.**  
   `deploy/api.env.example:1`, `deploy/compose.prod.yaml:94`, `src/hlmemo/server/app.py:436`. Token yoksa startup bozulmaz; `hlm.questions` reddedilir ve CLI gerekçesini göstererek notices fallback’ine geçer.  
   **Minimal fix:** Bağımsız, rastgele ≥32 karakter token’ı `api.env` kurulumuna ekleyin. Rotation’da API konteynerini **recreate** edin, owner ortamını güncelleyin; yeni token’ın kabulünü ve eskisinin reddini doğrulayın. Yalnız dosyayı değiştirmek veya container restart yeterli değildir. Secret’ın LLM fingerprint/manifest’e eklenmesi gerekmiyor (`deploy/scripts/llm_env_release.py:137`).

3. **Round-1 HIGH: CLOSED — Bearer ile doğrudan çağrı engellendi.**  
   `src/hlmemo/server/tools/__init__.py:90`, `src/hlmemo/server/mcp_server.py:229`. Owner kontrolü handler’dan önce çalışıyor; istemci etiketi taklidi yeterli değil. Doğru owner token cihazın READ/scope sınırlarını kaldırmıyor. Genel “ajan sırrı elde edemez” garantisi ise 1. maddede belirtilen operasyonel sınıra bağlı.

4. **Round-1 MEDIUM: CLOSED — Sayfalama ve fallback düzeltildi.**  
   `src/hlmemo/librarian/questions.py:738,811,816`, `src/hlmemo/cli/review.py:278`. Keyset, kapanan sorular nedeniyle kayıt atlamıyor; bütçe daralınca son **döndürülen** sorudan devam ediyor. Tek soru sığmıyorsa `E_BUDGET_TOO_SMALL`; fallback cursor’ı açıkça reddediyor.

5. **INFO — Cursor değiştirme yetki kazandırmıyor.**  
   `src/hlmemo/librarian/questions.py:615,704,734`. Farklı proje/tür prefix’i reddediliyor. Prefix’i mevcut listeye uyarlayıp sahte konum üretmek yalnız başlangıç konumunu değiştirir; görünürlük her sayfada uygulanır. Token generation’a bağlanmaması bu kapsamda güvenlik engeli değil.

6. **INFO — Header/error/timing kontrolü uygun.**  
   `src/hlmemo/server/mcp_server.py:168,263`, `src/hlmemo/auth/tokens.py:29`. İncelenen uygulama ve Caddy yapılandırmasında owner header’ın loglanması/echo edilmesi görülmedi. Ret mesajı sır içermiyor; eksik/yanlış/kısa token aynı hatayı alıyor. Karşılaştırma `hmac.compare_digest` kullanıyor.

7. **INFO — `hlm.export` için şimdi owner gate şart değil.**  
   `src/hlmemo/core/export_service.py:143`, `src/hlmemo/server/tools/__init__.py:78`. Belirtilen sözleşmede cihazın zaten okuyabildiği içeriğin toplu okunması yetki aşımı değil. Export’u da ajanlara yasaklamak ayrı bir ürün politikası değişikliği olur; “hidden” erişim kontrolü sayılmamalı.

**Doğrulama:** Owner kapısı 4 test PASS; iki paging/fallback test fonksiyonu ve gerçek cursor parser’ın proje/tür ret kontrolleri PASS. Gerçek DB/wire ve deployment testleri bu turda çalıştırılmadı. Handler SELECT-only; mevcut HTTP katmanının `last_seen_at` güncellemesi devam ediyor.