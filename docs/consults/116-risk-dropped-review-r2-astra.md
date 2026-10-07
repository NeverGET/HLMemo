**NO-GO**

- Astra HIGH — fixed | `src/hlmemo/core/risk_service.py:260`: bildirilen `password=...` örneği redakte ediliyor.
- Astra MEDIUM — fixed | `src/hlmemo/core/risk_service.py:304`: dropped alanları warnings/omitted/bütçe hatalarını değiştirmiyor.
- Astra LOW — fixed | `docs/decisions/DECISIONS.md:2352`: paketleme kontratı düzeltmeyle uyumlu.
- Sol HIGH — fixed | `src/hlmemo/core/risk_service.py:260`: bildirilen DSN parolası redakte ediliyor.
- Sol MEDIUM (bütçe) — fixed | `src/hlmemo/core/risk_service.py:320`: dropped alanları warning paketlemesinden sonra ayrı kopyada deneniyor.
- Sol MEDIUM (kapsam) — partially | `tests/integration/test_risk_dropped_by_judge.py:182`: testler eklendi; `assert above` yalnızca bir withheld adayın eşiği geçtiğini kanıtlıyor, her kategoriyi değil.
- Sol LOW (özet) — fixed | `src/hlmemo/cli/preflight.py:181`: nonempty dropped listesi özette belirtiliyor.
- Sol LOW (why kontratı) — fixed | `docs/decisions/DECISIONS.md:2350`: deterministik açıklama açıkça belgelenmiş.

- **NEW HIGH — `src/hlmemo/core/risk_service.py:260`:** Eşik üstü, judge’ın eşleştirmediği adayın başlığı `Never ship {"password": "ExampleSecret123"} in config` olduğunda parola `dropped_by_judge.title` içinde aynen çıkıyor; doğrudan yeniden üretildi. Ayrıca `password="firstpart123 secondpart456"` yalnızca ilk parçayı gizleyip `secondpart456` bölümünü sızdırıyor. Alttaki Redactor açığı mevcut olsa da yeni dropped yüzeyi bu sırları yayımlıyor.