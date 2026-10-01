## Round-1 closure

`W` = `.claude/worktrees/agent-ab919c6d103d1b46b` (`f02c39d`); `P` = `docs/decisions/R4-RELEASE-PLAN.md`. Kod yolları W’ye göredir. İnceleme salt okunurdu; testler yeniden çalıştırılmadı, özel dosyalar veya anahtarlar okunmadı.

| Finding | Durum | Kanıt | Kalan somut senaryo |
|---|---|---|---|
| R-1 | PARTIAL | `src/hlmemo/ops/backfill_links.py:159`; `tests/integration/test_backfill_links.py:170` | Yeni yazılacak yabancı/stale çiftler tüm işlemi reddediyor. Ancak `already_linked` elemesi doğrulamadan önce: geçerli A önerisiyle A etiketli, önceden bağlanmış B çifti verilirse A değişikliği commit edilir; beklenen exit 65/sıfır event gerçekleşmez. |
| R-2 | CLOSED | `deploy/scripts/rollback.sh:124`, `:236`, `:281`; `tests/deploy/test_release_convergence.py:480`, `:505` | — |
| R-3 | CLOSED | `deploy/scripts/install_llm_env.sh:85`, `:164`; `tests/deploy/test_release_convergence.py:293` R4→R3 caps, evaluate PASS ve boş journal kontrolü | — |
| R-4 | CLOSED | `deploy/scripts/llm_env_release.py:104`, `:121`, `:132`; `tests/deploy/test_llm_env_release.py:285`, `:341` | — |
| R-5 | CLOSED | `profiles/google-gemini38-flash-medium.toml:17`; `src/hlmemo/librarian/provider.py:675`; `deploy/scripts/check_librarian.py:678`; `test_no_usable_profile_fails_closed` | — |
| R-6 | PARTIAL | `src/hlmemo/ops/service.py:586`; `tests/integration/test_ask_research.py:765` | Fallback görünür; fakat oran soru değil başarılı prose çağrısı başına hesaplanıyor. Refine ve başarısız fallback yolları karar oranını değiştiriyor; N-1. |
| R-7 | CLOSED | `src/hlmemo/cli/links.py:244`; `deploy/RUNBOOK.md:800`; `tests/integration/test_backfill_links.py:259` | Zorunlu proje ve gerçek revert komutu düzeltildi; önizleme komutundaki ayrı regresyon N-2. |
| R-8 | CLOSED | `src/hlmemo/librarian/provider.py:384`, `:1113`, `:1161`; `tests/unit/test_r4_usage.py` | Eksik/çelişkili kullanım ihtiyatlı ücretleniyor; planın 2100-token örneğinden farklılık V1’de açıklanmıştır. |
| R-9 | CLOSED | `src/hlmemo/librarian/provider.py:868`, `:935`; `src/hlmemo/core/research_service.py:1247`; `test_an_unaffordable_writer_retry_is_never_reserved` | — |
| R-10 | PARTIAL | `tests/integration/test_r4_load_gate.py:175`, `:179`, `:194` | Havuz ve busy davranışı sınanıyor; gerçek 170 saniyelik Caddy/uvicorn/MCP yolu ve yük altında `/ready` sınanmıyor. |
| R-11 | CLOSED | `deploy/scripts/check_librarian.py:705`, `:709`; `tests/deploy/test_r2_librarian.py:757` | — |
| R-12 | CLOSED | `src/hlmemo/ops/backfill_links.py:216`, `:232`, `:241`; `tests/integration/test_backfill_links.py:109`, `:121` | — |
| R-13 | CLOSED | `P:133`, `P:138`, `P:140`; `deploy/scripts/install_llm_env.sh:69`, `:94`; `tests/deploy/test_llm_env.py:342` | Yerel hedef/SSH prosedürü düzeltildi; gerçek VM provasının tamamlandığına dair hüküm değildir. |
| R-14 | CLOSED | `src/hlmemo/ops/probe.py:32`, `:86`; `deploy/scripts/check_librarian.py:752`; `tests/deploy/test_release_convergence.py:264` | API’ye özel yanlış credential yakalanıyor; ayrı protokol doğrulama eksikliği N-4. |
| R-15 | PARTIAL | `P:148` tam geçmiş kontrollerini tanımlıyor; verilen gitleaks sonucu yalnız 47 değişen dosyayı kapsıyor | Tam gönderilecek geçmiş ve özel fakat secret olmayan içerik kontrolünün geçtiği gösterilmedi; public push temizliği henüz doğrulanmış değil. |
| R-16 | PARTIAL | `P:194`, `P:199`, `P:200`; `src/hlmemo/ops/service.py:597` | Karar sırası belli; fakat fallback paydası/penceresi, abstain paydası, maliyet otoritesi ve p95 yöntemi sonuç görülmeden kesinleştirilmemiş. |
| R-17 | CLOSED | `src/hlmemo/librarian/tasks/research.py:3318`; `tests/unit/test_r4_settings.py:117` | Expand 1500 token kalıyor. |

## Round-2 checks

| Kontrol | Sonuç | Değerlendirme |
|---|---|---|
| V1 — T1 | YES | Google settlement ve ledger aynı normalizasyonu, soru toplamı ledger satırını kullanıyor; OpenRouter `usage.cost` USD otoritesi. Güvenilir maliyet yoksa worst-case uygulanıyor (`provider.py:1113`, `:1133`, `:1171`; `tasks/research.py:3072`). `1000/100/0/3100`, plandaki 2100 yerine çelişkili sayılıp 16000 output/worst-case oluyor; eksik ücretlendirme değil. |
| V2 — T1 | YES | Retry affordability kontrolünden, **her** deneme rezervasyondan önce guard’dan geçiyor; fallback de buna dahil (`provider.py:874`, `:935`; `research_service.py:1256`). Zincir ileri ilerliyor; şema tekrarı sınırlı, writer’a geri dönüş döngüsü yok (`provider.py:674`, `:701`, `:872`). |
| V3 — T8 | YES | Expired profil ağ çağrısı/rezervasyondan önce atlanıyor; status expiry alanı, probe hatası ve başarılı fallback’in meta nedeni mevcut (`provider.py:675`; `ops/service.py:603`; `ops/probe.py:65`; `test_ask_research.py:812`). Harcama oluşmadığından eksik ledger satırı cap açığı değildir; kalıcı skip sayacı olmaması gözlemlenebilirlik sınırıdır. |
| V4 — T2 | YES | İncelenen HTTP hata yolları mesaj/header/body basmıyor; evaluator subprocess çıktısını filtreliyor (`ops/probe.py:87`; `check_librarian.py:621`). RUNBOOK private/0600 dosya ve silme adımlarını içeriyor (`RUNBOOK.md:730`, `:754`); reset kurulu değerleri korumaz, dosyadaki **iki gerekli anahtarı** yeniden kurar (`install_llm_env.sh:151`, `:310`; `test_release_convergence.py:315`). |
| V5 — T3 | PARTIAL | Fresh çiftlerde kilit altında iki head/proje kontrolü, transaction rollback ve exit 65 doğru; revert proje kapsamlı, replay kayıtlı mutations üzerinden deterministik (`backfill_links.py:159`, `:237`; `cli/links.py:263`; `test_backfill_links.py:109`). Önceden elenen çiftler bütün-dosya reddini atlıyor: R-1. |
| V6 — T6 | YES | Safety dump rotasyon dışındaki `rollback/` dizininde, ortak kilit altında; kayıpsa recovery journal’ı kapatmıyor (`rollback.sh:124`, `:236`, `:281`). R3 cap/evaluate dönüşü ve yeni fingerprint alanları testle kapsanmış (`test_release_convergence.py:293`; `llm_env_release.py:104`, `:121`). |
| V7 — T4 | PARTIAL | Havuzun boşluğu ve beşinci ask’in busy olması gerçek assertion’lar; ancak ×0.05, 30 s deadline ve ASGITransport gerçek 170 s yolu gizliyor (`test_r4_load_gate.py:40`, `:90`, `:99`, `:175`). LLM boyunca açık DB statement/transaction bulmadım; keep-alive aktif cevap süresi değildir (`middleware.py:457`; `Caddyfile:35`; `server/app.py:523`), fakat gerçek yol ve `/ready` kanıtı eksik. |
| V8 — T6/T8 | YES | Manifest unset writer kabul ediyor; probe hâlâ zorunlu ve araştırma zincirinin başını seçiyor: uygun primary, aksi halde research fallback (`check_librarian.py:180`, `:717`, `:752`; `ops/probe.py:43`). Cross-contract kapsamı: `tests/deploy/test_r4_cross_contract.py:446`. |
| V9 | PARTIAL | REVERT önceliği ve ara bölgenin OWNER CALL olması açık (`P:195`). Ancak abstain’in paydası, hold-out fallback oranı yerine 24 saatlik çağrı oranının kullanımı, Google/ledger maliyetlerinden hangisinin belirleyici olduğu ve p95 hesaplama yöntemi sabitlenmedi (`P:164`, `P:192`, `P:199`; `ops/service.py:587`). |

40 soru aritmetiği engellemez: payda bütün 40 soruysa KEEP için ≥28 doğru ve ≤1 contradiction; REVERT için ≤25 doğru veya ≥3 contradiction gerekir. Fakat `abstain ≥.90` için negatif alt kümenin paydası, fallback için soru/pencere tanımı ve sınırdaki maliyet/p95 kararları mevcut tarifle tek anlamlı değildir.

## New findings

Yeni CRITICAL/HIGH doğrulamadım. Aşağıdaki MEDIUM bulgular diff’in getirdiği somut kusurlardır.

| ID | Severity | Threat | Konum | Özet |
|---|---|---|---|---|
| N-1 | MEDIUM | T8 | `src/hlmemo/ops/service.py:587` | Fallback çağrı oranı, soru oranı yerine kullanılıyor. |
| N-2 | MEDIUM | T6 | `deploy/RUNBOOK.md:786` | Önizleme flag’i ve başarı ölçütü CLI ile uyuşmuyor. |
| N-3 | MEDIUM | T3 | `src/hlmemo/ops/backfill_links.py:137` | Mevcut ara düğümden geçen supersession döngüsü kaçıyor. |
| N-4 | MEDIUM | T8 | `src/hlmemo/ops/probe.py:102` | Geçersiz completion protokolü probe’dan PASS alabiliyor. |

### N-1 — Fallback oranı yanlış paydayla hesaplanıyor

**Severity:** MEDIUM · **Threat:** T8  
**Konum:** `src/hlmemo/ops/service.py:587`, `:597`, `:617`; `src/hlmemo/core/research_service.py:1794`.

**Senaryo:** 40 sorunun 11’inde ilk prose luna fallback ile geçerli abstention döndürür, refine sonrası Gemini cevaplar; diğer 29 soru doğrudan Gemini’den gelir. Kod 11/51 = %21,6 raporlar; herhangi bir fallback yaşayan soru oranı 11/40 = %27,5’tir ve planın %25 REVERT eşiğini aşar.

**Kanıt:**

```python
"SELECT profile, outcome, count(*) FROM llm_calls WHERE task = %s"
answered = sum(used.values())
```

**Minimal düzeltme:** Fallback’i soru/lineage başına kaydet ve say; final-test oranını yalnız hold-out sorularından üret. Başarısız fallback’lerin paydaya/numeratöre katılımını da önceden tanımla.

**Regresyon:** Yukarıdaki 40-soru/51-prose fixture’ı soru oranını %27,5 vermeli.

### N-2 — Belgelenen önizleme çalışmıyor

**Severity:** MEDIUM · **Threat:** T6  
**Konum:** `deploy/RUNBOOK.md:786`, `:787`, `:799`; `src/hlmemo/cli/links.py:220`.

**Senaryo:** Operatör apply/revert önizlemesini aynen çalıştırınca `--preview` bilinmeyen seçenek olarak reddedilir. `--dry-run` kullanıldığında da belgelenen “applied = approved count” şartı sağlanamaz; önizleme kasıtlı olarak `applied=0` döndürür.

**Kanıt:**

```python
bool, typer.Option("--dry-run", help="Report what --apply/--revert would do, then roll back")
assert len(preview["links"]) == 2 and preview["applied"] == 0 and await _counts(connect) == base
```

**Minimal düzeltme:** Plan/RUNBOOK’ta `--dry-run` kullan; onaylanan sayıyı `len(links)` ile karşılaştır ve sıfır yazmayı doğrula.

**Regresyon:** Belgede yazan tam apply/revert önizleme komutları CLI testinde exit 0 ve değişmeyen event/link sayıları üretmeli.

### N-3 — Döngü kontrolü mevcut ara düğümleri görmüyor

**Severity:** MEDIUM · **Threat:** T3  
**Konum:** `src/hlmemo/ops/backfill_links.py:137`; çağırdığı `src/hlmemo/ops/explicit_links.py:74`.

**Senaryo:** Projede canlı `C→B` ve `B→A` supersedes linkleri varken dosya yalnız `A→C` önerir. Sorgu yalnız `{A,C}` arasındaki mevcut linkleri yüklediğinden B üzerinden geçen yolu kaçırır ve üç düğümlü döngüyü commit eder.

**Kanıt:**

```python
lids = sorted({lid for r in kept for lid in _pair(r)})
AND src_logical_id = ANY(%(l)s) AND dst_logical_id = ANY(%(l)s)
```

**Minimal düzeltme:** Döngü kontrolüne projenin ilgili canlı supersession grafiğini veya gerekli erişilebilir yolları dahil et.

**Regresyon:** `C→B`, `B→A` mevcutken `A→C` ekleme girişimi yeni link/event üretmemeli.

### N-4 — Probe protokol yapısını yeterince doğrulamıyor

**Severity:** MEDIUM · **Threat:** T8  
**Konum:** `src/hlmemo/ops/probe.py:102`.

**Senaryo:** Endpoint HTTP 200 ile `{"choices":[1]}` veya `{"choices":[{}]}` döndürür. Probe `ok=true` üretir ve evaluate kabul eder; normal writer’ın kullanabileceği completion yapısı yoktur.

**Kanıt:**

```python
if not isinstance(data, dict) or not isinstance(data.get("choices"), list) or not data["choices"]:
out["ok"] = True
```

**Minimal düzeltme:** İlk choice/message yapısını ve geçerli protokol alanlarını doğrula; üretilen JSON’u değerlendirmeden gerçek `finish_reason="length"` yanıtını kabul etmeye devam et.

**Regresyon:** Bu iki bozuk fixture `unparseable`; mevcut length fixture’ı PASS olmalı.

## Residual risks for the owner

- **R-1:** Önceden elenen çiftlerin doğrulanmamasını kabul et veya bütün seçilmiş çiftleri elemeden önce kilit altında doğrulat.
- **R-6/N-1:** Çağrı oranını kabul et veya final kararı soru başına, hold-out kapsamındaki fallback oranına bağla.
- **R-10:** Ölçeklenmiş ASGI kanıtını kabul et veya gerçek 170 saniyelik Caddy/uvicorn/MCP ve `/ready` provasını zorunlu tut.
- **R-15:** Tam gönderilecek geçmiş ve özel içerik kontrolleri tamamlanmadan public push riskini kabul et veya push’u bu kanıta bağla.
- **R-16:** Belirsiz ölçüm tanımlarını kabul et veya ilk sorudan önce paydaları, maliyet otoritesini ve p95 yöntemini sabitle.
- **N-2:** Belgeleri düzelt veya `--dry-run` ve `len(links)` kullanımını açık operasyon istisnası olarak kabul et.
- **N-3:** Mevcut grafikten doğan döngüler için küratör kontrolüne güvenmeyi kabul et veya graf kontrolünü tamamla.
- **N-4:** Zayıf protokol probe’unu kabul et veya completion yapısı doğrulamasını ekle.

## Verdict

**GO-WITH-FIXES.**

Açık maddeler: **R-1, R-6/N-1, R-10, R-15, R-16, N-2, N-3, N-4**. R-1’in ilk turdaki yabancı-projeye yazma açığı kapanmış; kalan sorun bütün-dosya reddi sözleşmesidir.

Yeni CRITICAL/HIGH doğrulanmadı. Bu son turda yukarıdaki maddeler düzeltilmeli veya her biri için sahibin açık residual-risk kararı kaydedilmelidir; mevcut kanıt koşulsuz **GO** için yeterli değil.