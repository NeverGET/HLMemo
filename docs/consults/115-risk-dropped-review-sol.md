NO-GO

HIGH

- [risk_service.py:252](</Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-abf856ecda504d95c/src/hlmemo/core/risk_service.py:252>): `_safe_title`, `Redactor`’dan daha dar olan `strong_secret_rule` kullanıyor; DSN parolası/JWT/Bearer gibi metinler açık dönebiliyor. Repro testi: above-TAU aday başlığı `postgresql://alice:VerySecret123@db/prod`; `_dropped(...)["title"]` içinde `VerySecret123` bulunmamalı—şu an bulunuyor.

MEDIUM

- [risk_service.py:288](</Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-abf856ecda504d95c/src/hlmemo/core/risk_service.py:288>): boş dropped metadata warning’lerden önce bütçeye ayrılıyor. Doğrulandı: 256 token’da `dropped=None` bir warning’i 246 token ile taşırken `dropped=[]` aynı warning’i düşürüyor. Yeni alan warning’i bütçeden itiyor.
- [test_risk_dropped_by_judge.py:133](</Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-abf856ecda504d95c/tests/integration/test_risk_dropped_by_judge.py:133>): listelenen tehditlerin test kapsamı eksik; device-scoped, policy-withheld ve closed/superseded öğelerin yeni alana girmediği doğrudan sabitlenmemiş.

LOW

- [preflight.py:171](</Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-abf856ecda504d95c/src/hlmemo/cli/preflight.py:171>): nonempty `dropped_by_judge` + `no_matching_evidence`, güvenilir özette hâlâ “found no matching past lesson” üretiyor.
- [risk_service.py:258](</Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-abf856ecda504d95c/src/hlmemo/core/risk_service.py:258>): önceden yazılan kontrattaki “judge’s reason” yerine deterministik boilerplate `why` dönüyor; güvenli fakat kontrat sapması.

(a) Hayır; mevcut yol initial visibility, `res.denied` dışlaması ve post-judge `_recheck` ile korunuyor.  
(b) `verdict`/`judged` değişmiyor; dar bütçede `warnings` değişebiliyor veya çağrı artık bütçe hatası verebiliyor.  
(c) `why` bellek/judge metni taşımıyor; `title` Redactor’ın gizleyeceği sırları taşıyabiliyor.  
(d) Dropped girdileri warning’lerden sonra ekleniyor ve sayaçlar capped top-3 kümesi için doğru; fakat boş metadata warning’i düşürebildiğinden genel warning-first kontratı yanlış.

Doğrulama: temiz HEAD `5c5a71f`, iki bağımsız inceleme aynı HIGH/MEDIUM’ları buldu; saf reproducer’lar çalıştırıldı. Tam pytest, güvenli `HLM_TEST_DSN` olmadığı için yeniden çalıştırılmadı.