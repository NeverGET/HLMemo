**F15 düzeltildi; (b) seçildi.** Migration gerektirmeden izin verilen dosya sınırında kalıyor. Revoked satırın adı, yeni kayıtla aynı transaction içinde `-revoked-<id>` ekiyle değiştiriliyor; eski kimlik/token revoked kalıyor. Pending/trusted ad çakışmaları hâlâ **400**.

Değişiklikler:

- [auth_queries.py:99](/Users/cemalkurt/Projects/HLMemo-bake/fix-f15/src/hlmemo/db/auth_queries.py:99): kilitli ad boşaltma, 64 karakter sınırı ve arşiv adı çakışmalarının çözümü.
- [devices.py:112](/Users/cemalkurt/Projects/HLMemo-bake/fix-f15/src/hlmemo/server/devices.py:112): atomik yeniden kayıt ve fingerprint fallback.
- [test_f15_reregister.py:68](/Users/cemalkurt/Projects/HLMemo-bake/fix-f15/tests/integration/test_f15_reregister.py:68): 11 regresyon vakası. Başlıcaları: `test_cli_default_name_and_fingerprint_reregister_after_revoke`, `test_active_name_collision_still_rejected`, `test_failed_registration_rolls_back_revoked_name_rename`.

Kabul sonuçları — tüm pytest koşuları `hlm_f15` üzerinde:

- **R1: 17 geçti**; proxy testi açık güven ayarıyla.
- **Tam suite: 470 geçti, 4 atlandı; 11 alt test geçti.** Atlananlar token gerektiren üç canlı CLI testi ve opt-in worker crash testi.
- **Ruff check + format --check temiz:** 106 dosya.
- **G3 Recall@5: 0,930; G4 p95: 283,8 ms.**

Commit oluşturulmadı. Önerilen mesaj: `fix: allow re-registration with revoked device names`