## Verdict (FIX-NEEDED)

Her iki D-085 incelemesi de flag-ON sevkiyatı reddediyor: gönderim kapısında ham sır sızıntıları, guard’da komut/alıntı/teknik literal değişiklikleri ve üretim aktivasyonunda açık HIGH’lar var. 159 hedefli test geçti; flag-off MCP yüzey hash’i doğrulandı. DB entegrasyonu, 755/555 ve performans ölçümleri bu export’ta yeniden çalıştırılamadı; dosya değiştirilmedi.

## Per-claim

1. **PARTIAL** — Sekiz review-67 vakası sıfır çağrıyla testli; Unicode ve tırnaklı anahtarlı atamalar kaçıyor: [query_rewrite.py:103](</private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/rev70/tree/src/hlmemo/librarian/query_rewrite.py:103>), [test_pivot_slice1.py:762](</private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/rev70/tree/tests/integration/test_pivot_slice1.py:762>).
2. **OPEN** — Guard kabul edilen rewrite’larla komut, quoted string ve teknik literal değiştirebiliyor: [rewrite_guard.py:168](</private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/rev70/tree/src/hlmemo/core/rewrite_guard.py:168>), [rewrite_guard.py:244](</private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/rev70/tree/src/hlmemo/core/rewrite_guard.py:244>).
3. **OK** — Latency modunda ilk schema hatası sonraki profile geçiyor; background retry korunuyor: [provider.py:500](</private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/rev70/tree/src/hlmemo/librarian/provider.py:500>), [provider.py:577](</private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/rev70/tree/src/hlmemo/librarian/provider.py:577>).
4. **OK** — Genericity kontrolü AST/function kapsamlı: [test_wf_task_fallbacks.py:386](</private/tmp/claude-501/-Users-cemalkurt-Projects-HLMemo/07becd3e-3d09-45cc-b57f-ee0d0b6716e4/scratchpad/rev70/tree/tests/unit/test_wf_task_fallbacks.py:386>).

## New findings

| severity | file:line | trigger | fix |
|---|---|---|---|
| HIGH | `query_rewrite.py:103-132` | `ÖZEL=hunter2` ve `"pin": "1234"` provider’a çıkabilir. | Unicode/quoted assignment anahtarlarını engelle; sıfır-call testleri ekle. **D-102 scope.** |
| HIGH | `rewrite_guard.py:168-184` | `git add .`→`git add`; `>`→`<`, `&&`→`||`, `--` düşürme kabul ediliyor. | Komut içindeki noktalama/operatör tokenlarını ham biçimde koru. **D-102/D-097 scope.** |
| HIGH | `rewrite_guard.py:187-269` | `"prod db"`→`"prod new db"` ve `` `git status` ``→`` `git force status` `` kabul ediliyor. | Quote/backtick/command spanlarını bitişik ve bütünüyle verbatim eşleştir. **D-102 scope.** |
| HIGH | `rewrite_guard.py:47-65,140-157,192-223` | `false`→`true`, `main`→`master`, `go test`→`run build`; 8 argüman sonrası mutasyon kabul. | Teknik ortak sözcükleri koru, CLI kapsamını genişlet, argüman tavanını kaldır. **D-102 scope.** |
| HIGH | `config.py:247`; `deploy/llm.env.example:38-40` | R2 `llm.env` ile R3’te rewrite varsayılan olarak OFF kalıyor. | Template/installer’a `HLM_QUERY_REWRITE=true` ve post-deploy durum kapısı ekle. **D-099 scope.** |
| HIGH | `query_rewrite.py:103-109`; `redact.py:25-58` | `Mein Passwort blume…`, `p a r o l a hunter2`, `passw0rd hunter2` çağrı üretebilir. | DE stems ve yüksek kesinlikli spacing/leetspeak kuralları ekle. **Policy extension.** |

Geçerli IBAN/JWT/SSH/AWS/GitHub/URL/DSN örnekleri engelleniyor. PIN, Luhn kart, email/telefon için yüksek kesinlikli blok öneriyorum; çıplak kısa `hunter2` ya belgelenmiş residual olmalı ya da kısa mixed-alnum tamamen engellenmeli.

Lisans blocker’ı bulmadım: uyarlanan listeler CC-BY-SA kalmalı; attribution, değişiklik açıklaması ve lisans bağlantısı her dağıtımda korunmalı. Mevcut README/header yaklaşımı [upstream lisansı](https://github.com/hermitdave/FrequencyWords) ve [CC BY-SA §3](https://creativecommons.org/licenses/by-sa/4.0/legalcode) ile uyumlu görünüyor.

## Ship-with-flag-ON (R3)

**No.** Açık HIGH guard/privacy kusurları ve eksik üretim aktivasyonu D-099’un “no open HIGH” şartını ihlal ediyor.