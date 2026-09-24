## Verdict (FIX-NEEDED)

Astra-low ve Sol-xhigh incelemeleri birleştirildi: kimlik bilgisi sızıntısı ve komut değiştirme açıkları sürüyor. 138 izole birim kontrolü geçti; token ölçümü ortam kısıtına takıldı. DB entegrasyonu ve 3 saniyelik fallback testi yeniden çalıştırılamadı. Dosyalar değiştirilmedi.

## Per-claim

1. **PARTIAL** — Altı eski vaka düzelmiş; ASCII Türkçe ve apostrof ekleri doğru. Yeni komut bypass’ları mevcut: `src/hlmemo/core/language.py:335`.
2. **PARTIAL** — Algılanan sırlar bütün provider/fallback denemelerinden önce engelleniyor; algılama eksik: `src/hlmemo/librarian/query_rewrite.py:107,119,526`.
3. **FIXED/OK** — Task fallback, latency politikası ve profil bazlı breaker korunmuş; idempotent çözümleme zinciri çoğaltmıyor: `src/hlmemo/librarian/query_rewrite.py:189,549`; `src/hlmemo/librarian/provider.py:384`.
4. **FIXED/OK** — Deadline enqueue’dan başlıyor; başarısız schedule → unavailable: `src/hlmemo/librarian/query_rewrite.py:367,389`; `src/hlmemo/core/read_service.py:348`.
5. **FIXED/OK** — Günlük kapsamlı lineage; atomik 60/300 kota; ortak bütçede %20 tavan: `src/hlmemo/librarian/query_rewrite.py:128,413`; `src/hlmemo/librarian/budget.py:79`. Token rotasyonu cihaz kotasını, cihaz rotasyonu proje kotasını aşmıyor. Librarian toplam tüketimi %20’yi geçtiğinde rewrite da duruyor; pencere sınırında yeni kota açılıyor.
6. **FIXED/OK** — Demotion özgün+İngilizce terim birleşimini alıyor: `src/hlmemo/core/read_service.py:291,327`.
7. **FIXED/OK** — Yalnız config task-name kontrolü daraltılmış; model-id taraması değişmemiş ve testi geçti: `tests/unit/test_wf_task_fallbacks.py:356,374`.

Flag-off MCP byte-eşitlik testi geçti; retrieval diff’inde kapalı yolu değiştiren davranış bulunmadı. DB sonuçlarının byte eşitliği yeniden ölçülmedi.

## New findings

| severity | file:line | trigger | fix |
|---|---|---|---|
| HIGH | `src/hlmemo/librarian/query_rewrite.py:107` | Türkçe sorguda `DB_PASSWORD=abc123`: credential=False, redaksiyon=0; kısa parola gönderilebilir. | `_` içeren env isimlerini ve kısa değerleri gönderim öncesinde engelle. |
| HIGH | `src/hlmemo/librarian/query_rewrite.py:119` | `şifrem çiçek` ve `gizli anahtar hunter2`: credential=False, redaksiyon=0. | Doğal-kelime istisnasını daralt; bileşik credential ifadelerinde sonraki değeri kaçırma. |
| HIGH | `src/hlmemo/librarian/redact.py:51` | Türkçe sorgudaki `HTTPS://user:pass@host/path` iki filtreden de geçiyor. | URL şemasını büyük/küçük harften bağımsız eşleştir; userinfo içeren sorguyu engelle. |
| HIGH | `src/hlmemo/core/language.py:335,356` | `git -C "/tmp" status` → `git -C "/tmp" log` kabul ediliyor. `./manage.py migrate`→`flush`, `bun install`→`update` de geçiyor. | Alıntı argümanından sonra komut bağlamını sürdür; executable yollarını ve belirsiz CLI başlarını koru. |

## Ship-with-flag-ON

**Hayır.** Gerçek credential değerleri sağlayıcıya çıkabiliyor ve değiştirilmiş komutlar rewrite guard tarafından kabul ediliyor.