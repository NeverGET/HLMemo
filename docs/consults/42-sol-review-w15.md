## Verdict

**DO-NOT-MERGE.** G-I4 kabul eşiği sağlanmıyor; ayrıca yeniden içe aktarma ve export kimliğiyle ilgili veri bütünlüğü kusurları var. Bu yalnızca statik incelemedir; test çalıştırmadım.

## Findings

| Severity | File:line | Issue | Fix |
|---|---|---|---|
| High | [0007_import.py:74](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-ad7c9692cf72a8abd/alembic/versions/0007_import.py:74) | `EXCLUDE` için GiST oluşturulurken canlı tablo kilitlenir; `lock_timeout` kilidin tutulma süresini sınırlamaz. | Bakım penceresi veya çevrimiçi geçiş tasarımı. |
| Medium | [0007_import.py:67](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-ad7c9692cf72a8abd/alembic/versions/0007_import.py:67) | Eksik kaynak alanları `CHECK` içinde `UNKNOWN` üretip geçebilir; eksik yol `source_key=NULL` ile sahiplik kısıtından kaçar. | Zorunlu JSON anahtarlarını açıkça doğrula. |
| Medium | [common.py:389](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-ad7c9692cf72a8abd/src/hlmemo/importers/common.py:389) | Başlıktaki herhangi bir tarih geçerlilik kanıtı sayılıyor; örneğin tarih tablosu başlığı yanlış `valid_from` verebilir. | Tarihli kayıt biçimlerini daralt. |
| High | [plan.py:59](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-ad7c9692cf72a8abd/src/hlmemo/importers/plan.py:59) | A→B→A→B→A içerik döngüsünde temel ve sabit `#retry` istek kimlikleri tekrar kullanılır; son revizyon başarısız olur. | Revizyon kimliğine beklenen sürümü kat. |
| High | [plan.py:114](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-ad7c9692cf72a8abd/src/hlmemo/importers/plan.py:114) | Sahte export `logical_id`, hedef projedeki ilgisiz aynı tür öğeyi revize edebilir; `source:null` export’un tekrar aktarımı da yeni öğe açar. | Hedef projeye özgü kalıcı eşleme ve sahiplik kontrolü ekle. |
| Medium | [common.py:454](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-ad7c9692cf72a8abd/src/hlmemo/importers/common.py:454) | Başlık düzenlenince bölüm anahtarı değişir; eski bölüm açık kalır. | Kalıcı bölüm kimliği veya kontrollü yeniden eşleme kullan. |

`mtime`/commit tarihi üretim yolunda yalnızca provenance; `recorded_at` sunucu zamanı. Projeler arası UUID çakışması ve `logical_id` ile yetki aşımı engelleniyor. Replay yazıları yeniden kuruyor; fakat importlar `write` olayı olduğundan [istenen librarian önceliği 6](</Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-ad7c9692cf72a8abd/docs/decisions/PHASE2-4-ROADMAP.md:218>) için ayırt edici işaret gerekiyor. Backfill tüm eksik projeleri belleğe alıp tek tek yazıyor; downgrade veri taşıyan satırlarda haklı olarak reddediyor.

## G-I4

Sonuç **27/34 = 0.794**, eşik **0.90**; kapı başarısız.  
Doğru öğe **29/34** kez bulunmuş, fakat kanıt parçası yalnızca **27/34** kez dönmüş: iki kayıp parça seçimiyle ilgili. Bu sonuç kusuru yalnızca sıralamaya bağlamıyor.