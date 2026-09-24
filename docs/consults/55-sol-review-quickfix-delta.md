## Verdict

**FIX-NEEDED.** Yalnızca `4169ea6..547d7d7` farkı statik olarak incelendi; test çalıştırılmadı.

## Per-finding

1. **FIXED** — [risk_queries.py](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/db/risk_queries.py:33): sorgu, dışlanan projeye dokunan çok projeli öğeleri de eliyor.
2. **PARTIAL** — [actor.py](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/librarian/actor.py:127): politika değişikliği `FOR SHARE` ile sıralanıyor; `memory.answer` sırasında öğenin proje kapsamı değişebilir.
3. **PARTIAL** — [plan.py](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/importers/plan.py:194): eski tek parça öğe kapatılıyor; reddedilen bölümler de yerine geçen bölüm sayılıyor.
4. **PARTIAL** — [lessons.py](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/importers/lessons.py:189): belirgin prosedürler korunuyor; işaretsiz, sıralı iki adım hâlâ ayrı derslere bölünebilir.
5. **PARTIAL** — [replay.py](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/db/replay.py:74): Unicode konumu düzeltildi; eski politika olayları replay’de kaybolur.

## New issues

- [questions.py](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/librarian/questions.py:204): `policy_blocked` ve `_widen` arasında mantıksal öğe kilidi yok. Bir revizyon `project_ids` içine dışlanan T’yi eklerse `_widen` yeni başı okuyup T ile `hlm-global` kapsamını birleştirebilir.
- [runner.py](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/importers/runner.py:295): bölüm yazımı başarısız olsa bile eski tek parça öğeyi kapatma deneniyor; içerik kaybı doğabilir.