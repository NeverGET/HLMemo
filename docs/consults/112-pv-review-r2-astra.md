**NO-GO**

- **fixed — Astra #1:** Ham alanlar bütünüyle taranıyor; `src/hlmemo/core/write_service.py:363`, `src/hlmemo/core/secret_guard.py:48`.
- **fixed — Astra #2:** Secret biçimli anahtarlar doğrulamadan önce yakalanıyor; exception zinciri kaldırılmış: `src/hlmemo/core/secret_guard.py:66`, `src/hlmemo/core/write_models.py:486`.
- **fixed — Astra #3:** JWT kuralı kaldırılmış: `src/hlmemo/core/secret_guard.py:26`.
- **fixed — Astra #4:** PV-5 etiketleri strip/casefold ile karşılaştırılıyor: `src/hlmemo/core/write_models.py:61`.
- **fixed — Sol #1:** `updates[].replacement` dahil ham payload taranıyor: `src/hlmemo/core/write_service.py:363`.
- **not fixed — Sol #2:** Tasarım gereği korunmuş, yeni bulgu değil; sahibine sunulan artık risk: `src/hlmemo/core/write_service.py:508`, `docs/protocol/HLMEMO-PROTOCOL.md:374`.
- **fixed — Sol #3:** `project`/`project_ids` yetkilendirmeden önce taranıyor: `src/hlmemo/core/write_service.py:363`.
- **fixed — Sol #4:** JWT dokümantasyon örnekleri kabul ediliyor: `src/hlmemo/core/secret_guard.py:26`.
- **fixed — Sol #5:** Boş lesson parçaları ve `""` için `reason=blank`: `src/hlmemo/core/lesson_service.py:85`, `src/hlmemo/core/write_models.py:454`.

**Yeni bulgu — HIGH:** Reddedilen secret hâlâ INFO günlüğüne yazılıyor: `src/hlmemo/server/mcp_server.py:199`, `:268`. Tetikleyici: geçerli `memory.register_lesson` çağrısında `X-HLM-Client: ghp_` + 36 adet `A`. Header’dan türetilen `client`, PV-1 tarafından reddediliyor; fakat `finally` aynı değeri filtresiz kaydediyor. Gerçek handler/service/scan zinciriyle, yalnız bağlantı ve bağımlılık kurulumu taklit edilerek doğrulandı: `secret_in_error=False`, `secret_in_INFO_log=True`. Günlükleme yolu önceden mevcut; geniş taramanın gideremediği sızıntı.

Doğrulama: **85 birim testi geçti** (`--noconftest`; DB entegrasyonu çalıştırılmadı). Ek 2.053 UUID/SHA-256/base64/URL/kod örneğinde **0 yanlış ret**. 64.000 karakter × 50 öğenin mevcut çift taraması, beşer tekrarda **0,197–0,223 saniye** sürdü. Verilen 49.932 alanlık gerçek veri ölçümü yeniden çalıştırılmadı.