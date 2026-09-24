## Verdict (FIX-NEEDED)

Astra low ve Sol xhigh incelemeleri aynı sonuca ulaştı: 3. iddia tam kapanmamış; uzun metinlerde yanlış demotion sürüyor. Merge’de semantik kayıp, yeni deadlock, scope leak, D-074 observer mutasyonu veya authoritative replay sapması bulunmadı. İzole 3.000-vaka property testi ve bağımsız 20.000 grafik kontrolü geçti; iddia 7’deki tam test/performance gate sonuçları yeniden doğrulanmadı. Dosya değiştirilmedi.

## PROMOTION-READY (yes)

D-077’nin stranding düzeltmesi ve Sol yeniden inceleme önkoşulu sağlanıyor; D-076/D-087’nin observer-only kararı ayrıca yürürlükte.

## Per-claim

1. **FIXED** — `src/hlmemo/librarian/roles.py:179`, `src/hlmemo/librarian/tasks/apply_batch.py:48`: iki proposal biçimi de işleniyor; legacy widen otomatik release dışında. Kalan `proposal->'actions'` SQL filtresi bulunmadı.
2. **FIXED** — `src/hlmemo/core/supersession.py:213`: cyclic SCC içi kenarlar kaldırılıyor; dar bütçe regresyonu geçti.
3. **PARTIAL** — `src/hlmemo/core/supersession.py:113`: alıntı dışındaki terimler 24 benzersiz terimle sınırlandığından geç eşleşmeler kaçıyor.
4. **FIXED** — `src/hlmemo/core/supersession.py:216`: eski sıra feasible tutuluyor; deadline sıralaması rank sınırını koruyor. Karşı örnek bulunmadı.
5. **FIXED** — `tests/integration/_librarian_fixtures.py:280`: consumed backoff ham karşılaştırılıyor. Backoff→hand-back için belgelenen yanlış-pozitif residual devam ediyor.
6. **OK** — `src/hlmemo/librarian/worker.py:858`, `:868`, `:1105`: item locks → policy → staleness/rebase → event-id sırası korunmuş. `src/hlmemo/db/librarian_queries.py:293` FOR SHARE; `src/hlmemo/librarian/questions.py:214` doğrudan apply kontrolü korunmuş. Provider, D-086, N=3 ve auto-merge dosyalarında uyumsuzluk bulunmadı.

## New findings

| severity | file:line | trigger | fix |
|---|---|---|---|
| MEDIUM | `src/hlmemo/core/supersession.py:113` | Alıntı `API uses port 8080`; ardından `and`, 25 farklı filler terimi ve `backups use port 9090.`; sorgu `port`. Dış eşleşme kesildiğinden predicate yanlışlıkla `True`, sıra `[1,2]→[2,1]` oluyor. Çalıştırılarak doğrulandı. | Alıntı dışındaki metni terim sınırı olmadan tara; 24. terimden sonraki tekrar için regresyon ekle. |