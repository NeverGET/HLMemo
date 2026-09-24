## Verdict (FIX-NEEDED)

Rewrite ON ile yayımlanmamalı: iki bağımsız inceleme gizlilik ve token koruması açıklarını doğruladı. Bayraklar kapalıyken MCP yüzeyi ve D-087 işlem sırası korunuyor; İngilizce dal aynı görünürlük filtrelerini kullanıyor. Cap açıkken D-087 sınırı, cap uygulanmış listeye göredir. 98 birim kontrolü geçti; bir token ölçümü ortam kısıtına takıldı. Entegrasyon/gecikme kapıları yeniden çalıştırılmadı; dosyalar değiştirilmedi.

## Findings

| severity | file:line | trigger (concrete scenario) | fix |
|---|---|---|---|
| HIGH | `src/hlmemo/librarian/query_rewrite.py:389`; `src/hlmemo/librarian/redact.py:56` | `Veritabanı için parola: hunter2 neden çalışmıyor ve nasıl düzeltilir` için dil `tr`, redaksiyon sayısı **0**. Yetkili projede ham parola sağlayıcıya gönderilir. | Rewrite için açık kimlik bilgisi ifadelerini kısa değerler dahil ihtiyatlı biçimde reddet; sıfır HTTP çağrısını test et. |
| HIGH | `src/hlmemo/core/language.py:248,264,309` | `src/main dizini için hangi ayarlar kullanılıyor` → `Which settings are used for src/other directory` kabul ediliyor. Ayrıca `"x"` → `"y"` ve `git status` → `git log` değişiklikleri geçiyor. | Göreli yolları ve komutları korunan span olarak çıkar; tek karakterlik alıntıları da koru. Bu karşı örnekleri regresyon testlerine ekle. |
| MEDIUM | `src/hlmemo/librarian/query_rewrite.py:201,276,402` | Yetkili okuyucunun sürekli farklı Türkçe sorguları, tamamlanan görevlerin yerini doldurarak ortak saat/gün/ay bütçesini tüketebilir ve librarian çağrılarını durdurabilir. 32 görev/2 eşzamanlılık sınırı toplam tüketimi ayırmıyor. | Cihaz/proje bazında rewrite kotası ve librarian için ayrılmış bütçe payı uygula. |

## Ship-with-flag-ON assessment

**Hayır** — açık parola sağlayıcıya çıkabiliyor ve gerçek yol/komut değişiklikleri koruma denetiminden geçiyor.