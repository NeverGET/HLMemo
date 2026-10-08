**NO-GO**

**HIGH — `src/hlmemo/core/retrieval.py:305–306,321–324`:** Bilinen bir anahtar bulunması YAML geçerliliğini kanıtlamıyor; Markdown görev listesi hâlâ gövdeden silinebiliyor.

```python
s = "--- \ntitle: body description\n- [ ] rotate database\n--- \nAfter"
assert parse_frontmatter(s) == ({}, s)  # geçer
assert preview_text(s, 0) == s          # başarısız: "After"
```

Gerçek importer bu girdiyi tek kayıtta aynen saklıyor; YAML parser’ı bloğu reddediyor. Boşluksuz `title: [unterminated` örneği de hâlâ siliniyor.

Ek MEDIUM doğrulamadım. Exporter’ın standart frontmatter çıktılarında kaçırma, 1–64 bin karakterlik ölçümlerde süper-lineer büyüme bulmadım. Mevcut 15 preview/linear kontrolü geçti; karşı örnek gerçek fonksiyondan çıkarılan kodla doğrulandı.