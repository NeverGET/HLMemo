## Verdict (FIX-NEEDED)

İkili inceleme iki kusur buldu: geçmiş tarihli supersede tarihsel kayıt korumasını aşabiliyor; link-only geri alma zaman damgası nedeniyle başarısız olabiliyor. 34 saf birim vakası doğrudan çağrıyla geçti; DB entegrasyon testleri ve token sayacı doğrulanamadı.

## Findings

| severity | file:line | trigger | fix |
|---|---|---|---|
| HIGH | `src/hlmemo/core/write_updates.py:294` | Aynı öğede episode `[1 Ocak,1 Şubat)` ve fact head `[1 Şubat,∞)` bulunurken, 15 Ocak tarihli taşıyıcı fact clue’sunu supersede eder. Koruma yalnız head’i denetler; `close_record` episode’u da 15 Ocak’ta keser. Mock DB ile gerçek close yordamında yeniden üretildi. | Kesim tarihini değerlendirilen head’in geçerliliğiyle sınırla; dışında `now` kullan. Etkilenecek tüm segmentlerde tarihsel korumayı denetle. |
| MEDIUM | `src/hlmemo/librarian/reversal.py:389` | Link-only geri almada bağlantının `recorded_at` değeri `select_T` hesabına katılmaz. Saat geri giderse veya kayıt zamanı saatin ilerisindeyse `recorded_at < superseded_at` kısıtı ihlal edilir; geri alma abort eder. | Kapatılacak bağlantının `recorded_at` değerini `recorded` listesine ekle; saat gerilemesi testi ekle. |

## Safe-to-enable

**No.** Tarihsel kayıt değişmezliği ve geri almanın zaman sıralaması düzeltilmeden etkinleştirilmemeli.