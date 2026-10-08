NO-GO

HIGH — `src/hlmemo/core/retrieval.py:304-306,320-324`: ayraç sonundaki boşluk kabul ediliyor; importer (`src/hlmemo/importers/common.py:48`) bunu frontmatter saymıyor.
```python
s = "--- \ntitle: prose\n---\nBody"  # açılış ayracında trailing space
assert parse_frontmatter(s) == ({}, s)
assert preview_text(s, 0) == s
```
İkinci assertion başarısız; çıktı `"Body"`. Generic importer metnin tamamını gövde olarak saklıyor. `--- ` kapanışı da aynı kaybı üretiyor.

MEDIUM — `src/hlmemo/core/retrieval.py:305-306`: 40 satır sınırı gerçek, importer’ın kabul ettiği frontmatter’ı kaçırıyor.
```python
s = "---\ntitle: t\n" + "# c\n" * 40 + "---\nBody"
assert preview_text(s, 0) == "Body"
```
Gerçek çıktı `s`; `parse_frontmatter` ise gövdeyi `"Body"` olarak ayırıyor. Bu, `preview_text` docstring’iyle çelişiyor.

Süper-lineer backtracking bulunmadı; 8K–1M en kötü-biçimli girdiler yaklaşık doğrusal ölçeklendi. Doğrudan aday kodla doğrulandı; pytest, salt-okunur temp alanı ve `HLM_TEST_DSN` bulunmadığı için çalıştırılamadı.