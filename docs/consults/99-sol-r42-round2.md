## Verdict

**NO-GO.** Astra-low ve Sol-xhigh yeni F1 kabul yolunu bağımsız olarak yeniden üretti. Bu 2/2 son turdur; düzeltme veya risk kabulü owner’a gider.

1. **HIGH — F1 URL’nin path dışındaki kısımlarını değiştiriyor — `src/hlmemo/librarian/tasks/research.py:2081-2104`.**  
   Reproducer A: excerpt ``Use `POST /api/allow|deny?role=user` exactly.``; cevap ``Use `POST /api/deny`.`` → main **dropped**, R4.2 **kept**. Zorunlu query sessizce kayboluyor.  
   Reproducer B: excerpt ``Use `https://a|b/path`.``; cevap ``Use `https://b/path`.`` → main **dropped**, R4.2 **kept**. Authority/host path segmenti gibi genişliyor.  
   Aynı kod nested/unbalanced bracket içeren tokenlarda alternation’ı da genişletiyor.  
   **Minimal fix:** scheme+authority değişmez, query/fragment byte-exact kalmalı; yalnız gerçek path genişletilmeli. Herhangi bir bracket varsa `_brackets_flat(token)` false iken bütün expansion reddedilmeli. Üç sınıf için end-to-end test eklenmeli.

2. **MEDIUM — dangling heuristic bare-pronoun / demonstrative+noun ayrımını tam yapmıyor — `research.py:2546-2604,2717-2732`.**  
   Kaynak “The indicated can contains nothing” iken düşen hatalı ana cümleyi izleyen bağımsız `This can is empty.` de düşüyor; `can`, verb listesinde. Ters yönde `O tüm yedekleri siler.` ve `Es löscht alle Backups.` düşen antecedent’ten sonra kalıyor. `O …`, `29f132a`ya göre regresyon olsa da main/prod ile aynı permissive davranış.  
   **Minimal fix:** noun/verb olarak belirsiz kelimeleri ayrı ele alıp EN/TR/DE bare-pronoun ve demonstrative+noun karşıörneklerini tablo testi yapmak. Kalan dilsel risk owner tarafından açıkça kabul edilebilir.

3. **INFO — round-1 closure.**

   - Astra #1 / Sol #1 URL: **PARTIAL** (`research.py:2081-2104`), bulgu 1 açık.
   - Astra #2 / Sol #1 bracket/id: **PARTIAL** (`research.py:2084-2104`); `arr[index]` ve eski nested örnekler kapalı, mixed alternation+bad-bracket açık.
   - Astra #3 / Sol #2 restate: **CLOSED by removal** (`research.py:115,129,3134`; `config.py:300`).
   - Astra #4 / Sol #6 exact `This API`: **CLOSED** (`research.py:2576-2619,2717`); genel sınıf bulgu 2.
   - Astra #5 / Sol #7 preview: **CLOSED** (`research.py:883-930`).
   - Astra #6 / Sol #5 citation: **CLOSED** (`research_service.py:356-409`); yalnız `.0→.1`, cited handle `.1`.
   - Sol #3 manifest: **CLOSED/obsolete**.
   - Sol #4 merge-before-cap: **CLOSED** (`research_service.py:389-401`).
   - Sol #8 prompt/schema audit: **CLOSED**; dört dosya main ile byte-identical.

4. **INFO — change-set B merge’de aynen korunmuş.**  
   `git diff a8ec1b7..44fde29` ilgili provider/profile/error, Google profil ve B test dosyalarında boş. Exact envelope, duplicate-key reject, file-only policy, settle-once ve startup validation mevcut (`provider.py:171,1180-1205`; `profiles.py:190-225`; `research.py:3481-3494`). B unit testleri geçti; integration dosyası izole **10/10** geçti.

5. **INFO — prod `fda8fa0` üzerine mekanik release/rollback uyumlu.**  
   Commit ancestor; prod→R4.2’de Alembic, Compose veya deploy-script farkı yok. Yalnız iki image-baked Google profili policy kazanıyor (`Dockerfile:75`). `unbilled_errors` file-only/default-empty, MAIN_REPAIR kaldırılmış; mevcut prod `llm.env` yeni key istemiyor. Rollback önceki image ID ve env snapshot’ını servis başlamadan geri getiriyor (`rollback.sh:129-164,288-307`). Rehearsal/prod smoke çalıştırılmadı.

6. **INFO — main’deki bilinen literal zayıflıkları R4.2’yi tek başına bloklamamalı.**  
   Hyphen-part, nested token içindeki `foo` ve backtick dışı digit-less URL davranışları değişmedi; ayrı takip/owner kabulüdür. Bulgu 1 main’de reddedildiği için bu istisnaya girmez.

7. **Validation/limit.**  
   334 hedefli test + 25 subtest ve 2 dangling integration testi geçti; `git diff --check` ve dört prompt SHA karşılaştırması geçti. Bir birleşik B koşusunda breaker testi 38/50 ledger satırı gördü; aynı test ve tüm integration dosyası izolasyonda geçti. Docker-compose rehearsal, live provider ve prod smoke yapılmadı.

Kayıtlar: [birleşik rapor](/Users/cemalkurt/Projects/HLMemo-wt-r42/docs/consults/99-r42-round2-review.md), [Astra](/Users/cemalkurt/Projects/HLMemo-wt-r42/docs/consults/99-astra-r42-review.md), [Sol](/Users/cemalkurt/Projects/HLMemo-wt-r42/docs/consults/99-sol-r42-review.md).