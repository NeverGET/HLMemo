**NO-GO**

- **HIGH — `src/hlmemo/core/retrieval.py:305–306,320–324`:** İzin verilen bir anahtar, Markdown gövdesini hâlâ frontmatter saydırıyor. Aşağıdaki başlık ve görev listesi geçerli YAML değil; tamamı siliniyor.
  ```python
  s = "---\n# Deployment checklist\ntitle: release\n- [ ] rotate database\n---\nAfter"
  assert preview_text(s, 0) == s  # Gerçek sonuç: "After"
  ```
  Ayrıca round-1 karşı örneği `---\ntitle: [unterminated\n---\nAfter` hâlâ `"After"` döndürüyor. Anahtar kontrolü YAML geçerliliğini veya gerçek frontmatter olduğunu kanıtlamıyor.

Gerçek fonksiyondan çalıştırılarak ve bağımsız incelemeyle doğrulandı. Süper-lineer backtracking bulunmadı; uzun girinti/anahtar girdilerinde 2k–128k ölçümleri yaklaşık doğrusal. Ek MEDIUM doğrulanmadı.