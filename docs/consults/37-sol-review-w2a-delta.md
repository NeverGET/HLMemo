## Verdict

**DO-NOT-MERGE.** D-063’ün en fazla 5 saniyelik DF eskime sınırı uygulanmıyor; gizlilik ve cassette yollarında da veri sızdırabilen açıklar kaldı. Bu karar yalnızca statik incelemeye dayanıyor; test veya Docker çalıştırmadım.

## Per-finding status

- **#1 PARTIAL** — [pair_check.py:85](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/librarian/tasks/pair_check.py:85) ilk çağrıyı denetliyor; [provider.py:417](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/librarian/provider.py:417) retry ve fallback sırasında tekrar denetlemiyor. `hlm-librarian` kuralları da [memory.py:38](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/librarian/memory.py:38) üzerinden bu kapıdan geçmeden prompt’a giriyor.
- **#2 PARTIAL** — Metin içerik ve normal iş hataları arındırılmış; [provider.py:600](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/librarian/provider.py:600) metin dışı yanıt içeriğini arındırmadan cassette’e bırakıyor.
- **#3 PARTIAL** — [test_gl3_llm_down.py:296](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/tests/integration/test_gl3_llm_down.py:296) gerçek API’de yazı ve sorguyu eşzamanlı yürütüyor; ölçüm sırasında librarian’ın gerçekten stall/503 gördüğünü doğrulamıyor. DF sınırı ve soğuk yüklemenin toplam 2 saniye sınırı sağlanmıyor.
- **#5 FIXED** — [actor.py:202](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/librarian/actor.py:202) değerlendirilen sürümleri yazma yolunun mantıksal öğe kilitleri altında karşılaştırıyor.
- **#6 PARTIAL** — [worker.py:93](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/librarian/worker.py:93) CC-5 alanlarını, migration soru tablosunu ekliyor; tablo şekli dondurulmuş [§4b sözleşmesinden](/Users/cemalkurt/Projects/HLMemo/docs/decisions/PHASE2-4-ROADMAP.md:242) farklı ve bunu onaylayan D-062 kararı yok.
- **#7 PARTIAL** — 5xx/transport gideri muhafazakâr hesaplanıyor; [ledger.py:113](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/librarian/ledger.py:113) lineage sayımı atomik olmadığından eşzamanlı çağrılar 20 sınırını aşabilir.
- **#8 PARTIAL** — [actor.py:187](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/librarian/actor.py:187) bitiş zamanı ve deneme sayısını yeniden kuruyor; tam iş projeksiyonu hâlâ her durumda eşleşmiyor.

## New findings

| Önem | Bulgu |
|---|---|
| Yüksek | Retry aralığında cihaz iptal edilirse aynı içerik yeniden gönderilebilir; çalışma belleği kuralları da gizlilik denetimini atlıyor. |
| Yüksek | HTTP 200 içindeki dizi biçimli `message.content`, [cassette.py:58](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/librarian/cassette.py:58) ile doğrulamadan önce saklanabilir. |
| Yüksek | [term_stats.py:202](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/core/term_stats.py:202) 5 saniye geçse de ilk sorguya eski DF’yi döndürür; başarısız yenilemelerde eskime sınırsızdır. Single flight çalışan görev için mevcut; [soğuk yükleme](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/db/read_queries.py:349) dört ayrı 2 saniyelik SQL sınırı kullanıyor. |
| Yüksek | [0006_librarian.py:188](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/alembic/versions/0006_librarian.py:188) tablo kilitlerini üç GIN pending-list temizliği bitene dek tutabilir; canlı yazılar uzun süre bloklanabilir. |
| Orta | Retry sonrası `run_after` replay’e kaydedilmiyor ([lease.py:152](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/worker/lease.py:152)); soru satırındaki sayısal `job_id` de sıra boşluklarında değişebilir. İncelenen tamamlanma yolunda lease fencing korunuyor ([worker.py:383](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/librarian/worker.py:383)). |