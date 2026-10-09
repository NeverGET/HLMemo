## Verdict

**GO-with-fixes** — MCP sınırı düzeltilmiş; owner token’ın yerel kullanım/dağıtım sözleşmesi tamamlanmalı.

1. **MEDIUM — Round-1 owner izolasyonu PARTIAL; doğrudan MCP bypass CLOSED.** `src/hlmemo/server/mcp_server.py:229`, `src/hlmemo/cli/hlm.py:939`, `src/hlmemo/cli/launch.py:76`, `docs/USAGE.md:119`.
   Device bearer tek başına artık yeterli değil. Ancak CLI token’ı yalnız ortamdan okuyor; launcher yalnız `hlm claude|codex|agy` çocuklarından siliyor. Belgelenen doğrudan agent başlatmaları, shell’de export edilmiş owner token’ını devralabilir.
   **Kontrol:** Sentinelli doğrudan subprocess `owner_available=True`, `child_env` üzerinden subprocess `False` döndürdü.
   **Minimal fix:** Token’ı global shell ortamına/profile dosyasına koymayı yasaklayan, yalnız `hlm review` çağrısına sağlayan kullanım talimatı; doğrudan agent başlangıçlarını sanitizasyonla belgele. Aynı kullanıcı altında sınırsız shell/dosya erişimine karşı launcher tek başına güvenlik sınırı değildir.

2. **INFO — Round-1 paging CLOSED.** `src/hlmemo/librarian/questions.py:615`, `:738`, `:811`, `:816`; `src/hlmemo/cli/review.py:278`.
   Keyset son dönen sorudan ilerliyor; kapanan sorular offset kaymasına yol açmıyor. Sığmayan son kayıtlar sonraki sayfaya bırakılıyor; tek kayıt bile sığmazsa `E_BUDGET_TOO_SMALL`. Fallback cursor’ı reddediyor.
   Proje/kind değiştirilmeden kullanılan başka listing cursor’ı reddedildi. Prefix’i değiştirilen/uydurulan geçerli cursor yalnız konum seçer; `_VISIBLE` ve proje READ kontrolü yeniden uygulanır. Token-generation bağı gerekmiyor.

3. **LOW — Provisioning ve rotation runbook’u eksik.** `src/hlmemo/config.py:355`, `src/hlmemo/server/mcp_server.py:175`, `src/hlmemo/cli/hlm.py:939`.
   Token varsayılanı `None`; eksik/boş/<32 karakter halinde yalnız owner araç reddedilir, startup validation hatası yaratmaz. CLI bu durumda sınırlı notices fallback’ine döner; operatör tam listeyi göremeyebilir.
   **Minimal fix:** API ortamına yüksek entropili token ekleme, owner tarafında güvenli sağlama ve API instance’larını yeni ayarla yeniden başlatma/eskilerini boşaltma adımlarını kaydet. Rotation sonrası eski token reddedilir; device rotation owner token’ını değiştirmez. Secret’ı public fingerprint/manifest içine koyma.

4. **INFO — Header değerlendirmesi geçti.** `src/hlmemo/server/mcp_server.py:168`, `src/hlmemo/auth/tokens.py:29`, `src/hlmemo/cli/mcp_register.py:29`, `deploy/Caddyfile:1`.
   Token karşılaştırması `hmac.compare_digest` kullanıyor; hata metni secret’ı veya hangi kontrolün başarısız olduğunu göstermiyor. İncelenen application logging/Caddy yapılandırmasında owner header log/echo edilmedi.
   MCP kayıtları yalnız device bearer içeriyor; tool arguments owner header üretemiyor. Boş/eksik token yollarının kısa devresi secret içeriğini açığa çıkaran karşılaştırma değildir.

5. **INFO — `hlm.export` gate’i bu release için zorunlu değil.** `src/hlmemo/server/tools/__init__.py:78`.
   Mevcut davranış bulk-read, yetki yükseltme değil. Owner-only olduğu ürün sözleşmesi olarak isteniyorsa aynı gate uygulanmalı; ancak `hlm export` CLI ve review fallback’i de token ile güncellenmeli. Sadece server flag’ini değiştirmek mevcut akışları bozar.

Kontroller: dört owner-tool unit fonksiyonu doğrudan çalıştırıldı ve geçti; cursor proje/kind retleri, sentinel env aktarımı ve fix diff whitespace kontrolü geçti. Pytest temp/Docker fixture kısıtına takıldı; gerçek DB/wire ve production rotation doğrulanmadı.
