## Round-1 closure

| ID | Durum | Kanıt | CLOSED değilse kalan somut senaryo |
|---|---|---|---|
| R-1 | CLOSED | `src/hlmemo/ops/backfill_links.py:159-183`; `test_a_cross_project_proposal_rejects_the_whole_apply`; `test_a_stale_head_rejects_the_whole_apply` | — |
| R-2 | CLOSED | `deploy/backup/backup.sh:53-73`; `deploy/scripts/rollback.sh:121-127,229-241`; `test_missing_safety_dump_after_the_destructive_phase_fails_closed` | — |
| R-3 | CLOSED | `deploy/scripts/install_llm_env.sh:73-87,151-169,341-356`; `test_r4_env_switch_with_reset_then_behaviour_only_rollback_to_the_r3_template` | — |
| R-4 | CLOSED | `deploy/scripts/llm_env_release.py:88-138,191-205`; `test_fingerprint_covers_the_r4_behaviour_keys_and_the_guard` | — |
| R-5 | CLOSED | `profiles/google-gemini38-flash-medium.toml:12-20`; `src/hlmemo/librarian/provider.py:674-686`; `test_ask_r4_an_expired_writer_price_falls_back_counted` | — |
| R-6 | CLOSED | `src/hlmemo/ops/service.py:568-619`; `test_ask_r4_ops_status_shows_the_writer_fallback` | — |
| R-7 | CLOSED | `src/hlmemo/cli/links.py:239-266`; `test_cli`; `test_revert_is_project_wide` | Orijinal eksik `--project` kusuru kapalıdır; yeni önizleme kusuru N-1’dir. |
| R-8 | CLOSED | `src/hlmemo/librarian/provider.py:384-413,1112-1173`; `test_settlement_and_ledger_use_the_normalized_tokens`; `test_the_per_question_tally_uses_the_same_numbers` | — |
| R-9 | CLOSED | `src/hlmemo/librarian/provider.py:868-878,893-911`; `src/hlmemo/core/research_service.py:1247-1267`; `test_an_unaffordable_writer_retry_is_never_reserved`; `test_a_truncated_writer_is_not_retried` | — |
| R-10 | PARTIAL | `tests/integration/test_r4_load_gate.py:104-203` bağlantı bırakılmasını ve beşinci ask’in `busy` olmasını doğruluyor | `SCALE=0.05` ve in-process ASGI nedeniyle gerçek 170 saniyelik MCP/Caddy/uvicorn yolu doğrulanmıyor (`:40-41,80-101`). |
| R-11 | CLOSED | `deploy/scripts/check_librarian.py:693-713`; `test_r4_trace_dir_in_the_api_fails` | — |
| R-12 | CLOSED | `src/hlmemo/ops/backfill_links.py:124-269`; `test_apply_then_revert_round_trips_and_replays`; `test_the_proposer_is_not_ported` | — |
| R-13 | CLOSED | `deploy/scripts/install_llm_env.sh:69,94-100`; `test_r4_env_switch_with_reset_then_behaviour_only_rollback_to_the_r3_template` | Orijinal belirsiz hedef kusuru kapalıdır; smoke adımındaki yeni önizleme kusuru N-1’dir. |
| R-14 | CLOSED | `src/hlmemo/ops/probe.py:51-106`; `deploy/scripts/check_librarian.py:602-660`; `test_wrong_key_401_fails`; `test_expired_price_and_transport_errors` | — |
| R-15 | PARTIAL | Doğru kontroller `docs/decisions/R4-RELEASE-PLAN.md:148-155` içinde tanımlı | Verilen sonuç yalnız 47 değişen dosyanın gitleaks taramasını kanıtlıyor; geçmişteki silinmiş/private blob, büyük blob ve kamuya açık doküman incelemesi henüz kanıtlanmış değil. |
| R-16 | PARTIAL | Karar sırası `docs/decisions/R4-RELEASE-PLAN.md:194-201` içinde önceden kayıtlı | Abstain paydası, iki maliyet ölçümünden hangisinin bağlayıcı olduğu ve fallback kohortu tanımsız. |
| R-17 | CLOSED | `src/hlmemo/librarian/tasks/research.py:3312-3321`; `test_the_prose_override_changes_only_the_prose_job` | — |

## Round-2 checks

- **V1 — YES.** Google için `usage_reasoning="excluded"` normalizasyonu settlement ve ledger’a aynı verileri taşır; soru toplamı ledger satırlarını kullanır. OpenRouter’da `usage.cost` USD otoritesidir; güvenilmez token kullanımı worst-case’e yerleşir (`provider.py:384-413,1112-1173`; `research.py:3054-3074`).

- **V2 — YES.** İkinci writer denemesi affordability kontrolünden geçmeden rezerve edilemez; truncation doğrudan, şema hatası ise tekrar pahalıysa research-primary fallback’e gider ve fallback ayrıca attempt guard’dan geçer (`provider.py:868-911`; `research_service.py:1247-1267`). Zincir sonludur; profile döngüsü veya fallback’in writer’a geri dönmesi yoktur.

- **V3 — YES.** Süresi geçmiş profil ağ çağrısı ve rezervasyondan önce atlanır; response flag, ops status ve probe bunu görünür kılar (`provider.py:674-686`; `research_service.py:1229-1236`; `ops/service.py:599-619`; `ops/probe.py:63-68`). Ledger satırının olmaması cap blind spot değildir: bu profil için istek, rezervasyon ve harcama yoktur.

- **V4 — YES.** Probe yalnız sabit alanları basar; 401 gövdesi ve secret içeren transport exception testi çıktıya sızma olmadığını doğrular. Evaluate stderr’i atar ve alanları whitelist eder; key-file 0600 oluşturulup silinir, reset kurulumu template’in adlandırdığı bütün profil anahtarlarını key-file’dan yeniden yazar (`probe.py:51-106`; `check_librarian.py:602-660`; `install_llm_env.sh:268-340`; `RUNBOOK.md:727-754`).

- **V5 — YES.** İki head proje üyeliği endpoint kilitleri altında kontrol edilir; tek hata tüm transaction’ı exit 65/sıfır event ile reddeder. Revert proje kapsamlıdır ve apply/revert replay özdeşliği test edilmiştir (`backfill_links.py:159-183,237-269`; `test_backfill_links.py:121-228`). N-2/N-3 ayrı çevrim-bütünlüğü kusurlarıdır.

- **V6 — YES.** Safety dump rotasyon dışında tutuluyor ve eksikliğinde recovery fail-closed; `--release-template` R3 cap’leri 1/2/10 ile kurup R3 evaluate’ı geçiriyor. Yeni davranış, timeout, cap ve guard anahtarları fingerprint kapsamındadır (`backup.sh:53-73`; `rollback.sh:229-241`; `llm_env_release.py:88-138`; `test_release_convergence.py:293-349`).

- **V7 — PARTIAL.** Test, LLM bekleyen ask task’larının bağlantı tutmadığını ve beşinci ask’in hızlı `busy` döndüğünü gerçekten doğruluyor (`test_r4_load_gate.py:104-197`). Fakat 170 saniye 8,5 saniyeye ölçeklenmiş ve proxy/client katmanı yoktur; DB statement timeout için somut tehlike görünmüyor, ancak gerçek MCP/Caddy zaman yolu kanıtlanmamıştır (`:40-41,80-101`).

- **V8 — YES.** R4 manifest unset writer kabul ediyor; evaluate yine `HLM_PROFILE` veya research fallback zincirinin başındaki gerçek yazarı probe etmeyi zorunlu tutuyor (`check_librarian.py:693-773`; `test_r4_accepts_an_unset_writer_without_a_gemini_key`; `test_e_unset_writer_expected_probe_profile_is_what_probe_writer_reports`).

- **V9 — NO.** Karar sırası nettir, ancak 40 sorunun verileri şu üç eşiği tek anlamlı biçimde belirleyemez: `abstain ≥ .90` için payda/scoring, `$ /q ≤ .03` için Google mı ledger mı, fallback payı için yalnız hold-out soruları mı yoksa 24 saatlik answered-call kohortu mu (`R4-RELEASE-PLAN.md:192-201`; `ops/service.py:568-617`).

## New findings

| ID | Severity | Threat | Konum | Özet |
|---|---|---|---|---|
| N-1 | MEDIUM | T6 | `deploy/RUNBOOK.md:786-799`; `src/hlmemo/cli/links.py:220-222`; `src/hlmemo/ops/backfill_links.py:151,193-194` | Zorunlu backfill önizleme komutları çalışmıyor; belirtilen başarı ölçütü de gerçek çıktıyla uyuşmuyor. |
| N-2 | MEDIUM | T3 | `src/hlmemo/ops/backfill_links.py:137-143`; `src/hlmemo/ops/explicit_links.py:66-78` | Çevrim kontrolü teklif uçları dışındaki ara canlı linkleri göremiyor. |
| N-3 | MEDIUM | T3 | `src/hlmemo/ops/backfill_links.py:138-159`; `src/hlmemo/librarian/actor.py:190-202` | Canlı-link okuması ile endpoint kilitleri arasındaki yarış ters linkli iki düğümlü çevrim oluşturabilir. |

### N-1

- **Severity:** MEDIUM
- **Threat:** T6
- **File:** `deploy/RUNBOOK.md:786-799`; `src/hlmemo/cli/links.py:220-222`; `src/hlmemo/ops/backfill_links.py:151,193-194`
- **Failure scenario:** Operatör RUNBOOK’taki apply veya revert önizlemesini `--preview` ile çalıştırır; CLI yalnız `--dry-run` tanıdığı için işlem argument parsing aşamasında durur. Bayrak düzeltilse bile preview `applied=0` döndürdüğünden “applied = approved count” koşulu yanlış biçimde başarısız olur.
- **Evidence:**

  > `... hlm links backfill --project hlmemo --apply ... --preview`  
  > `bool, typer.Option("--dry-run", help="Report what --apply/--revert would do, then roll back")`

- **Minimal fix:** Plan ve RUNBOOK’ta `--preview` yerine `--dry-run` kullan; başarı ölçütünü `len(links) == approved_count` yap veya açık bir `would_apply` alanı ekle.

### N-2

- **Severity:** MEDIUM
- **Threat:** T3
- **File:** `src/hlmemo/ops/backfill_links.py:137-143`; `src/hlmemo/ops/explicit_links.py:66-78`
- **Failure scenario:** Canlı graph `A→B→C` içerirken onaylı dosya yalnız `C→A` teklif eder. Sorgu yalnız teklif uçları `{A,C}` arasındaki linkleri yüklediğinden ara `B` görünmez ve üç düğümlü çevrim prod’a yazılır.
- **Evidence:**

  > `existing = await xl.live_supersedes(conn, lids)`  
  > `AND src_logical_id = ANY(%(l)s) AND dst_logical_id = ANY(%(l)s)`

- **Minimal fix:** Her teklif için destination’dan source’a tam canlı graph erişilebilirliğini kontrol et veya ilgili bağlı bileşeni yükle; `A→B`, `B→C`, teklif `C→A` testinde sıfır event/link iste.

### N-3

- **Severity:** MEDIUM
- **Threat:** T3
- **File:** `src/hlmemo/ops/backfill_links.py:138-159`; `src/hlmemo/librarian/actor.py:190-202`
- **Failure scenario:** Başlangıçta `A↔B` linki yokken backfill `A→B` için canlı linkleri okur; eşzamanlı explicit/librarian işlemi endpoint kilitlerini alıp `B→A` yazar ve commit eder. Backfill kilitten sonra canlı linkleri yeniden okumadığı ve materializer yalnız aynı yönü dedupe ettiği için `A→B` de yazılır.
- **Evidence:**

  > `existing = await xl.live_supersedes(conn, lids)`  
  > `await q.lock_logical_ids(conn, sorted({lid for r in fresh for lid in _pair(r)}))`

- **Minimal fix:** Endpoint kilitlerini canlı-link/çevrim kontrolünden önce al ve kontrolü kilit altında yeniden çalıştır; ayrıca iki transaction’lı ters-link yarış testi ekle.

## Residual risks for the owner

- **N-1:** Owner, çalışmayan önizleme/rehearsal yolunu kabul etmeli veya prod link adımından önce bayrak ve başarı ölçütünü düzelttirmelidir.
- **N-2/N-3:** Owner, statik ve eşzamanlı çevrim riskini açıkça kabul etmeli veya prod link apply öncesinde graph kontrolünü kilit altında düzeltmelidir.
- **R-10/V7:** Owner, gerçek 170 saniyelik MCP/Caddy yolunun test edilmemesini kabul etmeli veya gerçek zamanlı prod-like testi zorunlu kılmalıdır.
- **R-15:** Owner, push öncesinde §2.3’ün full-history/private-path/blob/manual-doc kontrollerini çalıştırmalı veya kamuya açılabilecek geçmiş içerik riskini kabul etmelidir.
- **R-16/V9:** Owner, ilk hold-out sorusundan önce abstain paydasını, bağlayıcı maliyet kaynağını ve fallback kohortunu tanımlamalı; aksi halde sonuç tablosunun eylemi deterministik değildir.

## Verdict

**GO-WITH-FIXES.** Yeni CRITICAL/HIGH yoktur; ancak **N-1, N-2 ve N-3** prod link apply öncesinde düzeltilmeli, **R-15** kontrolleri push öncesinde kanıtlanmalı ve **R-16/V9** ölçüm tanımları test başlamadan sabitlenmelidir. **R-10/V7** ise owner’ın açıkça kabul veya reddetmesi gereken son operasyonel residual’dır.