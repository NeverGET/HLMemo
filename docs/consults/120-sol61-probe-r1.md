**NO-GO**

- **HIGH — [src/hlmemo/core/retrieval.py:303](/private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/rev118/src/hlmemo/core/retrieval.py:303):** Regex, yatay çizgiler arasındaki tab girintili Markdown kodunu frontmatter sayıp siliyor. Bu, geçerli YAML metadata değildir. Reproduksiyon:
  ```python
  s = '---\n\tprint("keep")\n---\nTail'
  assert preview_text(s, 0) == s  # FAIL: sonuç "Tail"
  ```
  Yalnız doğrulanmış metadata blokları kaldırılmalı.

- **MEDIUM — [src/hlmemo/cli/preflight.py:214](/private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/rev118/src/hlmemo/cli/preflight.py:214):** Dört aday TAU üstündeyken ilk üçü warning, dördüncüsü unjudged olur. Başarısız judge çağrısı sırasında ilk üç kapanırsa yalnız dördüncü kalır; preflight yanlış biçimde “no past lesson passed the retrieval warn threshold” der. Gerçek fonksiyonlarla doğrulandı.
- **LOW:** Ek bulgu yok.

(a) **Görünürlük açısından hayır.** Aynı aday filtresi ve judge sonrası D-062 kontrolü uygulanıyor; privacy-withheld okunabilir adaylar kabul edilen kapsamda.

(b) **Hayır; değişiklik saptanmadı.** Verdict/warnings/omitted/judged hesapları korunuyor; ek alanlar warnings paketlendikten sonra ekleniyor. Bütçe hataları eşleşti, `budget.used` tam ve limit içinde kaldı.

(c) **Evet, kabul edilmiş mevcut boşluklarla:** JSON biçimli ve tırnaklı çok kelimeli secret değerleri unredacted kalabiliyor; iki biçim de doğrulandı. Yeni redaction bypass’ı saptanmadı.

(d) **Gövde metni düşebilir: HIGH yukarıda.** Süperlineer backtracking saptanmadı; ölçümler yaklaşık doğrusal.

Kontroller: 39 unit test, 1.344 paketleme karşılaştırması ve bulgu reproduksiyonları. DB entegrasyon testleri çalıştırılmadı; dosyalar değiştirilmedi.