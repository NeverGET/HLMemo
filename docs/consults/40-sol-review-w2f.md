## Verdict

**DO-NOT-MERGE.** Adjudication iki public vakada model hatasına kredi veriyor; varsayılan `hlm bench` harcama sınırı da eşzamanlı koşular arasında ortak değil. İnceleme salt statikti: test çalıştırmadım, dosya değiştirmedim, `docs/private/**` okumadım.

## Findings

| Önem | Dosya:satır | Sorun | Düzeltme |
|---|---|---|---|
| Yüksek | [ADJUDICATION.md:38](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-afe2f6c25e6597b28/bench/v2/ADJUDICATION.md:38) | T10-07 düşürülmüş. Oysa prompt, istenen tarih yoksa `answer=null` diyor; vaka tarihi açıkça belirsiz bırakıyor. Bu, D-067’deki hatayı ve leaderboard’daki false-answer oranını siliyor. | Vakayı özgün gold ile tutup sonuçları yeniden hesaplayın. |
| Yüksek | [adjudication_v2.json:13](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-afe2f6c25e6597b28/src/hlmemo/bench/adjudication_v2.json:13) | T9-03 için kabul edilen L20, belirtilen `pytest` komutunda ONNX yüklenmesini gerektiriyor; L09 için de paylaşılan DB kullanımı belirtilmemiş. “Açıkça uygulanır” kuralı gevşetilmiş. | `{L03}` dışındaki kabulleri kaldırın; T9-01’deki koşullu L16’yı da yeniden değerlendirin. |
| Yüksek | [engine.py:257](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-afe2f6c25e6597b28/src/hlmemo/bench/engine.py:257) | Aynı production `Provider` kullanılıyor, fakat varsayılan `MemoryBudget` her koşuda yeniden oluşturuluyor; `DbBudget` yalnızca isteğe bağlı. İki eşzamanlı bench ortak saat/gün/ay sınırını aşabilir. | Production DB rezervasyonunu varsayılan yola bağlayın veya ortak sınır olmadığı açıkça belirtilen ayrı bir mod sunun. |
| Orta | [engine.py:298](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-afe2f6c25e6597b28/src/hlmemo/bench/engine.py:298) | `Redactor()` production’daki `Redactor.from_settings()` yerine kullanılıyor; etkinleştirilmiş e-posta/telefon maskelemesi bench çağrılarına ve cassette kayıtlarına taşınmıyor. | Redactor’ı ayarlardan kurun. |
| Yüksek | [cli.py:231](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-afe2f6c25e6597b28/src/hlmemo/bench/cli.py:231) | `--baseline` retrieval geçmişini değiştiriyor; sonraki W-E iterasyonlarını ekleyen ve korpuslar arası 1 puan kuralını uygulayan akış yok. | Aday ekleme, geçmiş koruma ve karar kaydı kontrolünü bağlayın; config hash ve prompt sürümlerini saklayın. |
| Yüksek | [cli.py:235](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-afe2f6c25e6597b28/src/hlmemo/bench/cli.py:235) | Kısmi/yarıda kesilmiş koşu “best” olabilir; adjusted gold eksik private pack vakalarını sessizce atar. | Kapsamı ve pack hash’lerini doğrulayın; eksik koşuları sıralamayın. |

## Adjudication spot-check

- **T10-07:** Düşürmeye katılmıyorum; tarih yok ve `answer=null` sözleşmesi açık.
- **T9-03:** L20/L09 kredisine katılmıyorum; görev yalnızca L03’ü açıkça destekliyor.
- **T9-01:** L16 şüpheli; görev deploy süresini belirtmiyor.

İncelenen tracked leaderboard ve public cassette dosyalarında private vaka metni saptamadım; private hükümlere yalnızca yayımlanan gerekçeleri üzerinden baktım.