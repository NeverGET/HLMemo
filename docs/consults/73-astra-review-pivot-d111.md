## Verdict (FIX-NEEDED)

İki bağımsız inceleme, korunan alıntı içeriğinin provider payload’ına sızdığını doğruladı; D-099 kriter 3 sağlanmıyor. 102 hedefli test geçti; 2.000 zor sorgu/8.080 must-hide öğesi doğrulandı. Tam unit/deploy/integration toplamları, G3 ve canlı kabul oranları yeniden doğrulanmadı. Dosyalar değiştirilmedi.

## Per-claim

1. **PARTIAL** — NFC, görünmez karakter kapısı ve normalizasyon mevcut; fullwidth/math apostrof ekleri açık kalabiliyor (`src/hlmemo/core/rewrite_guard.py:385`).
2. **PARTIAL** — Keyfî apostrof kuyruğu ve `error'qzxv` reddediliyor; bitişik tek tırnaklı alıntı sızıyor (`src/hlmemo/core/rewrite_guard.py:430`).
3. **PARTIAL** — Adjacency kontrolü var; çekimli tekrarlar ve `sep→poss` değişimi kabul ediliyor (`src/hlmemo/core/rewrite_guard.py:652,667`). Özgün sorgu da arandığından retrieval etkisi MEDIUM.
4. **OK** — Muafiyet beklemenin nedenselliğine bağlı; `queue_wait_s=1e-9`, 0,6 saniyelik denemeyi muaf tutmadı (`src/hlmemo/librarian/provider.py:123`).
5. **OK** — Eksik env FAIL; R3 modu rewrite ON/cap OFF gerektiriyor. Etiketli R2-env PASS, yalnız D-108 ara adımı olarak güvenli; nihai R3 kabulü değil (`deploy/scripts/check_librarian.py:232,293,318,340`).
6. **PARTIAL** — Normal akışta R2 snapshot alınır, rollback öncesi render/restore edilir; hata kurtarması R3 env’i getirir (`deploy/scripts/remote-deploy.sh:313`; `deploy/scripts/rollback.sh:135,178,199,234`). İstenen testler mevcut; eski runner’ın snapshotsız yolu yalnız uyarır ve env uyumsuzluğunu önlemez (`deploy/scripts/rollback.sh:126`).

## New findings

| severity | file:line | trigger | fix |
|---|---|---|---|
| HIGH · in-scope | `src/hlmemo/core/rewrite_guard.py:430` | `XX'başlangıç üretim bağlantı son' neden oluyor ve nasıl düzeltilir?`: gate=`None`, redaction=0; payload `⟦P1⟧ üretim bağlantı ⟦P2⟧…` içeriyor. | Yalnız doğrulanmış dil ekini apostrof say; diğer bitişik alıntıları span boyunca maskele. |
| MEDIUM · in-scope | `src/hlmemo/core/rewrite_guard.py:667` | `worker neden hata veriyor?` için `why is ⟦P1⟧ worker’s failure?` kabul edilerek `worker worker’s` üretiyor. | Kabul edilen clitic/çekim biçimlerinin tabanını da protected-repeat kontrolüne kat. |
| MEDIUM · in-scope | `src/hlmemo/core/rewrite_guard.py:652` | `config.py neden çalışmıyor?` için `why is ⟦P1⟧’s failure?` kabul ediliyor; açıklanan whitespace istisnası sahiplik eklemeye genişliyor. | Yalnız start/end/whitespace eşdeğerliğini serbest bırak; possessive sınırını koru. |
| LOW · in-scope | `src/hlmemo/core/rewrite_guard.py:385` | `Qzxv’ｄｅ neden hata oluyor?` → `⟦P1⟧’ｄｅ…`; math-letter `𝐝𝐞` de açık kalıyor. | Eki görünür bırakmadan önce ham karakterlerde Latin/script kontrolü uygula. |

## Ship-with-flag-ON (R3)

**No.** Bitişik tek tırnaklı alıntı sızıntısı invariant A’yı doğrudan ihlal ediyor; restore kusurları da açık.