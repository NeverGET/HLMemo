## Verdict (FIX-NEEDED)

D-077 stranding, cycle handling and D-087 rank bound are closed. Ancak apply yollarında expiry sonrası mutasyon yarışı, out-of-span denetiminde iki karşıörnek ve replay oracle over-mask’i var. Yeni deadlock, scope leak veya D-074 observer mutasyonu bulmadım. DB/performance gate sayıları bu bağımlılıksız exportta yeniden çalıştırılmadı.

## PROMOTION-READY (no)

D-077 stranding şartı sağlandı; fakat TTL yarışı ve D-087’nin observer-only kararı nedeniyle bütün ağaç promotion-ready değil.

## Per-claim

1. **OK** — Legacy `mutation` ortak adaptörden geçiyor; widen kind/action düzeyinde atlanıyor: `roles.py:162-195`, `apply_batch.py:48-52`, `worker.py:842-847`. Başka actions-only SQL filtresi yok.
2. **OK** — Cyclic SCC iç kenarları kaldırılıyor ve tight-budget vaka korunuyor: `supersession.py:211-240`, `test_librarian_judgement_v2.py:403-416`.
3. **PARTIAL** — Basit ikinci-clause vakası düzeldi; ancak tam-span tekrarı ve ilk 24 terim sonrası dış eşleşme hâlâ demote ediliyor: `supersession.py:104-116`, `normalize.py:39-55`.
4. **OK** — Deadline/Lawler yapısı sınırı sağlıyor: `supersession.py:216-240`; frozen 3.000-case test `test_librarian_judgement_v2.py:419-492`. Ek karşıörnek bulunmadı.
5. **PARTIAL** — Consumed back-off ham karşılaştırılıyor; fakat her queued/NULL iş de maskeleniyor: `_librarian_fixtures.py:280-284`, `_w2b_fixtures.py:194-197`, `_write_fixtures.py:129-133`.
6. **PARTIAL** — D-083 policy kontrolleri item kilitlerinden sonra/event-id’den önce; D-084 latency/breaker, D-086 events ve N=3 envelope korunmuş. Ancak auto-merged `questions` ve worker policy kilidi TTL kontrolünden sonra bekleyebiliyor: `worker.py:858-868,1105`, `questions.py:178-215,277`.

## New findings

| severity | file:line | trigger | fix |
|---|---|---|---|
| HIGH | `worker.py:859-868`; `questions.py:178-215` | Item veya policy `FOR SHARE` beklemesi sırasında soru expire olur; sonra link/close/widen uygulanır. | Her iki apply yolunda son policy kilidinden sonra fresh-clock TTL recheck ve kilit-bariyerli regresyon testi. |
| MEDIUM | `supersession.py:113` | Dış bölümde aynı exact span tekrarlanır veya query terimi ilk 24 dış-terimden sonra gelir; `matched_in_span=True`. | Tüm dış metni occurrence-aware ve limitsiz tara; belirsiz çoklu span’da demotion yapma. |
| MEDIUM | replay fixtures above | Pristine queued/NULL işin event-authoritative `run_after` sapması maskelenir; backoff→hand-back geçerli replay’i de yanlış flagler. | Pairwise karşılaştırmada yalnız systemic↔NULL çiftini maskele; NULL↔NULL’ı ham karşılaştır. |