## Verdict

**GO-with-fixes — Astra low ve Sol xhigh aynı sonuca vardı.** İlk turun yedi bulgusu kod düzeyinde **CLOSED**; yeni HIGH saptanmadı. Aşağıdaki artık riskler son tur gereği sahibin kabul/ret kararına kalıyor.

**Round-1 kapanışları** — yollar `src/hlmemo/` altındadır:

| Bulgu | Durum | Kanıt |
|---|---|---|
| Astra #1: pinned quote sızıntısı/yanlış sürüm | **CLOSED** | `db/read_queries.py:683`, `:791`; pinned authorization ve sürüm bazlı eşleşme |
| Astra #2: 16 adımlık revert sınırı | **CLOSED** | `librarian/actor.py:849`, `:886`; visited-set traversal |
| Sol #1: raw update payload sızıntısı | **CLOSED** | `core/read_service.py:637`; yetkisiz hedefin tüm update girdisi kaldırılıyor |
| Sol #2: ortak span guard’larının atlanması | **CLOSED** | `core/write_updates.py:182`, `:340`; dispatch öncesi unique/boundary, revise için ek guard’lar |
| Sol #3: dar carrier’ın geniş hedefi kapatması | **CLOSED** | `core/write_updates.py:189`; mutating supersede için visibility kontrolü |
| Sol #4: ayrık tarih aralıkları | **CLOSED** | `db/read_queries.py:735`; unpinned link/version overlap |
| Sol #5: boş lesson brief | **CLOSED** | `brief/fetch.py:176`, `:247`; doğrulanan chunk reconstruction ve self-link istisnası |

1. **MEDIUM — İlgisiz revision, geçerli pinned part düzeltmesini düşürüyor.**  
   **Konum:** `src/hlmemo/db/read_queries.py:697`, `src/hlmemo/db/librarian_queries.py:357`, `:407`.  
   **Senaryo:** V1=`TTL 60. Owner Alice.`; `TTL 60` için pinned part düzeltmesi var. Yalnız owner değişip V2=`TTL 60. Owner Bob.` olunca bütün gövde eşitliği bozuluyor. Eski TTL değişmediği halde query demotion ve `memory.ask` supersession etiketi/pull-in kaybolabiliyor.  
   **Minimal fix:** pinned sürüm yetkisini koruyarak, değişmeden kalan quoted span için part-link’i yeni sürüme taşı veya yeniden pinle; whole-link’i sürüme bağlı bırak. Alternatif, bu davranışı açıkça kabul etmek.

2. **MEDIUM — Recursive kontrolün maliyeti aday bağlantılarla çarpılıyor.**  
   **Konum:** `src/hlmemo/db/read_queries.py:688`, `:791`, `:815`.  
   **Senaryo:** H hit, hit başına L pinned link, D ancestor derinliği ve B gövde uzunluğunda iş yaklaşık **O(H×L×D×B)** olabilir. Raw SQL limiti ve query’nin Python dilimlemesi, pahalı filtrelemeden sonra çalışır; aday/derinlik sınırı yok.  
   **İndeks:** version PK mevcut; `links_dst`/`links_dst_version` indeksleri yalnız `superseded_at='infinity'` satırlarını kapsar (`alembic/versions/0001_phase0.py:245`). Temporal predicate bunların kullanımını garanti etmez. Varsayılan 10 saniyelik statement timeout gecikmeyi başarılı sonuçla sınırlamaz; sorguyu keser.  
   **Minimal fix:** ancestry’yi distinct hit version başına bir kez hesaplayıp linklerle join et; yüksek L/D için `EXPLAIN ANALYZE` ve süre ölçümü yap. Burada ölçülmüş timeout yok.

3. **LOW — Eski wrapper tüm pinned bağlantıları sessizce yok sayıyor.**  
   **Konum:** `src/hlmemo/db/librarian_queries.py:432`.  
   **Senaryo:** `superseded_among()` sürüm haritasını iletmiyor; varsayılan `None` pinned eşleşmeleri kaldırıyor. Mevcut ürün çağrısı bulunmadı.  
   **Minimal fix:** `version_of` parametresini ilet veya kullanılmayan wrapper’ı kaldır.

**Prod etkisi:** kayıtlı küme **252 explicit whole + 118 backfill part + 1 backfill whole = 371**. İki yazıcı da `dst_version_id=None` üretir (`ops/explicit_links.py:99`, `ops/backfill_links.py:268`): yeni pinned predicate nedeniyle etkilenen **0/371**. Bu, dağıtım kaydı ve kod doğrulamasıdır; canlı DB denetimi değildir. Eski okuyucuların aktif çağrıları sürüm haritasını iletiyor (`core/read_service.py:257`, `core/research_service.py:751`); pinned bağlantılarda bulgu 1 geçerli.

**Supersede not-whole muafiyeti:** verilen rubric altında güvenli. Açık supersede modu bütün item’ı kapatır; unique/boundary, expected-version, authorization ve visibility kontrolleri korunmuştur. Historical hedefler link-only kalır.

**Release:** `deploy/RUNBOOK.md:928` doğru. `fda8fa0` üzerine B3 rollback’i pre-upgrade ref+dump’a döner; **dağıtım sonrası bütün yazılar kaybolur**. R4.2 önce dağıtılırsa rollback çifti R4.2 ref+dump olur; birleşik release ayrıca doğrulanmalıdır. B3 event’lerini koruyarak yalnız eski image’a dönmek replay divergence yaratır. Compensating revert, veriyi pre-B3 uyumlu yapmaz.

**Doğrulama sınırı:** iki diff ve iki bağımsız inceleme tamamlandı; `git diff --check` temiz. İzole birim koşusunda **73 geçti**, 1 test geçici dizin/tokenizer erişiminde durdu. Docker fixture’ı çalışmadığından entegrasyon, replay ve deploy/rollback provası bu oturumda doğrulanmadı. Kod değiştirilmedi.