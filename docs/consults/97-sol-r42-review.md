## Verdict

**NO-GO** — Astra-low ve Sol-xhigh bağımsız incelemeleri birleşti; iki somut HIGH release blocker var.

1. **HIGH — F1 opaque değerlerden desteklenmeyen literal üretiyor — `src/hlmemo/librarian/tasks/research.py:2085`.** Excerpt ``https://example.com/?filter=a|b`` iken cevap ``...?filter=b`` kabul ediliyor. Ayrıca `v1|v2-3 → v1-3`, `CVE-2025-[fix]1234 → CVE-2025-1234` ve `foo[bar[baz]] → foobarbaz` üretiliyor. `v1|v2 → v12` ve salt sayısal alternation doğru şekilde reddediliyor. **Minimal fix:** URL query/fragment’lerinde genişletmeyi kapat; rakam içeren prefix/suffix ve nested bracket’ları reddeden negatif testler ekle.

2. **HIGH — F2 restate desteklenmeyen iddiayı validation’dan geçiriyor — `src/hlmemo/librarian/tasks/research.py:3045`, `src/hlmemo/core/research_service.py:1730`.** Excerpt “Port 8443 serves read-only health checks” iken `restate_ok("Port 8443 permanently deletes all backups.") == True`; yanlış yüklem final cevaba giriyor. Soru içindeki excerpt-dışı literal’ler de “question-supported” sayılıyor. **Minimal fix:** restate’i extractive/verbatim, doğrulanmış excerpt span’ına bağla ve question literal’lerini bu kontrolde destek sayma; bu guard gelene kadar flag default-off olmalı.

3. **MEDIUM — MAIN_REPAIR install-time manifest kontrolünde yok — `deploy/scripts/check_librarian.py:130`.** Flag fingerprint ediliyor fakat `MANIFEST_KEYS`/`R4_TRACKED` içinde değil; API effective `true`, librarian `false` senaryosu `_manifest_failures == []` veriyor. **Minimal fix:** anahtarı `R4_TRACKED` içine ekle; absent/true/false serbest kalırken disk/API/librarian eşitliği denetlensin.

4. **MEDIUM — F3 cap, sıfır-slot merge’i erken kesiyor — `src/hlmemo/core/research_service.py:394`.** `collapse_windows(["v5.0","v5.1"], 1)` ikinci pencereyi `cap` diye atıyor; oysa mevcut pencereyi `0..2`ye ücretsiz genişletebilirdi. **Minimal fix:** dedupe/merge kararını slot-limit kontrolünden önce yap.

5. **MEDIUM — Birleşik pencerenin citation handle’ı alıntıyı yeniden açamayabilir — `src/hlmemo/core/research_service.py:347`.** `["v6.0","v6.3"]` dahili `0..4` metnini `v6.0` altında sunuyor; normal drilldown yalnız `0..1` açıyor. **Minimal fix:** tek ±1 handle ile temsil edilemeyen union’ları birleştirme veya gerçek member/range provenance döndür.

6. **MEDIUM — Anaphora kuralı bağımsız destekli “This” cümlesini düşürüyor — `src/hlmemo/librarian/tasks/research.py:2546`.** Hatalı ilk cümleden sonra excerpt’te aynen bulunan “This service has backups enabled.” de siliniyor. **Minimal fix:** çıplak zamirleri koşulsuz anaphora sayma; bağımsız exact support varsa koru.

7. **MEDIUM — Preview skip gerçek içeriği metadata sanıyor — `src/hlmemo/librarian/tasks/research.py:887`.** `VERIFIED backups are disabled…` satırı tamamen gizleniyor. **Minimal fix:** `VERIFIED/UNVERIFIED` için migration biçimindeki tarih ve `:` şartını doğrula.

8. **MEDIUM — Prompt/schema yerinde değişti, audit sürümü değişmedi — `src/hlmemo/librarian/prompts/research/v3.md:34`.** Sistem hash’i `73e68cc6791c → ae0c7e46f41a`, fakat ledger hâlâ `prompt_version/schema_version=v3` kaydediyor. Cassettes fail-closed miss verir; sürüm bazlı yeniden üretim belirsizleşir. **Minimal fix:** yeni prompt/schema revision oluşturup açıkça seç.

Restate’in privacy gate, carried-source gate, attempt budget, reservation/settlement ve ledger yollarında bypass bulunmadı. A+B birleşimi B’nin provider/profile davranışlarını koruyor; prod’da eksik key default `true` yükleniyor ve env-snapshot rollback çalışıyor.

Kontroller: güncel HEAD `29f132a`; 143 hedefli test + 29 subtest geçti, `git diff --check` geçti. Beş R4.2 integration vakası bu worktree’de E5 model dosyası bulunmadığı için setup aşamasında çalışamadı.