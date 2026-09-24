## Verdict

**DO-NOT-MERGE.** `exclude` politikası iki yönde izolasyon sağlamıyor ve politika değiştikten sonra eski öneriler uygulanabiliyor. İnceleme yalnızca statikti; test çalıştırmadım.

## Findings

| Önem | Bulgu |
|---|---|
| Bloker | `risk_check` proje listesini daraltıyor, fakat [risk_queries.py](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-ae29184860bd65a3c/src/hlmemo/db/risk_queries.py:32) `project_ids` ile kesişim arıyor. Hem test hem gerçek projeye kapsamlı bir ders, dışlanan sınırı iki yönde aşarak sonuca ve yargıca girebilir. |
| Bloker | Politika yalnızca aday planlanırken okunuyor. Önceden hazırlanmış bağlantı/kapatma önerileri [worker.py](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-ae29184860bd65a3c/src/hlmemo/librarian/worker.py:397) üzerinden; onaylanmış `widen_scope` ise [questions.py](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-ae29184860bd65a3c/src/hlmemo/librarian/questions.py:289) üzerinden, `exclude` sonrasında yeniden politika kontrolü olmadan uygulanabilir. |
| Orta | Eski tek parça `fact` yeni derslere bölününce kaynak anahtarı kayboluyor. Küçük kapsamlı yeniden içe aktarmada [toplu kapatma koruması](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-ae29184860bd65a3c/src/hlmemo/importers/plan.py:335) eski kaydı açık bırakabilir; yeni dersler yanında mükerrer bilgi oluşur. Tek parça kind revizyonunda kaynak sahipliği ve export eşlemesi makul görünüyor. |
| Orta | [Bölme sezgisi](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-ae29184860bd65a3c/src/hlmemo/importers/lessons.py:166) beş sözcüklü iki prosedür adımını bağımsız ders sanabilir. İlk sözcüklerden üretilen anahtar, sıradan ifade düzeltmesinde değişip remap/close döngüsü yaratabilir. |
| Düşük | Risk penceresi gizlilik kapısından geçmiş okunabilir metni kullanıyor. Ancak [Unicode `casefold` konumları](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-ae29184860bd65a3c/src/hlmemo/librarian/risk_judge.py:190) özgün gövde konumlarından sapıp yanlış pencere seçebilir. Politika ayarı yalnızca ops DB yolunda; [olay kaydı](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-ae29184860bd65a3c/src/hlmemo/ops/service.py:400) tek başına politikayı yeniden kurmuyor. |

## Decision-log text

Auto-memory types: feedback→lesson; user/project/reference→fact.  
Split lessons at ≥2 rule headings or ≥2 bullets of ≥5 words; copy shared context and derive anchors from headings or lead words.  
A kind-only change creates a revision under the existing source owner.  
For lessons over 1,200 characters, send the title and best matching readable window to the risk judge.  
`librarian_cross_project=exclude` is two-way; ops status names the breaker consistently; `hlm.export` skips unlisted-tool validation; the realdata client keeps its connection alive.