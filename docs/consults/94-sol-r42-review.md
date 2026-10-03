## Verdict

**NO-GO.** Astra-low ve Sol-xhigh aynı release-blokerini buldu. Bu tur 2/2; üçüncü review önerilmiyor.

1. **HIGH — Bozuk gzip rezervasyonu açık bırakıyor.** [provider.py:1121](/Users/cemalkurt/Projects/HLMemo-wt-fix-writer-unavailable/src/hlmemo/librarian/provider.py:1121)

   Failure/reproducer: gzip etiketli bozuk bir 503 yanıtı `DecodingError` üretti; `reserved=0.00143600`, açık rezervasyon `1`, `spent=0`, ledger satırı `0`. `DecodingError`, yakalanan `TransportError` alt sınıfı değil. MemoryBudget kalıcı sızıyor; DB rezervasyonu TTL sweep’e kadar kalıp sonra worst-case harcanıyor.

   Normal success, fallback, cancellation, timeout ve breaker yolları tam bir kez settle ediyor; sorun decode yolunda. Minimal fix: post-reservation decode/request hatalarını worst-case settlement + ledger + fallback yoluna alın; MemoryBudget ve DB regresyonları ekleyin.

2. **HIGH — D-017 açık: Google-native alanlar ortak kodda başka sağlayıcıyı yanlış settle ediyor.** [provider.py:147](/Users/cemalkurt/Projects/HLMemo-wt-fix-writer-unavailable/src/hlmemo/librarian/provider.py:147)

   Failure/reproducer: generic bir 529 policy tam olarak `candidates=0` ilan ettiğinde eşleşen gövde, ortak `_EVIDENCE_KEYS` içindeki Google-native `candidates` nedeniyle reddediliyor ve worst-case oluyor.

   Minimal fix: evidence alanlarını profile policy’ye taşıyın veya duplicate-key reddinden sonra recursive veto’yu kaldırıp exact key-set doğrulamasına dayanın.

3. **MEDIUM — Malformed policy fail-fast değil.** [profiles.py:187](/Users/cemalkurt/Projects/HLMemo-wt-fix-writer-unavailable/src/hlmemo/librarian/profiles.py:187), [research.py:3174](/Users/cemalkurt/Projects/HLMemo-wt-fix-writer-unavailable/src/hlmemo/librarian/tasks/research.py:3174)

   `unbilled_errors=false`, `0` veya `{}` hata yerine policy-off olur. Diğer writer-profile `LlmConfigError`’ları da loglanıp luna’ya düşer; lazy researcher nedeniyle API startup geçebilir.

   Minimal fix: yalnız `None`/`[]` off olsun; configured writer policy hatasını startup doğrulamasında propagate edin.

4. **LOW — Duplicate JSON anahtarları “exact envelope” garantisini bozuyor.** [provider.py:175](/Users/cemalkurt/Projects/HLMemo-wt-fix-writer-unavailable/src/hlmemo/librarian/provider.py:175)

   Reproducer: `[{"error":{"usage":{"completion_tokens":100}},"error":{"code":503,"message":"m","status":"UNAVAILABLE"}}]` için matcher `True`; `json.loads` ilk `error` ve usage kanıtını siliyor. Google’ın bunu üretip faturaladığına dair kanıt yok. Unique-key gövdelerde HTML/CDN, partial/SSE, ekstra alan ve boş/sıfır/null usage worst-case kalıyor. Google’ın güncel FAQ’sı başarısız 400/500 isteklerini token-faturasız sayıyor: [Gemini billing FAQ](https://ai.google.dev/gemini-api/docs/billing#am-i-charged-for-failed-requests).

   Minimal fix: her derinlikte duplicate key reddi ve settlement testi.

5. **LOW — Env override release provenance dışında.** [config.py:212](/Users/cemalkurt/Projects/HLMemo-wt-fix-writer-unavailable/src/hlmemo/config.py:212), [llm_env_release.py:91](/Users/cemalkurt/Projects/HLMemo-wt-fix-writer-unavailable/deploy/scripts/llm_env_release.py:91)

   `HLM_UNBILLED_ERRORS` primary profile’ı override edebiliyor fakat fingerprint/manifest bunu izlemiyor. Mevcut prod env bu override’ı kullanmıyor; mevcut `llm.env` yüklenebilir, schema/Compose değişikliği yok ve rollback eski image+env’yi geri yüklüyor.

   Minimal fix: env override’ını yasaklayın veya fingerprint ve manifest kapsamına alın.

6. **LOW — Round-1 closure:** #1 **CLOSED**, #2 **PARTIAL**, #3 **OPEN**, #4 **CLOSED**, #5 **CLOSED**. Yeni HIGH/LOW regresyonları eksik.

   Doğrulama: 89 test + 25 subtest ve DB-window entegrasyonu geçti; iki HIGH reproducer tekrarlandı; lint ve `git diff --check` temiz. Canlı prod/rehearsal/rollback çalıştırılmadı. Birleşik rapor: [94-codex-combined-review.md](/Users/cemalkurt/Projects/HLMemo-wt-fix-writer-unavailable/docs/consults/94-codex-combined-review.md).