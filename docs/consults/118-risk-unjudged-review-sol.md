NO-GO

HIGH

- [src/hlmemo/core/retrieval.py:302](</private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/rev118/src/hlmemo/core/retrieval.py:302>) — `preview_text` sıradan Markdown görev listesini frontmatter sanıp siliyor.
- Repro: `s="---\n- [ ] rotate database\n- [x] verify backup\n---\nBody continues"`; `assert preview_text(s, 0) == s`.
- Gerçekte yalnızca `"Body continues"` dönüyor. PyYAML listeyi geçersiz YAML olarak reddediyor; unsplit import yolu bunu gövde olarak saklıyor ([build.py:165](</private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/rev118/src/hlmemo/importers/build.py:165>)). Bu, rubricteki body-text-loss HIGH.

MEDIUM

- [retrieval.py:302](</private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/rev118/src/hlmemo/core/retrieval.py:302>) — Geçerli frontmatter içindeki üst-seviye yorum (`# generated`) stripping’i engelliyor; “leading YAML block” vaadiyle çelişiyor ([BACKLOG.md:96](</private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/rev118/docs/status/BACKLOG.md:96>)).

LOW

- Yok.

Yanıtlar

- (a) Hayır; `unjudged` aynı görünür/current/active aday kümesinden geliyor ve judge çağrısı sonrası aynı D-062 re-check’ten geçiyor.
- (b) Hayır; 3.000 diferansiyel bütçe vakasında verdict/warnings/omitted/judged ve budget-error davranışı değişmedi; `budget.used ≤ limit`.
- (c) Yeni sızıntı yolu yok; `unjudged.title` `_safe_title` kullanıyor. Ancak önceden kabul edilen JSON-style/quoted multi-word Redactor boşlukları hâlâ unredacted geçebilir.
- (d) Evet, yukarıdaki HIGH ile body text düşebilir; super-linear backtracking bulmadım, regex büyümesi lineer.

Doğrulama: risk packing unit testleri 26/26 geçti; preview reproduksiyonu doğrudan çalıştırıldı. DB integration suite, adanmış test DSN olmadığı için çalıştırılmadı.