## Checklist

`P` = [R4-RELEASE-PLAN.md](/Users/cemalkurt/Projects/HLMemo/docs/decisions/R4-RELEASE-PLAN.md), `W` = `.claude/worktrees/agent-a2359ac13ecf7557f`, `B` = `.claude/worktrees/agent-a038bcc4743022b02`. Aşağıdaki yollar bu köklere göredir. İnceleme salt okunurdu; özel dosyalar/anahtarlar okunmadı, VPS veya ücretli API çağrısı yapılmadı. Fingerprint karşılaştırması ve bütçe aritmetiği çalıştırıldı; entegrasyon kontrolleri çalıştırılmadı.

**Q1 — PARTIAL.** `usage.cost` önceliği doğru; ledger ve soru toplamını aynı normalize edilmiş kullanımdan üretmek de doğru. Ancak reasoning alanının “dahil/ekstra/bilinmiyor” anlamı ve `total_tokens` güvenilirliği doğrulanmıyor; mevcut öneri çelişkili veya eksik metadata’yı eksik ücretlendirebilir (**R-8**). Saat/gün/ay sınırları doğrudan `output_tokens` değil, rezervasyonun USD settlement sonucunu kullanır: `W/src/hlmemo/librarian/provider.py:978`, `budget.py:145`; `P:44`.

**Q2 — PARTIAL.** Her çağrıda örnek 10.000 input için Gemini `11.000×0,75/M +16.000×3,75/M=$0,06825`; luna fallback `$0,01420`; planner/rerank/attribution toplam rezervasyonu `$0,009225`. `2G+L+A=$0,159925>$0,12`, fakat `G+L+A=$0,091675`; rutin kesilme kanıtlanmış değil, pahalı tekrar yolu kesilir (**R-9**).
Dört sorunun doğru muhasebe altında toplam kabul edilebilir harcama/rezervasyonu en fazla `4×$0,12=$0,48<$3`; sıfırdan yalnız bu dört soru HOUR sınırını doldurmaz. Önceki harcama ve async librarian ayrıca hesaba katılır; seri denemelerin rezervasyonları eşzamanlı tutulmaz: `W/src/hlmemo/core/research_service.py:1234`, `librarian/budget.py:88`, `librarian/prompts/__init__.py:53`.

**Q3 — PARTIAL.** Temp anahtar dosyası: private dizin+0600+silme şartıyla güvenli; kurulu `llm.env`, `.bak-*`, `.tmp-*`, `llm.env.release-*`: secret içerir, mevcut oluşturma yolu 0600’dür; rendered rollback Compose kopyaları da secret içerir. Journal metadata/yol tutar, installer çıktısı değerleri maskeler: `W/deploy/scripts/install_llm_env.sh:85,149,156`; `llm_env_release.py:46`; `rollback.sh:53`.
Container env ve ham `docker inspect` anahtarı içerir; bunlar host/Docker yöneticisi güven sınırındadır ve paylaşılmamalıdır. Mevcut provider logları/checker hata çıktıları key/body basmaz; installer SSH stdin ve builtin `printf` kullanarak shell history/`ps` argümanlarına anahtar koymaz: `provider.py:896`; `check_librarian.py:223`; `install_llm_env.sh:232`.
Trace’in kapalı olması garanti değil (**R-11**); yeni authenticated probe’un etkin API anahtarıyla çalışması ve hata çıktısının secret taşımaması ayrıca doğrulanmalı (**R-14**). Mevcut fingerprint pipeline ham inspect’i filtreleyerek tüketir: `W/deploy/scripts/remote-deploy.sh:490`.

**Q4 — PARTIAL.** B5, template satırı kaldığı sürece kurulu Gemini anahtarını korur; ancak her installer çalıştırmasında yerel key-file hâlâ zorunludur: `W/deploy/scripts/install_llm_env.sh:128,270`. Mevcut probe yalnız librarian collect yolunda çalışıyor; API’nin gerçekten kullandığı yanlış anahtarı kaçırmamak için yeni probe API tarafına bağlanmalı (**R-14**): `:214–218`.
Tek sentetik 20-input/16-output çağrı, plan tarifesiyle yaklaşık `$0,000075` eder; anlamlı harcama değildir. Fallback/retry olmaması, timeout ve parse edilecek yanıt katmanı açıkça belirlenmelidir.

**Q5 — PARTIAL.** Tek async uvicorn süreci var; dört ask worker’ı 170 saniye bloklamaz ve LLM beklerken request DB bağlantısı bırakılır; beşinci ask `busy` olur. Ancak sorgu aşamasında `4×3=12` bağlantı talebi, 8 bağlantılık ortak havuzda query/write için bekleme yaratabilir (**R-10**): `W/src/hlmemo/server/app.py:517`, `server/middleware.py:442`, `core/research_service.py:529`, `config.py:366`.
Caddy’de response timeout yok; `flush_interval -1` henüz üretilmemiş cevabı/heartbeat’i üretmez. Gerçek MCP istemcisinin 170 saniyeyi tamamlaması doğrulanmamış; plan yalnız 60–120 saniye test ediyor: `W/deploy/Caddyfile:32`; `P:98`.

**Q6 — NO.** Google 5xx/timeout/breaker durumlarında luna’ya geçilebilir; response başına `writer_used` operatör görünürlüğü için yeterli değil (**R-6**). İki şema hatası ise fallback değil, `schema_fail` üretir; B2 yanlış (**R-9**): `W/src/hlmemo/librarian/provider.py:583,615,737`; `core/research_service.py:1399`.

**Q7 — PARTIAL.** Head kontrolü kilit altında yapılır; CLI exception’da transaction’ı tamamen geri alır, başarıda bir event ile commit eder; replay kayıtlı mutations’ı kullanır. Stale satırlar hata vermek yerine atlanabilir; ayrıca proje üyeliği kontrolü eksik, port uyumsuz ve revert komutu eksik (**R-1/R-7/R-12**): `B/src/hlmemo/ops/backfill_links.py:583`; `B/src/hlmemo/cli/links.py:193`; `W/src/hlmemo/db/replay.py:315`.
`--revert` tek apply event’ini değil, projenin bütün canlı `by=backfill` linklerini kapatır. Full rollback linkleri **ve event’lerini** dump ile siler; sonraki redeploy bunları otomatik getirmez, yeniden doğrulanmış apply gerekir: `B/.../backfill_links.py:642`; `P:146`.

**Q8 — NO.** Yeni writer/mode/timeout/prose alanları fingerprint tarafından reddedilmiyor, sessizce yok sayılıyor (**R-4**). D-123, review-77 risk kabulünü yalnız R3’e sınırlamış ve hardening’i R4 önkoşulu yapmış; somut rollback kusuru hâlâ mevcut (**R-2**): `W/deploy/scripts/llm_env_release.py:88`; `docs/decisions/DECISIONS.md:325`.

**Q9 — PARTIAL.** R4 kodu+R3 env research’ü kapatır; 0009 cache tablosunun kalması bu davranış dönüşüne engel değildir. Ancak R3 reinstall’ın template/cap seçimi ve link revert komutu yürütülebilir biçimde tamamlanmamış (**R-3/R-7**): `W/deploy/scripts/check_librarian.py:100`; `P:144`.
Tek snapshot slotuna yalnız deploy öncesi snapshot almak tutarlıdır; test sonrası ikinci snapshot almak bu geri dönüş noktasını ezer. Script rollback’in güvenilirliği snapshot’ın varlığıyla kanıtlanmış olmaz (**R-2**): `P:113,147`.

**Q10 — PARTIAL.** Tracer varsayılan olarak kapalı, üretimde zorunlu olarak kapalı değil (**R-11**). İncelenen ask yollarında yetkisiz başka-proje içeriği için açık bulmadım: view, rerank teklifleri, xref/temporal pull-in ve final kontrol proje/grant/isolation sınırını koruyor; ortak-proje öğeleri tüm izinler sağlandığında meşrudur: `W/src/hlmemo/core/memory_map.py:536`; `research_service.py:518,831,1855`.

**Q11 — PARTIAL.** Push öncesi tam outgoing commit/blob kümesi, etkin gitleaks kuralları/allowlist’leri, geçmiş dahil tracked private/key yolları, büyük bloblar ve DECISIONS/consults/RUNBOOK değişikliklerindeki key-like veya özel metin doğrulanmalı (**R-15**). `.gitignore` mevcut tracked geçmişi gizlemez: `P:99`.
İzin verilen okuma listesi `.gitignore`, gitleaks config ve consult içeriklerini kapsamıyor; bunların temiz olduğunu doğruladığımı iddia etmiyorum.

**Q12 — NO.** Eksikler: bağımlılıklarıyla backfill portu, gerçekten yerel installer rotası, dört eşzamanlı/170 saniyelik gate, API writer probe’u, tracer-OFF doğrulaması, public-push nesne kontrolü ve final kalite sonucuna bağlı karar (**R-10–R-16**). Konumlar: `P:86,95,98–99,118,128–147`; ayrıca D-123 hardening önkoşulu eksik (**R-2**).

## Findings

| id | severity | threat | location | one-line summary |
|---|---|---|---|---|
| R-1 | HIGH | T3 | §3.1/§4.6; B `backfill_links.py:587` | Backfill başka projenin geçerli kimliklerini kabul ediyor; proje revert’i bunları kaçırabilir. |
| R-2 | HIGH | T6 | §4/§6.3; W `rollback.sh:230` | Rotasyon rollback safety dump’ını silebilir; recovery yanlışlıkla journal’ı kapatabilir. |
| R-3 | HIGH | T6 | §6.2; W `install_llm_env.sh:128` | Korunan R4 cap’leri R3 manifest’ini bozarak env-switch/rollback’i kilitleyebilir. |
| R-4 | HIGH | T6 | §1.2/§1.5; W `llm_env_release.py:88` | Yeni davranış ayarları provenance fingerprint’inde yok. |
| R-5 | HIGH | T1 | §1.3; P:59 | Planın belirttiği fiyat artışı yalnız yorumda; sonraki harcama yarım kaydedilir. |
| R-6 | HIGH | T8 | §4.5/§5; W `research_service.py:1399` | Luna fallback’i operatöre sürekli görünür değil. |
| R-7 | HIGH | T3 | §6.1; P:144 | Yazılı link rollback komutunda zorunlu proje eksik. |
| R-8 | MEDIUM | T1 | §1.1; P:46 | Thinking normalizasyonu provider sözleşmesini ve eksik metadata’yı kapsamıyor. |
| R-9 | MEDIUM | T1 | B2/§1.5; P:32,82 | Bütçe pahalı şema tekrarını karşılamıyor; şema hatası fallback iddiası yanlış. |
| R-10 | MEDIUM | T4 | §2.2; P:98 | Ortak DB havuzu ve 170 saniyelik istemci davranışı yük altında doğrulanmıyor. |
| R-11 | MEDIUM | T5 | §2.1/§4.4 | Etkin tracer ayarının OFF olması release gate değil. |
| R-12 | MEDIUM | T6 | §1.6; P:86 | Belirtilen iki dosyalık backfill portu W üzerinde çalışmıyor. |
| R-13 | MEDIUM | T6 | §2.2; P:95 | Yerel smoke komutu zorunlu SSH state/target tanımlamıyor. |
| R-14 | MEDIUM | T2 | §1.4; P:64 | Probe’un API’nin etkin credential’ını doğrulaması ve güvenli hata sözleşmesi eksik. |
| R-15 | MEDIUM | T9 | §2.3; P:99 | Public-push kontrolü Git geçmişi ve özel fakat secret olmayan içerik için yetersiz. |
| R-16 | MEDIUM | T8 | §5/§6/§9 | Final kalite ölçümleri için release’i açık tutma/kapatma kararı tanımlanmıyor. |
| R-17 | LOW | T6 | §1.2; P:43,50 | Yeni varsayılanın expand’a uygulanması mevcut 1500-token davranışını değiştirir. |

### R-1

**(a) id:** R-1 · **(b) severity:** HIGH · **(c) threat:** T3  
**(d) location:** §3.1/§4.6; B `src/hlmemo/ops/backfill_links.py:587–620`; W `src/hlmemo/librarian/actor.py:200–211,300–306`.

**(e) Senaryo:** Curated JSON, `project=hlmemo` diyerek başka projenin geçerli PROD logical/version kimliklerini içerir. Head kontrolü geçer; link kaynak öğenin proje üyeliğiyle yazılır, event hlmemo altında kalır ve hlmemo-scoped revert linki bulamayabilir.

**(f) Kanıt:**
```python
records_, _ = await materialize(conn, None, None, actions)
"project_ids": list(src.project_ids),
```

**(g) Minimal düzeltme:** Export’u proje filtreli yap; apply sırasında endpoint kilitleri altında iki head’in de istenen `pid` içinde olduğunu doğrula. Uyumsuzlukta tüm transaction’ı reddet.

**(h) Tekrarlama:** Yerel A/B projelerinde B’ye ait iki canlı head ile A etiketli proposal gönder. Mevcut seçim/materialization yolu bunu kabul eder; düzeltilmiş yol sıfır event/sıfır link ile reddetmelidir. Port uyumsuzlukları giderildikten sonra A-scoped revert’in yabancı linki kaçırdığını da göster.

### R-2

**(a) id:** R-2 · **(b) severity:** HIGH · **(c) threat:** T6  
**(d) location:** §4/§6.3; W `deploy/scripts/rollback.sh:230–243,256–261`; `deploy/backup/retention.py:39–42`; D-123, `DECISIONS.md:325–331`.

**(e) Senaryo:** Destructive rollback kesilir; sonraki günlük backup aynı günün önceki safety dump’ını siler. Retry sırasında recovery eksik dump nedeniyle DB restore’unu atlayıp servisler açılırsa rollback journal’ını temizleyebilir; beklenen mevcut DB geri dönmemiştir.

**(f) Kanıt:**
```bash
if ((db_replaced)) && [[ -f $safety ]]; then
python3 "$helpers/release_state.py" end-rollback "$parent_dir" >&2
```

**(g) Minimal düzeltme:** Aktif rollback dump’ını rotasyondan koru; backup/restore/recovery işlemlerini ortak kilitle koordine et; destructive işlem sonrası safety eksikse journal’ı temizlemeden fail-closed dur. D-123’ün R4 hardening koşulunu plana geri ekle.

**(h) Tekrarlama:** Yerel fault-injection testinde `rollback_destructive=true` journal oluştur, kayıtlı safety dump’ını rotasyonla kaldır, başarılı servis başlangıcıyla retry yap. Önceki davranışın restore olmadan `end-rollback` çağırdığını; düzeltmenin bunu reddettiğini doğrula.

### R-3

**(a) id:** R-3 · **(b) severity:** HIGH · **(c) threat:** T6  
**(d) location:** §6.2; W `deploy/scripts/install_llm_env.sh:128–130,204–219`; `check_librarian.py:91–106`; `rollback.sh:71–72`.

**(e) Senaryo:** R4, onaylanan MONTH=60 ile çalışır; davranış rollback’i R3 template’ini varsayılan preserve davranışıyla kurar. MONTH=60 korunur fakat mevcut R3 manifest’i en fazla 10 kabul eder; evaluation başarısız kalır, `env_switch` kapanmaz ve full rollback de reddedilir.

**(f) Kanıt:**
```python
line = f"{key}={installed[key]}"
"month_max_usd": 10.0,
```

**(g) Minimal düzeltme:** Tam R3 template/checkout ve cap geri yükleme komutunu belirt; anahtarları korurken R3’e uygun cap’leri açıkça kur. R4 cap değişikliğini ortak `_BUDGETS` üzerinden sessizce R3’e de yayma.

**(h) Tekrarlama:** Yerel R4 env MONTH=60 üzerinde preserve ile R3 reinstall yap; evaluation hatasını, kalan journal’ı ve rollback reddini göster. Düzeltilmiş prosedür R3 evaluation PASS ve boş journal ile bitmelidir.

### R-4

**(a) id:** R-4 · **(b) severity:** HIGH · **(c) threat:** T6  
**(d) location:** §1.2/§1.5/§6; W `deploy/scripts/llm_env_release.py:88–113,166–168`.

**(e) Senaryo:** Diskte Gemini/150 saniye, çalışan API’de luna/20 saniye bulunur; eski fingerprint alanları aynıdır. Provenance farkı görmez ve çalışmamış bir konfigürasyonu rollback snapshot’ı olarak kabul eder.

**(f) Kanıt:**
```python
return key in FINGERPRINT_KEYS or key.startswith(TASK_FALLBACK_PREFIX)
```

**(g) Minimal düzeltme:** Yeni writer, mode, timeout, prose-limit ve guard/cap alanlarını fingerprint’e dahil et; iki servisin de raporlanmasını zorunlu kıl.

**(h) Tekrarlama:** Çalıştırdığım bellek içi kontrolde writer, HTTP timeout ve budget-disabled değerleri farklı iki env için fingerprint eşit çıktı; `mismatches=[]` döndü. Aynı kontrol düzeltmeden sonra alan farklarını vermelidir.

### R-5

**(a) id:** R-5 · **(b) severity:** HIGH · **(c) threat:** T1  
**(d) location:** §1.3, P:59; W `src/hlmemo/librarian/profiles.py:64–77`; `provider.py:978–985`.

**(e) Senaryo:** R4, planın belirttiği 2027-01-01 tarife artışından sonra aynı profil ile çalışmaya devam eder. Direct Google yanıtında `usage.cost` yoksa hem rezervasyon hem settlement eski fiyatlardan hesaplanır; `$3` kayıtlı Gemini harcaması gerçekte yaklaşık `$6` olur.

**(f) Kanıt:**
> prices 0.75/3.75, with a comment noting 1.50/7.50 from 2027-01-01

**(g) Minimal düzeltme:** Profil fiyatını tarihli bir güncelleme/expiry gate ile bağla veya bu test release’inin tarife değişmeden önce kesin kapanışını tanımla.

**(h) Tekrarlama:** Aynı usage fixture’ını eski profil ve plandaki yeni tarifeyle hesapla; 2× farkı ve eski rezervasyonun yeni gerçek maliyeti karşılamadığını doğrula. 10k input/16k output rezervasyonu `$0,06825 → $0,13650` olur.

### R-6

**(a) id:** R-6 · **(b) severity:** HIGH · **(c) threat:** T8  
**(d) location:** §4.5/§5; W `src/hlmemo/librarian/provider.py:583–619`; `core/research_service.py:1399`; `ops/service.py:516–556`.

**(e) Senaryo:** Smoke sonrasında Google 5xx vermeye başlar veya breaker açılır. Sorular luna üzerinden başarıyla yanıtlanır; her cevabın flag’ini okumayan operatör sağlıklı görünen servisin yazar değiştirdiğini fark etmez.

**(f) Kanıt:**
```python
self.flags["writer_used"] = self.last_profile
```

**(g) Minimal düzeltme:** API tarafında gerçek writer/fallback için yapılandırılmış sayaç üret; yakın dönem count/rate’i ops status’ta göster ve beklenmeyen writer kullanımını bildir.

**(h) Tekrarlama:** Gemini’yi 5xx, luna’yı başarılı yanıtlayacak şekilde mock et. Hem cevap flag’inin hem operatör-visible sayaç/status’un fallback’i gösterdiğini iste; mevcut status ikinci koşulu karşılamıyor.

### R-7

**(a) id:** R-7 · **(b) severity:** HIGH · **(c) threat:** T3  
**(d) location:** §6.1, P:144; B `src/hlmemo/cli/links.py:324–331`.

**(e) Senaryo:** Container’da varsayılan proje tanımlı değildir ve link rollback’i gerekir. Plandaki `hlm links backfill --revert` komutu exit 64 ile biter; linkler canlı kalır.

**(f) Kanıt:**
```python
if not project or len(modes) != 1:
```

**(g) Minimal düzeltme:** Tam komutu yaz ve doğrula: `stack.sh exec -T api hlm links backfill --project hlmemo --revert`. Bunun event-id bazlı değil, bütün canlı backfill linkleri üzerinde çalıştığını belirt.

**(h) Tekrarlama:** Bir link uygula, project varsayılanı olmadan plan komutunu çalıştır; exit 64 ve canlı linki doğrula. Tam komut bir revert event’i üretip linki kapatmalıdır.

### R-8

**(a) id:** R-8 · **(b) severity:** MEDIUM · **(c) threat:** T1  
**(d) location:** §1.1, P:46–48; W `src/hlmemo/librarian/provider.py:978–1014`.

**(e) Senaryo:** Normalizasyon fixture’ında `prompt=1000`, `completion=100`, `reasoning_tokens=0`, `total=3100` gelir. Önerilen “reasoning mevcutsa, aksi halde total” dalı 100 output kaydeder ve total’ın gösterdiği farkı yok sayar; izin verilen belgeler Google’ın bu alanlar için güvenilir sözleşmesini kanıtlamıyor.

**(f) Kanıt:**
> Otherwise book output = `completion_tokens` + `completion_tokens_details.reasoning_tokens` if present. Else …

**(g) Minimal düzeltme:** Provider/profile bazında reasoning’in completion’a dahil olup olmadığını doğrulayan fixture’lar ekle; absent/zero/çelişkili metadata’yı kapsa. Güvenilir toplam bulunamadığında partial usage’ı kesin maliyet sayma, worst-case settlement kullan; `usage.cost` USD otoritesi olarak kalsın ve ledger token normalizasyonu ayrıca tanımlansın.

### R-9

**(a) id:** R-9 · **(b) severity:** MEDIUM · **(c) threat:** T1  
**(d) location:** B2/§1.5, P:32,82; W `src/hlmemo/core/research_service.py:1234–1245`; `librarian/provider.py:615–617,737–741`.

**(e) Senaryo:** 10k-input Gemini denemesi 16k output tüketip geçersiz JSON döndürür. İlk gerçek maliyet `$0,06750`, sonraki rezervasyon `$0,06825` olduğundan yalnız bu ikisi `$0,13575` eder ve tekrar budget-stop olur; bütçe yeterli olsa bile ikinci şema hatası luna’ya geçmez.

**(f) Kanıt:**
```python
except SchemaFail:
    raise
```

**(g) Minimal düzeltme:** Retry/refine/fallback yolları için açık bütçe politikası seç; `$0,12` altında hangi yolların kesileceğini test et. B2’yi düzelt ve truncation/schema failure’ı gerçek hata/rollback metriğine dahil et; “rutin kesilir” sonucunu ölçüm olmadan yazma.

### R-10

**(a) id:** R-10 · **(b) severity:** MEDIUM · **(c) threat:** T4  
**(d) location:** §2.2, P:98; W `src/hlmemo/core/research_service.py:175,529–538`; `config.py:366–367`.

**(e) Senaryo:** Dört ask aynı anda üçlü query aşamasına girer; 12 bağlantı talebi sekiz bağlantılık havuzda normal query/write’ı kuyruğa koyar. Ayrıca 120 saniyelik smoke’u geçen bir MCP istemcisi, 170 saniyelik gerçek çağrıyı yine kesebilir.

**(f) Kanıt:**
```python
PARALLEL_QUERIES = 3
pool_max_size: int = 8
```

**(g) Minimal düzeltme:** Dört eşzamanlı, 170 saniyeye yakın ask sırasında query/write/readiness gecikmesini ve gerçek MCP istemcisini test et. Havuz bekleme/503 görülürse ask DB paralelliğini azalt veya diğer araçlar için kapasite ayır.

### R-11

**(a) id:** R-11 · **(b) severity:** MEDIUM · **(c) threat:** T5  
**(d) location:** §2.1/§4.4; W `src/hlmemo/config.py:299–302`; `core/research_trace.py:92–94,150–159`; `deploy/compose.prod.yaml:94–100`.

**(e) Senaryo:** Mevcut app/api env içinde `HLM_RESEARCH_TRACE_DIR=/tmp/traces` kalmıştır. R4 llm.env bunu temizlemez ve evaluation bu ayarı kontrol etmez; üretim soruları, excerpt’ler ve model çıktıları trace dosyalarına yazılır.

**(f) Kanıt:**
```python
return cls(Path(directory), question) if directory else None
```

**(g) Minimal düzeltme:** R4 evaluation etkin API `research_trace_dir` değerinin boş olmasını zorunlu kılsın. Yerel testte stale env değeriyle gate’in FAIL verdiğini doğrula.

### R-12

**(a) id:** R-12 · **(b) severity:** MEDIUM · **(c) threat:** T6  
**(d) location:** §1.6, P:86; B `src/hlmemo/ops/backfill_links.py:53,73,637,646`; W `src/hlmemo/ops/explicit_links.py:109,182`.

**(e) Senaryo:** Yalnız belirtilen CLI ve ops dosyaları W’ye taşınır. W’de olmayan proposer modüllerinin import’u başarısız olur; bunlar kaldırıldığında da `_record(client=...)` ve `explicit_links(by=...)` çağrıları mevcut W imzalarıyla uyuşmaz.

**(f) Kanıt:**
```python
from hlmemo.core import supersede_candidates as sc
links = await xl.explicit_links(conn, pid, by=BY)
```

**(g) Minimal düzeltme:** Apply/revert’in gerekli bağımlılık ve yardımcı API değişikliklerini port kapsamına ekle; dışarıda kalan proposer’dan import bağımlılığını kaldır. Yerel R4 image üzerinde apply → replay → revert gate’i ekle; stale/dropped/applied sayıları da beklenen setle eşleşsin.

### R-13

**(a) id:** R-13 · **(b) severity:** MEDIUM · **(c) threat:** T6  
**(d) location:** §2.2, P:95; W `deploy/scripts/install_llm_env.sh:54,59–63,223`.

**(e) Senaryo:** Yerel Compose smoke’u plandaki yalnız `--key-file` komutuyla başlatılır. Installer zorunlu `--state` olmadığı için durur; mevcut production state’i eklemek ise “no prod” aşamasını uzak production kurulumuna dönüştürür.

**(f) Kanıt:**
```bash
rssh() { ssh -F "$ssh_config" -o BatchMode=yes hlm-deploy "$@"; }
```

**(g) Minimal düzeltme:** İzole yerel SSH hedefi/state ve yolları tanımla veya yerel render/install yolu ekle. Smoke başlamadan hedefin yerel olduğunu doğrulat.

### R-14

**(a) id:** R-14 · **(b) severity:** MEDIUM · **(c) threat:** T2  
**(d) location:** §1.4, P:64; W `deploy/scripts/install_llm_env.sh:214–218`; `check_librarian.py:201–213,245–259`.

**(e) Senaryo:** Yeni authenticated probe mevcut `--probe` yoluna eklenir; librarian doğru Gemini anahtarını, API ise farklı ve geçersiz fakat boş olmayan anahtarı taşır. Librarian probe’u ve iki `key_set` değeri geçerken gerçek writer kimlik doğrulaması başarısız olur.

**(f) Kanıt:**
```bash
bash deploy/scripts/stack.sh exec -T api python - collect --service api < deploy/scripts/check_librarian.py > "$reports/a.json" || true
```

**(g) Minimal düzeltme:** Tek writer probe’unu API container’ının etkin writer profili/credential’ıyla çalıştır; evaluator bu sonucu zorunlu kılsın. Sentetik prompt, tek deneme, fallback yok, kısa timeout, yalnız status/hata türü çıktısı kullan; sentinel anahtarın stdout/stderr’de bulunmadığını test et. “Parseable”ın protokol yanıtını mı, model JSON’unu mu ifade ettiğini belirle; 16-token truncation’ı yanlış credential ile karıştırma.

### R-15

**(a) id:** R-15 · **(b) severity:** MEDIUM · **(c) threat:** T9  
**(d) location:** §2.3, P:99.

**(e) Senaryo:** Gönderilecek eski bir commit özel fakat anahtar içermeyen bir bellek dosyası taşır; dosya sonraki commit’te silinmiş veya artık gitignored’dır. Secret scan temiz çıkabilir fakat public push eski blob’u da yayımlar.

**(f) Kanıt:**
> Secret scan (gitleaks) over main + r4-rc. Then push main and r4-rc to origin …

**(g) Minimal düzeltme:** Tam outgoing geçmiş üzerinde etkin/default gitleaks kurallarını ve allowlist’leri doğrula; tracked private/key yollarını geçmiş dahil reddet. Büyük blob envanteri çıkar; DECISIONS/consults/RUNBOOK değişikliklerini key-like ve özel bellek içeriği açısından incele. Bu inceleme kapsamında mevcut bir sızıntı saptandığı iddia edilmiyor.

### R-16

**(a) id:** R-16 · **(b) severity:** MEDIUM · **(c) threat:** T8  
**(d) location:** §5/§6/§9, P:128–141,175; `docs/decisions/DECISIONS.md:406–410`.

**(e) Senaryo:** Final test teknik olarak başarılıdır fakat correctness veya abstention kabul eşiğinin altında kalır. §5 yalnız raporlama ister; §6 kaliteye bağlı kapatma tetikleyicisi içermez, oysa §9 yalnız maliyet ve latency eşiklerini waive eder.

**(f) Kanıt:**
> D-130 gate items knowingly waived for this test: p95 ≤ 20 s and ≤ $0.01/q.

**(g) Minimal düzeltme:** Test başlamadan, kalan D-130 kalite ölçütlerinin nasıl ölçüleceğini ve başarısızlıkta research’ün kapatılacağını açıkça yaz. Production test izni ile Production Ready sertifikasını ayrı kararlar olarak tanımla.

### R-17

**(a) id:** R-17 · **(b) severity:** LOW · **(c) threat:** T6  
**(d) location:** §1.2, P:43,50; W `src/hlmemo/librarian/tasks/research.py:159–161`.

**(e) Senaryo:** Mevcut kullanıcı `research_expand=true` kullanırken yeni setting’i varsayılanda bırakır. Default 3000’in expand’a da uygulanması mevcut 1500 sınırını ikiye katlar; “yeni ayarlar default iken davranış değişmez” koşulu bozulur.

**(f) Kanıt:**
```python
"prose": 3000,
"expand": 1500,
```

**(g) Minimal düzeltme:** Expand’ın varsayılanını 1500 olarak koru veya bu davranış değişikliğini açıkça kapsamlandırıp test et.

## Verdict

**GO-WITH-FIXES.** Deploy öncesinde **R-1–R-16** kapatılmalı; özellikle **R-1–R-7 HIGH** bulguları release blocker’dır. **R-17** varsayılan davranış sözleşmesinin düzeltilmesini gerektirir.

Doğrulanmış bir CRITICAL bulgu yok; saptanan engeller planın kapsamında giderilebilir.