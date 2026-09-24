## Verdict (FIX-NEEDED)

0009’un yükseltmesi eklemeli ve kısa lock timeout’lu; R3 dağıtımı yazarları durdurduğu için release-safe, fakat zero-downtime değildir. W0a’nın dump-restore rollback’i doğru. Somut provider/cache/G5 sızıntısı ve fusion’da çift oy bulmadım; DF invalidation da doğru. Ancak veri bütünlüğü, yanıltıcı önizleme, D-086 uyumu ve başarısız latency kapısı nedeniyle bu haliyle merge edilmemeli.

## Findings

| severity | file:line | trigger (concrete scenario) | fix |
|---|---|---|---|
| HIGH | `alembic/versions/0009_language_pivot.py:104-120` | R3 event/job ürettikten sonra `alembic downgrade 0008` yalnız tabloları düşürür; `translate:*`/`embed_rendition:*` işler kalır ve eski replay `rendition_upsert` mutation’ında durur. W0a etkilenmez çünkü snapshot restore kullanır. | Yeni artefakt varsa downgrade’ı reddet; operasyonel rollback’in yalnız W0a snapshot restore olduğunu belgele. |
| HIGH | `src/hlmemo/core/language.py:199-200,253-266` | `/Config/Foo.py`→`/config/foo.py` ve `--dry-run`→`dry run` kabul edilir; casefold ve tire-parçalama “verbatim” garantisini bozar. | Path/id/command/flag için case-sensitive NFC exact karşılaştırma yap; flag’leri parçalama. |
| HIGH | `src/hlmemo/librarian/tasks/translate.py:157-192,229-239` | 17+ chunk içerikte yalnız ilk 16 çevrilir; yine tam gövde digest’iyle `accepted` yazılır ve sonraki backfill kalanı sonsuza dek atlar. | Tüm chunk’ları resumable batch’lerle tamamla veya eksik sonucu `partial/rejected` tut. |
| HIGH | `src/hlmemo/core/retrieval.py:357-371` | Kabul edilen negation/modality flip rendition baskın eşleşirse İngilizce ters anlam doğrudan preview olur. | Preview daima original olsun; çeviriyi ayrı, açıkça non-authoritative hint olarak döndür. |
| HIGH | `src/hlmemo/librarian/worker.py:319-373`; `src/hlmemo/db/replay.py:312-315` | Tekrarlanan outage/budget/disabled hand-back’leri sınırsız authoritative defer event’i üretir; terminal event kimliği de D-086’ya uymaz. | D-086’yı port et: systemic hand-back row-only, ≤4 defer, tek `uuid5("job:<key>")` terminal event. |
| MEDIUM | `src/hlmemo/core/term_stats.py:102-112` | Aynı canonical unit’te terim original ve rendition’da varsa DF iki kez sayılır; ör. gerçek 15/100, hesaplanan 30/100 olur ve terim yanlış elenir. | DF’yi canonical original unit üzerinden union olarak hesapla. |
| MEDIUM | `src/hlmemo/server/mcp_server.py:66-71`; `server/tools/query.py:24-30`; `server/tools/__init__.py:44-49`; `server/tools/schemas.py:97-98` | Tüm flag’ler kapalıyken MCP instructions/schema hâlâ English query ve `index_en` ister; “byte-identical” iddiası yanlıştır. | Yüzeyi flag/capability ile koşullandır veya iddiayı yalnız flag’siz request davranışıyla sınırla. |
| MEDIUM | `src/hlmemo/core/retrieval.py:235-259` | İlk dört hit aynı source’tansa alternatif yokken hepsi top-5’te kalır; vaat edilen `≤2/source` uygulanmaz. | Gerçek cap uygula veya kararı açıkça “best effort” olarak değiştir. |
| MEDIUM | `src/hlmemo/core/read_service.py:305-319`; `src/hlmemo/db/rendition_queries.py:65-92,146-159` | %100 rendition coverage’da üç ek seri sorgu, 1000-row lexical rerank ve exact vector scan p95’i 541/515 ms’ye çıkarır. | Önce rendition-only candidate bütçelerini düşürüp recall gate’i çalıştır; gerekirse original/rendition leg’lerini tek SQL’de birleştir. |

## Positions

(a) English previews: Original gösterilmeli; translation yalnız ayrı ve non-authoritative hint olmalı.  
(b) Observer renditions: D-074 ihlali değildir; user data değil, rebuildable retrieval projection’dır—kararda açıkça yazılmalı.  
(c) Latency: En ucuz ilk adım `PRE_FACTOR=10` ve rendition `V_MAX` bütçesini azaltıp W‑E/G‑L3’ü yeniden çalıştırmaktır.

## Decision-log text

- R3 language pivot, validator/completeness/DF/cap/D-086 kusurları giderilene kadar blocked.
- 0009 upgrade yalnız quiesced W0a deploy ile kabul; schema downgrade operasyonel rollback değildir.
- Original preview authoritative; translation hint’tir. Observer rendition rebuildable index projection’dır.
- `HLM_RENDITIONS` ancak yüksek-coverage latency ve retrieval kalite kapıları geçince açılır.