## Verdict

**DO-NOT-MERGE.** Bu yalnızca statik incelemedir; test veya Docker çalıştırmadım, dosya değiştirmedim.

## Per-item status

- **Gizlilik: PARTIAL.** Retry ve fallback öncesi kontrol var; fakat uygulama aşamasındaki kilitli denetimle aynı anlık durum garantisini vermiyor. Kuralların yazarı ve biçimi kontrol ediliyor, ancak kısa bir kuralın başka projeden içerik taşıması engellenmiyor.
- **Cassette: PARTIAL.** Provider yolu yanıt içeriğini normalize edip arındırıyor; [kalıcı yazma noktası](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/librarian/cassette.py:161) arındırmayı zorunlu kılmıyor.
- **DF: PARTIAL; G-L3: PARTIAL.** Dört sorgu ortak 2 saniyelik bütçeyi kullanıyor. Ancak 30 saniye yazma anından değil, ilk geçersizlik gözleminden başlıyor. G-L3, stall **veya** 503 ile örtüşmeyi doğruluyor; ikisini ayrı ayrı doğrulamıyor.
- **Migration: PARTIAL.** Ayrı autocommit adımı var; hatadan sonra yeniden çalıştırma güvenilir değil.
- **Lineage: PARTIAL.** SQL sayacı atomik; HTTP’ye ulaşmayan denemeleri de tüketiyor.
- **Replay: PARTIAL.** Tamamlanan işin `run_after` değeri geri yükleniyor; henüz tamamlanmamış ertelenmiş işinki event’te yok.
- **Sorular: PARTIAL.** `job_key` referansı **FIXED**; kolonlar dondurulmuş soru şemasından hâlâ farklı.

## Remaining findings

| Bulgu | Karşı örnek |
|---|---|
| Gizlilik yarışı | [Precheck](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/librarian/privacy.py:129) kilitsiz transaction’ı kapatır; ardından grant iptal edilip HTTP isteği gönderilebilir. [Apply denetimi](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/librarian/actor.py:66) cihaz erişimini kilitler. |
| Migration yarım durumu | Upgrade indeksleri önce kapatır; bu yolda DDL `fastupdate=on` kalmaz. Fakat [DDL sonrası flush](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/alembic/versions/0006_librarian.py:220) başarısız olursa DDL commit edilmişken revision eski kalır; koşulsuz DDL tekrar çalışmaz. [Başarısız downgrade](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/alembic/versions/0006_librarian.py:236) ise 0006 tablolarını `fastupdate=on` bırakabilir; upgrade yeniden çalışmaz. Flush’ta 3 saniyelik timeout yok. |
| Çağrısız sayaç tüketimi | [Claim](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/librarian/provider.py:493) bütçe rezervasyonundan önce: 20 bütçe reddi, sıfır provider çağrısıyla işi aç bırakabilir. HTTP 503/timeout sayılması makuldür. |
| DF sınırı | [invalid_since](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/src/hlmemo/core/term_stats.py:215) ilk sorguda kurulur. Yazıdan 31 saniye sonraki ilk sorgu eski DF’yi alabilir. |
| Projeksiyon sözleşmesi | [Mevcut kolonlar](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a21f9ce923d827a45/alembic/versions/0006_librarian.py:65), [yol haritasındaki şemayla](/Users/cemalkurt/Projects/HLMemo/docs/decisions/PHASE2-4-ROADMAP.md:242) uyuşmuyor; ertelenmiş işin replay durumu da eksik. |

## Blocking for observer-only production?

**Evet.** OBSERVER bağlantı mutasyonlarını önlese de prompt sızıntısı yarışını, migration’ın kurtarılamayan yarım durumunu ve provider çağrısı olmadan lineage sınırının tükenmesini önlemez.