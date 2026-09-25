## Verdict (OK)

- 1 — FIXED: Kesim yalnız hedef head üzerinden seçiliyor; etkilenen segmentler ayrıca denetleniyor. `src/hlmemo/core/write_updates.py:377`
- 2 — FIXED: Taşıyıcının aynı `supersedes` bağlantısı, güncellemeyi `duplicate_link` ile reddettiriyor. `src/hlmemo/core/write_updates.py:386`, `:467`
- 3 — FIXED: Hedef, mevcut saatte geçerli segment; yazma saatinde tekrar doğrulanıyor. `src/hlmemo/core/write_updates.py:301`, `:371`
- 4 — FIXED: Mevcut bağlantının herhangi bir örtüşmesi çatışma sayılıyor; bağlantısız kapatma oluşmuyor. `src/hlmemo/core/write_updates.py:471`
- 5 — FIXED: Bağlantının `recorded_at` değeri geri alma zamanına katılıyor. `src/hlmemo/librarian/reversal.py:394`, `:398`

Kapsam içi yeni regresyon saptanmadı. Yedi regresyon vakası ve ilgili kod yolları incelendi; salt okunur incelemede DB’ye yazan entegrasyon testleri çalıştırılmadı.