# Jev (TypeSafe AI) — Dosya

> Derleme: 2026-09-28. Yalnızca iki rapordan derlendi: `01-primary-sources.md` (üretici ve platform dokümanları) ve
> `02-independent-evidence.md` (üçüncü taraf kanıtlar). Yeni olgu eklenmedi, web araştırması yapılmadı.
> Bu dosya yorum veya tavsiye içermez; değerlendirme ayrıca eklenecek.

**Etiketler** (kaynak raporlardaki gibi korundu)
- 01'den: `DOC-FACT` = dokümandaki teknik özellik (API şekli, limit, ID, fiyat) · `VENDOR-CLAIM` = pazarlama veya öz-beyan
  (üreticinin iddiasını tekrarlayan Vercel/OpenRouter/Cloudflare metinleri dahil) · `DERIVED` = 01 yazarının hesabı veya grafik okuması.
- 02'den: `INDEPENDENT` = üçüncü tarafın ölçümü veya ifadesi · `VENDOR-ECHO` = üçüncü tarafın TypeSafe iddiasını tekrarlaması ·
  `INFERENCE` = 02 yazarının çıkarımı.

**Referanslar.** `[S#]` = 02'nin numaralı kaynak listesi; `[S31 #id]` = HN yorum id'si. 01'in sık geçen URL'leri için kısaltmalar:
- [blog] https://typesafe.ai/blog/introducing-system-one-models-and-jev (FAQ render edildi) · [home] https://typesafe.ai (FAQ render edildi)
- [team] https://typesafe.ai/team · [PR] Business Wire bülteni, 2026-09-15: https://finance.yahoo.com/technology/ai/articles/typesafe-ai-emerges-stealth-40m-190000776.html
- [intro] https://docs.typesafe.ai/introduction · [primer] https://docs.typesafe.ai/introduction/machine-learning-primer
- [sysone] https://docs.typesafe.ai/concepts/system-one · [models] https://docs.typesafe.ai/models · [api] https://docs.typesafe.ai/api
- [jag] https://docs.typesafe.ai/model-jaggedness/jev-1.13 (son gözden geçirme 2026-09-17) · [evals] https://evals.typesafe.ai/
- [v-what] https://vercel.com/i/what-is-jev · [v-eval] https://vercel.com/docs/ai-gateway/modalities/evaluation
- [v-gw] https://ai-gateway.vercel.sh/v1/models (ve `/v1/models/typesafe-ai/jev/endpoints`) · [aisdk] https://ai-sdk.dev/docs/ai-sdk-core/evaluation
- [or-doc] https://openrouter.ai/docs/guides/community/jev.md · [cf] https://developers.cloudflare.com/ai/models/typesafe/jev/

---

## 1. Özet

Jev, TypeSafe AI, Inc.'in 2026-09-15'te duyurduğu ilk "System One" modelidir. Metin üretmez: bir `state` (metin, JSON nesnesi
veya dizi) ile tipli sorular (Choice / Score / Noul) alır; her soruya önceden tanımlı şekle uyan bir cevap, bir olasılık dağılımı
ve (Choice/Score için) bir `confidence` döndürür (`DOC-FACT` [intro], [sysone]). Üretici "frontier-level intelligence" ile
"up to 100 times" [PR] ile "193.6x Faster, 444.6x Cheaper" [home] arasında değişen üstünlük iddia eder (`VENDOR-CLAIM`).
Bağımsız ölçümler düşük maliyeti ve düşük medyan gecikmeyi doğrular ama katsayıları çok daha dar bulur; doğruluk orta fiyatlı
LLM'lerle aynı seviyede, frontier'ın gerisindedir (`INDEPENDENT` [S13], [S17]).

- **Ne olduğu:** tipli karar ("evaluation") modeli; text generation, tool calling ve streaming yok (`DOC-FACT` https://docs.typesafe.ai/introduction/coding-agents, [v-gw]).
- **Fiyat:** $0.042 / 1M input token, output ücretsiz; TypeSafe, Vercel, OpenRouter ve Cloudflare'de aynı (`DOC-FACT` [models], [v-gw], [cf]; `INDEPENDENT` [S1]).
- **Hız:** OpenRouter sunucu tarafı p50 242.5 ms, p99 13.4 s; istemci medyanları 0.24–0.66 s (`INDEPENDENT` [S4], [S14], [S15], [S16], [S29]).
- **Doğruluk:** "level with mid-price LLMs and 6.5 to 11.5 points behind the frontier" (`INDEPENDENT` [S13]); JevBench sealed set %37, en iyi sistem %95.5, şans %29.3 [S16].
- **"TypeSafe" = şekil garantisi, doğruluk değil:** tip hatası %0 kalır, ama kararlar küçük bağlam değişiklikleriyle çevrilebilir (`INDEPENDENT` [S26], [S23], [S24]).
- **Kalibrasyon:** LLM'lerin sözel (verbalized) güveninden iyi, LLM olasılıklarından iyi değil; tek bir temperature refit hatanın çoğunu düzeltir (`INDEPENDENT` [S17], [S14], [S20]).
- **Şeffaflık:** mimari, base model, boyut ve RLCD açıklanmadı; public benchmark bilinçli olarak yayımlanmadı (`VENDOR-CLAIM` [blog]; `INDEPENDENT` [S9], [S11], [S13]).

## 2. Jev nedir, kim yapıyor

| Alan | Bilgi | Etiket · Kaynak |
|---|---|---|
| Tüzel kişi | TypeSafe AI, Inc., 255 California St, Suite 1300, San Francisco; şartlar Delaware hukukuna tabi | `DOC-FACT` https://typesafe.ai/legal/terms |
| Kuruluş | 2024, merkez San Francisco; "After two years in stealth" | `VENDOR-CLAIM` [PR], [blog] |
| Kurucular | Diogo Almeida (CEO; ex-Google Brain), Sasha Sheng (COO; ex-Meta/FAIR research engineer), Erik Gafni (CTO; Ravel kurucusu, Invitae ve Freenome'da erken çalışan) | `VENDOR-CLAIM` [team], [PR] |
| Almeida'nın RLHF rolü | Üretici: "co-inventor of RLHF/ChatGPT". Bağımsız: "He is not an author of Christiano et al. (2017), which introduced it; he is a primary author of InstructGPT" | `VENDOR-CLAIM` [PR], [primer] · `INDEPENDENT` [S13] → **çelişkili** |
| Finansman | $40M seed, lider DCVC (GP James Hardiman); diğer yatırımcılar isimsiz | `VENDOR-CLAIM` [PR], [team] |
| Değerleme | Forbes (Wikipedia üzerinden): tur $200M değerleme. Bülten gelir, müşteri, pay oranı veya resmi değerleme açıklamıyor | `INDEPENDENT` [S12], [S10] |
| Misyon | "making intelligence composable…"; slogan "We're building prod, not God." | `VENDOR-CLAIM` https://typesafe.ai/manifesto |

**Zaman çizelgesi**
- 2024-05-07 typesafe.ai alan adı kaydı (`INDEPENDENT` [S38]); 2024-05-28 GitHub org `typesafe-ai` (`DOC-FACT` api.github.com/orgs/typesafe-ai).
- 2026-09-11 / 09-14: JS ve Python SDK ilk sürümleri (`DOC-FACT` https://docs.typesafe.ai/sdk/python/changelog, `/sdk/javascript/changelog`).
- 2026-09-15: stealth'ten çıkış, $40M seed, Jev duyurusu; Vercel'de release date (`DOC-FACT` [blog], https://vercel.com/ai-gateway/models/jev; [PR]).
- 2026-09-16: Vercel AI Gateway changelog; 09-18: OpenRouter kaydı `typesafe/jev-1.13` (canonical `…-20260917`) (`DOC-FACT` 01 §5; `INDEPENDENT` [S1]).
- 2026-09-21: Vercel TypeSafe-compatible API; 09-22: yeni kayıtlar durduruldu (`INDEPENDENT`, ikincil [S39]); 09-25: ayrı ürün `typesafe/jev-router` [S1], [S5].

**"System One" (Kahneman).** İsim açıkça Kahneman'ın *Thinking, Fast and Slow* ayrımından geliyor: hızlı, sezgisel System 1 ile
yavaş, bilinçli System 2 (`VENDOR-CLAIM` [blog] FAQ; `DOC-FACT` [sysone]: "the emphasis is on fast, focused judgments").
Kahneman'dan ayrıldıkları nokta: System 1 "error-prone" sayılır, TypeSafe ise System One modellerin "can be made more reliable
than its alternatives" olduğunu iddia eder (`VENDOR-CLAIM` [blog]). "Jev" adı William Stanley Jevons / Jevons Paradox'a gönderme ([blog], [PR]).

**"TypeSafe" = şekil garantisi, doğruluk değil.**
- Üretici: "Possible outputs and structure are defined in advance. The model never makes type errors."; "mathematically impossible" (`VENDOR-CLAIM` [blog]).
- Üreticinin kendi FAQ'ı: "Jev guarantees the shape of its answers, not that every decision is correct." / "it can't invent a category outside that list, but it can choose the wrong one." (`VENDOR-CLAIM` [home]).
- "0% hallucination" bir ölçüm değil, şema garantisinin kendisi: "Our number is not empirical." (`VENDOR-CLAIM` [blog]).
- Vercel: "That guarantee concerns answer structure. Semantic correctness still needs evaluation." (`VENDOR-CLAIM` [v-what]).
- Bağımsız: "the type-error rate remains 0%, even when decision accuracy degrades substantially." (`INDEPENDENT` [S26]).

## 3. Nasıl çalışır

**Model: state + tipli sorular → tipli cevaplar.** `POST https://api.typesafe.ai/v1/systemone`; gövde `state` (string | object |
array), `model` ve `questions` (id → soru map'i). Soru anahtarı (id) modele gönderilmez. `instructions` ve `criteria` JSON nesnesi
veya dizi olabilir; soru metni `state` alanlarına backtick ile atıf yapar (`DOC-FACT` [api]).

| Primitive | Sorduğu | Döndürdüğü | Limit | Kaynak |
|---|---|---|---|---|
| **Choice** | seçenekler arasından biri | `choice`, `probabilities` (toplamı 1), `confidence` | en çok 255 seçenek | `DOC-FACT` [intro], [api] |
| **Score** | sıralı seviyeler | `score`, `probabilities`, `confidence` | 2–10 seviye | `DOC-FACT` [intro], [api] |
| **Noul** | evet/hayır | `noul` (0–1) = "evet" olasılığı; `confidence` yok | — | `DOC-FACT` https://docs.typesafe.ai/primitives/noul |

Vercel ve AI SDK Noul'e `boolean`, cevap alanına `probability` der (`DOC-FACT` [v-eval]).

**Olasılık ve confidence.**
- `confidence`, dönen dağılımdan hesaplanan bir istatistik; Noul'de yok (`DOC-FACT` https://docs.typesafe.ai/confidence).
- RLCD'nin hedefi "decisions and calibrated probabilities"; "Higher probability should correspond to a greater chance that the answer is correct." (`DOC-FACT` [primer]; yöntemin kendisi açıklanmıyor).
- Kalibrasyon yalnızca toplu anlamda: "it does not guarantee that an individual answer is correct." (`DOC-FACT` [sysone]).
- API her olasılığı 0.01'e yuvarlıyor; `noul`'ün 0.01–0.98'e kırpıldığı raporlanıyor (`INDEPENDENT`, aktarılmış [S13]).
- AI SDK'da confidence `result.providerMetadata?.typesafe?.confidence` altında (`DOC-FACT` [aisdk]); Gateway'de `confidence: 0` ve `probabilities: {}` "unavailable" demek, sıfır güven değil (`DOC-FACT` https://vercel.com/docs/ai-gateway/sdks-and-apis/typesafe).

**Paralel ve izole sorular.** "Every question is evaluated in parallel and in isolation against the same state in one go. Adding
questions barely changes the response time." (`DOC-FACT` [intro]). Üreticinin cookbook'u: 13 soru tek çağrıda $0.000497 / 0.27 s,
13 ayrı çağrıda $0.006090 / 2.71 s; "12.2x cheaper and 10.0x faster with no change in answers" (`VENDOR-CLAIM`
https://docs.typesafe.ai/cookbooks/parallel_questions). Sorular arasında yapısal tutarlılık yok: bir soru ve olumsuzu iki Noul
olarak 0.72 + 0.47 = 1.19 verdi (`DOC-FACT` [jag]). AI SDK'nın LLM adaptörleri tüm soruları tek prompt'ta değerlendirir ve bu
semantiği sağlamaz (`DOC-FACT` [aisdk]).

**Mimari (yalnızca iddia).** "a new model architecture, parallel sampler … Reinforcement Learning for Calibrated Decisions (RLCD)";
autoregressive yerine paralel hesaplama; "Jev is neither small nor an LLM" (`VENDOR-CLAIM` [blog], [home]). Veri: "We make all the
data ourselves." (`VENDOR-CLAIM` [blog]); CEO verinin tamamen sentetik olduğunu onayladı (`VENDOR-ECHO` [S34]). Tüm hesaplara aynı
ağırlıklar; müşteri verisiyle fine-tune/LoRA yok; müşteri trafiğiyle eğitim yok (`DOC-FACT` [models]). Paper, teknik rapor veya
model card yok (01 açık soru 1; [S11]).

**Yapmadıkları.** Metin üretmez, tool çağırmaz, dosya düzenlemez (`DOC-FACT` https://docs.typesafe.ai/introduction/coding-agents);
streaming yok ([v-gw], [aisdk]); AI SDK evaluate API multilabel classification ve ilgisiz state batch'lemesi yapmaz ([aisdk]);
gerekçe/reasoning döndürmez (`DOC-FACT` [or-doc]). Function calling, fonksiyon adlarını ve kapalı küme argümanları Choice
sorularına eşleyerek yapılır (`DOC-FACT` https://docs.typesafe.ai/llms.txt). OpenRouter'da `supported_parameters: []`:
`response_format`, `tools`, `reasoning`, `temperature` yok (`INDEPENDENT` [S1]).

**Determinizm.** Üretici determinizm değil "consistency" vaat eder: "`jev-1.13` is extremely consistent…" (`VENDOR-CLAIM` [home],
[jag]). Bağımsız: özdeş geçişlerde cevapların %1.33'ü (bir çalışma) ve %2.2'si (phishing benchmark) değişti (`INDEPENDENT` [S13]).

**Limitler**

| Konu | Değer | Etiket · Kaynak |
|---|---|---|
| Context | TypeSafe: istek başına 64k; `state` + en uzun soru için 32k. Vercel API `context_window: 32000` (açıklaması 64k/32k'yi tekrarlar); OpenRouter ve Cloudflare toplam 32,000 → **çelişkili** | `DOC-FACT` [models], [v-gw], [or-doc], [cf]; `INDEPENDENT` [S1] |
| Girdi | Yalnızca metin (string, JSON, dizi); görsel, ses, video yok | `DOC-FACT` [models] |
| Dil | İngilizce birincil; CJK dahil diğer diller "handled but not equally well" | `DOC-FACT` [models] |
| Rate limit (direkt) | 250,000 token/s, 1,200 RPM; "adjusting dynamically", "can change without notice" | `DOC-FACT` [models] |
| Rate limit (OpenRouter) | `limit_rpm` 2000 | `INDEPENDENT` [S4] |
| Hatalar | `429`, `529` → exponential backoff | `DOC-FACT` [api] |
| Versiyon | `jev-1.13.0`; `jev-latest` ve `jev-preview` → 1.13.0; alias yeni sürümle kayar | `DOC-FACT` [models] |

**API örneği: OpenRouter Decisions** (kısaltıldı: `is_bug` için `criteria` çıkarıldı; tam hali
https://openrouter.ai/docs/guides/community/jev-tutorial.md)
```bash
curl https://openrouter.ai/api/alpha/decisions \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" -H "Content-Type: application/json" \
  -d '{
    "model": "typesafe/jev-1.13",
    "state": {"customer_tier": "enterprise",
              "ticket": "My checkout page shows a blank screen after I click Pay. I have tried two browsers."},
    "questions": {
      "is_bug":  {"type": "noul", "instructions": "Is the customer reporting a software defect?"},
      "team":    {"type": "choice", "instructions": "Which team should own this ticket?",
                  "criteria": {"payments": "Checkout, billing, or payment processing issues.",
                               "frontend": "Rendering, layout, or browser compatibility issues.",
                               "account":  "Login, permissions, or profile issues."}},
      "urgency": {"type": "score", "instructions": "How urgent is this ticket?",
                  "criteria": ["Can wait for the next release", "Should be fixed this week", "Blocking revenue right now"]}
    }}'
```
Yanıt şekli (TypeSafe doğrudan API dokümanındaki Choice örneği [api]; iki raporda da OpenRouter yanıt örneği yok):
```json
{"model": "jev-1.13.0",
 "answers": {"department": {"type": "choice", "choice": "billing",
             "probabilities": {"billing": 0.88, "technical": 0.12, "sales": 0.0}, "confidence": 0.81}},
 "usage": {"input_tokens": 318, "output_tokens": 34}}
```

## 4. Neden var / konumlandırma

**İddia edilen problem**
- "Models have been superhuman at chat for years, so where is all the automation?" (`VENDOR-CLAIM` [blog]).
- Bugünkü yöntem: "coercing a text-generation system into outputting structured decisions, then parsing the results back…" (`DOC-FACT` [intro]).
- Tez: otomasyon "closer to 99% machine-to-machine interactions and 1% human interaction" olacak (`VENDOR-CLAIM` [primer]).
- RLHF eleştirisi: "mode dropping, overconfidence, and lack of reliability", bu yüzden LLM'ler human-in-the-loop ister (`VENDOR-CLAIM` [home]).
- Kalibrasyon anahtar: "If a model can do a task 95% of the time but doesn't say when it's in the 5%, it can't automate that task." (`VENDOR-CLAIM` [blog]).
- JSON mode/structured outputs'a karşı: "forcing an LLM into that format can leave some of its intelligence on the table" (`VENDOR-CLAIM` [home]); CEO: "constrained decoding … make models dumber unfortunately" (`VENDOR-ECHO` [S31 #49718849]). Literatür kısmen destekliyor: format kısıtında reasoning düşüşü [S50], dağılım bozulması [S51] (`INDEPENDENT`).
- Önceki teknik: "We already had encoder models that skipped text generation…" [S31 #49719883]; izinli etiket token'ları üzerinde softmax okuması, vLLM `logprob_token_ids`, SGLang `/v1/score` (`INDEPENDENT` [S13]).

**System 1 vs System 2**

| Boyut | İçerik | Etiket · Kaynak |
|---|---|---|
| Kahneman ayrımı | System 1 hızlı ve sezgisel; System 2 yavaş ve bilinçli | `VENDOR-CLAIM` [blog]; `DOC-FACT` [sysone] |
| Jev'in alanı | "fast, focused judgments" | `DOC-FACT` [sysone] |
| System 2'ye bırakılan | "complex mathematics or chess-like planning" büyük reasoning modellerine daha uygun olabilir | `VENDOR-CLAIM` [home] |
| Escalation | düşük güvende "escalate to a person or a reasoning model"; Gateway "evaluation fallbacks" düşük confidence'ta başka modele yeniden sorar | `DOC-FACT` [sysone], [v-eval] |
| LLM ile ayrım (Vercel) | içerik üretimi veya tool'larla daha geniş problem gerekiyorsa GPT-6 Astra | `DOC-FACT` https://vercel.com/i/jev-vs-gpt-6-astra |
| Literatür | CoT kazancı çoğunlukla matematik/mantıkta [S42]; bazı görevlerde CoT doğruluğu %36.3'e kadar düşürür [S43]; System 2 → System 1 distillation [S44] | `INDEPENDENT` |

Kullanım ilkesi: uzun akıl yürütme veya birçok bağımsız faktör gerekiyorsa soruyu parçala, sonucu kodda birleştir (`DOC-FACT`
[intro]). 02'nin tutarlılık kontrolü: Jev sınıflandırma ve yargıda rekabetçi, temporal/numeric (JevBench %28) ve türetim
kontrolünde [S19] zayıf; yani "System One" gerçek bir görev ayrımı, yalnızca marka değil (`INFERENCE`, 02 §4.1).

## 5. Nerede kullanılır

**Üreticinin ve platformların senaryoları**
- TypeSafe: "smart if-statements", "Map-reducing over big data", "Real-time applications", "Score, judge, verify, guardrail, and detect jailbreaks…" (`VENDOR-CLAIM` [blog]).
- Vercel: agent loop'ta sonraki tool/subagent seçimi; continue/retry/ask/stop kararı; aksiyon öncesi aciliyet/risk skoru; çıktı doğrulama ve guardrail (`VENDOR-CLAIM` https://vercel.com/changelog/typesafe-ai-jev-now-available-on-ai-gateway).
- Cookbook'lar: re-ranking, LLM guardrails, citation checking, function calling, hierarchical classification, SDE cascade, date extraction, RAG passage classification, skill suggestion (`DOC-FACT` https://docs.typesafe.ai/llms.txt).
- Kapsam dışı: "not a drop-in replacement for the LLM behind Claude Code, Cursor…" (`DOC-FACT` https://docs.typesafe.ai/introduction/coding-agents).
- Demolar: Doom bot, Wikiracing (`VENDOR-CLAIM` [blog]); Doom için harness, prompt veya state encoding paylaşılmadı (`INDEPENDENT` [S32]).

**Entegrasyonlar** (aksi belirtilmedikçe `DOC-FACT`)

| Yol | Model ID | Çağrı | Kaynak |
|---|---|---|---|
| TypeSafe direkt | `jev-latest` / `jev-1.13.0` | HTTP `/v1/systemone`; `typesafe-sdk` (Python ≥3.10), `@typesafe-ai/sdk` (Node 20+); SDK'lar MIT | [api], https://docs.typesafe.ai/introduction/quickstart |
| Vercel AI Gateway | `typesafe-ai/jev` | AI SDK 7 `experimental_evaluate` (≥7.0.105); HTTP `ai-gateway.vercel.sh/v1/evaluate`; TypeSafe-compatible base URL `…/typesafe`. OpenAI/Anthropic/Cohere-compatible endpoint'lerde yok | [v-eval], https://vercel.com/docs/ai-gateway/sdks-and-apis/typesafe |
| AI SDK provider | `jev-latest` | `@ai-sdk/typesafe-ai` → `typeSafeAi.evaluationModel(...)`; "experimental and may change in patch releases" | [aisdk] |
| Vercel Connect | — | Credential broker; Jev connector'ı customer-managed API key; `getToken('jev/acme-jev')`. Hobby 500 token request/ay ücretsiz, Pro $3 / 1,000 | https://vercel.com/connect/jev, https://vercel.com/docs/connect |
| OpenRouter | `typesafe/jev-1.13`, `~typesafe/jev-latest` | `POST /api/alpha/decisions` veya `/api/v1/systemone`; chat completions değil; waitlist yok | [or-doc]; `INDEPENDENT` [S6], [S7] |
| Cloudflare Workers AI | `typesafe/jev` | `env.AI.run('typesafe/jev', …)` veya REST `/ai/run` | [cf] |
| TanStack AI | sağlayıcıya göre | `decide()` + `typesafeDecider` / `vercelGatewayDecider` / `cloudflareDecider` / `openRouterDecider` | https://vercel.com/i/jev-integrations |
| Diğer | — | LangChain `langchain-typesafe`; Vercel'in agent framework'ü eve'de varsayılan evaluation modeli; coding-agent skill | jev-integrations; https://vercel.com/changelog/ai-gateway-now-supports-typesafe-clients-and-http-api-for-jev |

Connect ile Gateway farkı: Connect kendi TypeSafe anahtarınızı saklayıp runtime'a verir (fatura TypeSafe'ten), Gateway faturayı
Vercel üzerinden keser (`DERIVED`, 01 §4; token'ın TypeSafe client'a nasıl geçtiği 01'in çıkarımı).

**Benimsenme.** Vercel: "By hour 24, nearly 13% of paid teams were using it." (`VENDOR-CLAIM` https://vercel.com/blog/ai-gateway-jev-model-launch).
HN lansman gönderisi 1,984 puan / 520 yorum [S31], [S52]; 2026-09-22 itibarıyla GitHub'da 2,170 public Jev projesi [S27];
OpenRouter'da 30 dakikalık pencerede 2,945,627 istek [S4] (`INDEPENDENT`).

**Bağımsız kullanıcıların gerçekte yaptıkları** (`INDEPENDENT`)
- Toplu etiketleme ve gateway trafiği: OpenRouter'ın en çok trafik gönderen uygulamaları (pencere belirtilmemiş) "blask datos labelling" (39.2B token, 26.2M istek), mirasim.ai (16.0B), "onet-crosswalk pilot-01" (14.9B), magnific.com "AI Assistant" (14.1B), Portkey AI (13.6B) [S4].
- Sınıflandırma/annotation araştırmaları: 18 hesaplamalı sosyal bilim görevi [S17], 195,857 kaza anlatısının kodlanması [S20], ContractNLI [S21], radyoloji rapor hatası tespiti [S22], phishing tespiti [S15], LLM judge yerine kullanım [S18], [S19].
- Model routing: Classmethod (Japonya) [S29]; bir router testi 40 zor görevin hepsini 0.96 medyan güvenle ucuz modele gönderdi [S13].
- Ajan belleği: Jev-Mem, Jev'i typing, routing, retrieval budget ve candidate scoring için "System-One control plane" olarak kullanıyor [S28]; başka bir projede aynı fikir için açılmış GitHub issue (yalnızca niyet) [S41].
- Ajan oturumunda "danger gate"; outage'ların görünmediği fail-open davranış için issue açıldı [S40].
- Uygulayıcılar: "works incredibly well in concert with LLMs, not as a replacement" [S31 #49718890]; Bryo AI CTO: Gemini biraz daha doğru ama 10–20 kat pahalı (aktarılmış, tekrarlanamaz) [S9]; Vercel mühendisi: Luna yerine Jev 5–18 kat hızlı ve "greater accuracy" (`VENDOR-ECHO` düzeyinde, Vercel Jev'i satıyor) [S9].

## 6. Erişim ve fiyat

| Platform | ID | Not | Etiket · Kaynak |
|---|---|---|---|
| TypeSafe | `jev-1.13.0`; alias `jev-latest`, `jev-preview` | Direkt erişim "early access", waitlist; 09-22'de yeni kayıtlar durduruldu | `DOC-FACT` [models]; `VENDOR-CLAIM` [blog], [PR], [home]; `INDEPENDENT` ikincil [S39] |
| Vercel AI Gateway | `typesafe-ai/jev` | GA, waitlist belirtilmemiş; endpoint API'de sağlayıcı adı `"digitalocean"` (açıklanmamış) | `DOC-FACT` [v-gw]; 01 açık soru 4 |
| OpenRouter | `typesafe/jev-1.13` (canonical `typesafe/jev-1.13-20260917`), `~typesafe/jev-latest` | Varsayılan model listesinde yok, yalnızca `?output_modalities=all` ile görünür; tek sağlayıcı; OpenRouter anahtarı yeter | `INDEPENDENT` [S1], [S2], [S6] |
| OpenRouter | `typesafe/jev-router` | Ayrı ürün: Jev üzerinde çalışan LLM router; endpoint yok, kullanım verisi yok, fiyat "-1" | `DOC-FACT` 01 §5; `INDEPENDENT` [S5] |
| Cloudflare | `typesafe/jev` | Workers AI | `DOC-FACT` [cf] |

**Fiyat.** $0.042 / 1M input token ($42 / 1B), output $0, istek başı ücret yok; dört kanalda aynı (`DOC-FACT` [models], [v-gw],
https://openrouter.ai/~typesafe/jev-latest, [cf]; `INDEPENDENT` [S1]). Örnek: 275 input token = $0.00001155 (`DERIVED`, Gateway
doküman örneği). Üretici karşılaştırması: "238x Lower input price than Claude Fable 5.1" (`VENDOR-CLAIM` [home]).
Sürdürülebilirlik konusunda üretici çelişkili: "We can serve Jev profitably at our current prices." ([home]) ile "We can't prove it
isn't subsidized" ([blog]) (`VENDOR-CLAIM`; [S11], [S13] tekrarlar). Self-hosting başa baş: tek EC2 L4'te Gemma 4 26B tam yükte
milyon karar başına en çok $5.43, Jev aynı 132-token prompt'larla $5.54 (`INDEPENDENT` [S13]).

**Gizlilik ve saklama: kaynaklar çelişkili**

| Kaynak | ZDR | Eğitim | Saklama | Etiket · Kaynak |
|---|---|---|---|---|
| TypeSafe docs | yalnızca enterprise müşterilere | müşteri istek/yanıtıyla eğitilmez | — | `DOC-FACT` https://docs.typesafe.ai/legal, [models] |
| TypeSafe privacy policy | — | "will not train or fine tune" | "as long as reasonably necessary … business or commercial purposes" | `INDEPENDENT` [S35]; 02'ye göre belirsiz (`INFERENCE`) |
| Vercel changelog | "supports Zero Data Retention and No Training, enabled per request" | — | — | `DOC-FACT` 09-16 changelog |
| Vercel Gateway API | `zdr: "none"`, `has_zdr: false` | `no_training: "all"` | — | `DOC-FACT` [v-gw] |
| Cloudflare | "Yes" | — | — | `DOC-FACT` [cf] |
| OpenRouter | public ZDR listesinde | `training: false` | `retainsPrompts: false` | `INDEPENDENT` [S3], [S4] (OpenRouter'ın beyanı, denetlenmedi) |

Ek: OpenRouter'da HIPAA-eligible değil [S4]; site şartlarında sorumluluk tavanı "$100 USD" [S36]. Lisans: model proprietary,
yalnızca API; ağırlık, parametre sayısı veya self-hosting yok (`DOC-FACT` 01 §5; `INDEPENDENT` [S11]). Resmi Hugging Face
varlığı yok; `TypeSafeAI` adlı bir HF hesabı ilgisiz bir StepFun mirror'ı barındırıyor, bağlantısı doğrulanmamış (01 §5).

## 7. İddialar vs kanıtlar

**Üreticinin kendi eval'i** ([evals]). Referans GPT-6 Astra + Claude Fable 5.1 konsensüsü, insan etiketi değil; 4 kurum içi
workflow; vaka sayısı ve güven aralığı yok; LLM'ler üreticinin wrapper'ı ve varsayılan reasoning ayarıyla koştu (`DOC-FACT` / `VENDOR-CLAIM`).

| Model | Ortalama doğruluk | Maliyet / vaka | Süre / vaka |
|---|---|---|---|
| Jev | 67.8% | $0.0004 | 0.4 s |
| sol | 74.1% | $0.0836 | 23.3 s |
| opus 5 | 73.1% | $0.1761 | 37.8 s |
| terra / sonnet 5 | 67.9% / 67.8% | $0.0304 / $0.1174 | 10.1 s / 78.1 s |
| luna | 66.8% | $0.0033 | 12.9 s |
| haiku 4.5 | 53.6% | $0.0195 | 12.5 s |

`DERIVED` (01): Jev hiçbir workflow'da en doğru model değil; Invoice Processing'de (61.8%) haiku 4.5 dışındaki tüm modellerin altında.

| # | İddia (vendor) | Bağımsız kanıt | Hüküm |
|---|---|---|---|
| 1 | **Hız:** "193.6x Faster" [home]; "less than 100 milliseconds", "up to 100 times faster" [PR]; "70ms-500ms", "40x-200x" [blog] (`VENDOR-CLAIM`, kendi içinde tutarsız) | OpenRouter p50 242.5 ms, p99 13.4 s [S4]; istemci medyanı 0.24 s (Fransa) [S15] ile 0.66 s (Japonya) [S29] arası; hız avantajı 0.5x (lokal Gemma'dan yavaş) ile 12.1x arası [S13]; üreticinin kendi verisinde 8 kurulumun ortalaması 97.8x [S13] | **kısmen** |
| 2 | **Maliyet:** "444.6x Cheaper"; "238x Lower input price than Claude Fable 5.1" [home] | Liste fiyatı dört kanalda aynı [S1]; ölçülen avantaj 0.6x (daha pahalı) ile 478x arası, çoğu ücretsiz output'tan [S13]; üreticinin verisinde 149.2x [S13]; medyan 44x ucuz [S17]; karşılaştırılanın ücretinin %0.36'sı [S19]; JevBench $0.040 vs GPT-6 Luna (low) $0.127 / 1k karar [S16]; Gemma self-host ile başa baş [S13] | **kısmen** |
| 3 | **Frontier düzeyi zekâ:** "frontier-level intelligence" [PR]; "similar levels of intelligence on System One tasks" [blog] | "6.5 to 11.5 points behind the frontier" [S13]; 15 değerlendirme görevinin 14'ünde en iyi LLM'in gerisinde, medyan −11.6 macro-F1 [S17]; 72.5% vs Fable 5.1 84.0%, Astra 79.0% [S14]; phishing 62.6% vs Haiku 4.5 81.3% [S15]; JevBench Intelligence 53.1 vs GPT-6 Luna (low) 96, sealed 37% vs 95.5% [S16] | **doğrulanmadı** |
| 4 | **Dar yargı görevlerinde LLM judge yerine geçer:** "Score, judge, verify…" [blog] | 27 eşleştirilmiş karşılaştırmanın yalnız 8'inde anlamlı fark [S18]; SOTA judge'a 3 puan yakın [S19]; insan etiketine karşı F1 0.908, bir frontier +0.059, diğeri ayırt edilemez [S20]; ContractNLI'de LLM'ler daha doğru [S21] | **kısmen** |
| 5 | **Kalibrasyon:** RLCD "calibrated probabilities" [primer]; üretici ECE veya reliability grafiği yayımlamadı (01 açık soru 9; [S13]) | 19 LLM'in 16'sının sözel güveninden iyi, ama 3 frontier modelin medyan kalibrasyon hatası daha düşük (Jev 0.157, frontier 0.066) [S17]; ECE 0.161, 6 modelin en kötüsü [S14]; ChaosNLI'de düz 1/3 tahmini Jev'i, GPT-6 Astra'yı ve DeepSeek'i geçiyor [S14]; empati görevinde şansa yakın doğrulukta yüksek güven [S17]; refit hatayı 3.3 kat düşürüyor [S20] | **kısmen** |
| 6 | **Tip güvenliği:** "The model never makes type errors", "mathematically impossible" [blog] | Tip hatası oranı doğruluk ciddi düştüğünde bile %0 [S26]; bağımsız çalışmalarda şema hatası %0 (02 özet) | **doğrulandı** |
| 7 | **"0% hallucination" / "can't hallucinate"** [blog] | Üreticinin kendisi: "it can choose the wrong one" [home], "Our number is not empirical" [blog]; "it can still emit a completely wrong valid value" [S31 #49718492]; "It does absolutely hallucinate" [S31 #49727858]; "delegates the hallucination problem a little bit to the user" [S9] | **çelişkili** (yalnızca şema anlamında doğru) |
| 8 | **Robustness / consistency:** "extremely consistent … quantitatively similar outputs for semantically similar inputs" [jag] | Eklenen tek görüş kararların %12.1'ini çeviriyor [S23]; optimize bağlam 508 doğru kararın 312'sini (%61.4) çeviriyor [S24]; yes/no seçenek adlarını değiştirmek AUC'yi .8146'dan .5806'ya düşürüyor [S26]; adaptive injection başarısı %1.8'den %3.5'e [S25]; özdeş geçişlerde %1.33–2.2 değişim [S13] | **çelişkili** |
| 9 | **İngilizce dışı diller:** "handled but not equally well" [models] (üreticinin kendi uyarısı) | Rusça XNLI 88.3%'ten 77.3%'e, ECE üç katı; İspanyolca 3–6 puan kayıp [S13]; Türkçe ölçülmedi | **doğrulandı** (zayıflık teyit edildi) |
| 10 | **Bellek/retrieval kullanımı:** re-ranking ve RAG passage classification cookbook'ları; re-ranking top-1 5%→18%, top-10 38%→62% (40 CLERC sorgusu) (`VENDOR-CLAIM` https://docs.typesafe.ai/cookbooks/rerank_typesafe) | Jev-Mem: LoCoMo'da LLM-as-a-Judge 0.777, en güçlü baseline'a göre %11.0 göreli iyileşme, bellek inşası 158 s (6.6x hızlanma); tek, hakemsiz preprint, LLM-judge metriği [S28] | **kısmen** |
| 11 | **Paralel sorular:** "Adding questions barely changes the response time" [intro]; batch "12.2x cheaper and 10.0x faster" (cookbook) | İki raporda bağımsız ölçüm yok | **doğrulanmadı** |
| 12 | **Fiyat sürdürülebilir:** "We can serve Jev profitably at our current prices." [home] | Aynı üretici: "We can't prove it isn't subsidized" [blog]; [S11], [S13] tekrarlar | **çelişkili** |
| 13 | **Kurucu:** "co-inventor of RLHF/ChatGPT" [PR], [primer] | Christiano et al. (2017) yazarı değil; InstructGPT'nin birincil yazarı [S13] | **çelişkili** |
| 14 | **Benimsenme:** 24. saatte ücretli ekiplerin ~%13'ü (Vercel blog) | HN 1,984 puan [S52]; 2,170 GitHub projesi [S27]; OpenRouter'da 30 dk'da ~2.9M istek [S4]; %13 rakamı bağımsız doğrulanamıyor | **kısmen** |

## 8. Güçlü yanlar ve zayıf yanlar

**Güçlü yanlar**
- **Şema garantisi pratikte tutuyor:** tip hatası %0 [S26]; Choice başına 255 seçeneğe kadar (`DOC-FACT` [api]). Kıyas: Anthropic API'si 78 seçenekli bir şemayı "compiled grammar is too large" diyerek reddetti [S14].
- **Düşük maliyet:** output ücretsiz, $0.042/M input [S1]; medyan 44x ucuz [S17]; karşılaştırılanın ücretinin %0.36'sı [S19]. Üreticinin re-ranking çalışması: 1,200 çağrı $0.0645 (`VENDOR-CLAIM` cookbook).
- **Düşük medyan gecikme:** p50 242.5 ms [S4]; Fransa'dan istemci p50 239 ms (network floor 163 ms) [S15].
- **İkili ve az sınıflı İngilizce kararlarda rekabetçi:** yes/no görevinde "94%, AUROC 0.970 … Jev ties" [S14]; radyolojide false-negation AUROC 0.977 [S22]; JevBench routing %100, trap/adversarial %83 [S16].
- **Cascade'in ilk basamağı:** düşük güvenli öğeleri LLM'e yönlendirmek LLM'in tek başına sonucunu ¼–½ maliyetle yakalıyor veya geçiyor [S17]. Karşı kanıt: LLM judge'lar Jev'in en emin hatalarını tekrarlıyor, kazanç en çok 1.5 puan [S18].
- **Olasılık ve confidence çıktısı eşik tabanlı escalation'da kullanılıyor:** Gateway'de yerleşik fallback (`DOC-FACT` [v-eval]); sözel güvenden iyi kalibrasyon [S17]; ucuz refit [S20].
- **Geniş dağıtım:** Vercel, OpenRouter, Cloudflare, TanStack, LangChain (§5); OpenRouter'da waitlist yok [S6]; OpenRouter yolunda training ve prompt retention kapalı, ZDR listesinde [S3], [S4].

**Zayıf yanlar**
- **Frontier'ın gerisinde doğruluk, zor öğelerde sert düşüş:** [S13], [S17], [S14], [S15]; sealed %37 [S16].
- **Sayı, tarih, sayma:** "Jev is not a calculator", "`jev-1.13` does not count reliably", tarihleri sıralı nicelik değil metin olarak okur (`DOC-FACT` [jag]); JevBench temporal/numeric %28 [S16]; "Larger gaps arise when judgments require checking a derivation" [S19].
- **Kırılganlık ve injection:** [S23], [S24], [S25], [S26]; üretici: "State is data, and `jev-1.13` does not treat it as hostile by default." (`DOC-FACT` [jag]).
- **Kendinden emin hatalar:** empati görevinde yüksek güven, şansa yakın doğruluk [S17]; router testinde 0.96 güvenle yanlış yönlendirme [S13].
- **Diğer bilinen zaaflar** (`DOC-FACT` [jag]): harfiyen okuma ("answers the question you wrote, not the one you meant"), çift olumsuz ve dolaylılık, context rot ("unrelated material in the `state` costs you accuracy"), instructions ile criteria çeliştiğinde karışma, sorular arası yapısal tutarlılık yok (0.72 + 0.47 = 1.19), Score seviyelerinde zayıf sayısal kalibrasyon.
- **İngilizce dışı dillerde düşüş:** [S13]; [models].
- **Açıklama yok:** reasoning döndürmez (`DOC-FACT` [or-doc]); metin, tool ve streaming yok.
- **Kaba ve kısmen nondeterministik çıktı:** olasılıklar 0.01'e yuvarlanıyor, özdeş girdide %1.33–2.2 değişim [S13].
- **Kuyruk gecikmesi:** p99 13.4 s [S4].

## 9. Kırmızı bayraklar ve riskler

1. **Başlık iddiaları kendi testleri:** "These are company-generated results, not independent measurements." [S10]. Eval referansı LLM konsensüsü, 4 kurum içi workflow, vaka sayısı yok; üretici "some bias could exist" ve konsensüsün "biases answers towards OpenAI and Anthropic's models" olduğunu kabul ediyor ([evals], [blog]). Halüsinasyon/tool-error grafiğinde LLM sayıları OpenRouter'dan, yöntem belirtilmemiş ([blog]). Public benchmark bilinçli olarak yok ([blog]; [S13]; Latent Space bölüm başlığı "Why TypeSafe Rejects Public Benchmarks" [S34]); Artificial Analysis ve OpenRouter benchmark widget'ları boş [S4]. Hız ve maliyet rakamları üreticinin kendi materyalleri arasında tutarsız (01 açık soru 5).
2. **Kapalı model, olası fine-tune:** "architecture is close to the chest for now" (CEO, `VENDOR-ECHO` [S31 #49718824]); "outside observers suspect is built on top of an open-weight LLM" [S9]; ağırlık, parametre sayısı, self-hosting yok [S11]. CEO'nun Latent Space ifadeleri (sentetik veri; "Why Diogo Wouldn't Pre-Train with $1 Billion"; "I am not going to put into the models that you are Jev from TypeSafe") 02'ye göre mevcut bir base üzerinde post-training'e işaret ediyor, doğrulanmamış (`INFERENCE` [S34]). GitHub org'unda LLaDA (diffusion LM) ve vLLM fork'ları var; Jev hakkında bir şey kanıtlamaz (01 açık soru 1).
3. **Mekanizma yeni değil, arayüz kopyalanıyor:** "Every piece of the mechanism" Jev'den önce vardı [S13]; açık replikalar Qwen3.5-4B ve Gemma üzerinde. OpenRouter'da `decisions` modality'sinde rakipler çıktı: `jaredpalmer/kev-4b` (Qwen3.5-4B-Base üzerinde LoRA, aynı `/v1/systemone` sözleşmesi, aynı fiyat), `respan/span-01`, `upstage/solar-decide` (`INDEPENDENT` [S1]).
4. **Fiyatın sürdürülebilirliği belirsiz:** sübvansiyon dışlanamıyor; üretici ifadeleri çelişkili ([home] vs [blog]).
5. **Operasyonel olgunluk:** OpenRouter'da tek sağlayıcı, fallback yok [S2]; p99 13.4 s, 09-28 uptime %98.32 (gün devam ederken) [S4]; lansman haftasında talep yüzünden API geçici olarak hizmet veremedi [S9]; yeni kayıtlar 09-22'de durduruldu [S39]; rate limit'ler "can change without notice" ([models]); alias kayması hangi sürümün cevap verdiğini kaydetmeyi zorlaştırıyor ([models]; [S13]); OpenRouter build etiketi 09-17, lansmandan sonra, ağırlıkların değişip değişmediği bilinmiyor (01 açık soru 13); AI SDK API'si experimental ([aisdk]); bir entegrasyonda fail-open davranış [S40].
6. **Gizlilik ve hukuk:** ZDR durumu kanala göre çelişkili (§6); doğrudan API'de saklama maddesi belirsiz [S35]; sorumluluk tavanı $100 [S36]; HIPAA-eligible değil [S4]; Vercel endpoint'inde açıklanmamış `digitalocean` sağlayıcı adı (01 açık soru 4); MCA, DPA ve AUP okunmadı (01), 02 ise DPA veya SLA bulamadı.
7. **Taklit ve ilişkisiz alan adları:** jevtypesafeai.com (2026-09-18) "an instant hosted key" satıyor ve "Not affiliated with or endorsed by TypeSafe AI." diyor [S37], [S38]; thejevai.com [S13]; jevaiguide.com, jevainews.com [S38]; jev-router.com JevBench operatörlerine ait, OpenRouter'ın `typesafe/jev-router`'ı ile ilgisiz [S16]. Bunlar üzerinden yapılan çağrı veriyi bilinmeyen bir üçüncü tarafa gönderir (`INFERENCE` 02).
8. **Değerlendiricilerde çıkar çatışması:** JevBench operatörleri jev-router.com'u işletiyor [S16]; Vercel, OpenRouter ve LangChain dağıtıcı veya entegratör [S9], [S6], [S13]; toplu inceleme Claude yardımıyla hazırlanmış [S13]; ~20 arXiv çalışmasının tamamı hakemsiz ve birkaç günlük (02 §2); popüler "deep dive"lar büyük ölçüde tekrar [S30].
9. **Kurucu kimlik bilgisinin abartılması:** RLHF "co-inventor" iddiası [S13] ile çelişiyor (§7 satır 13).

## 10. Bilinmeyenler

- Mimari, base model, parametre sayısı, tokenizer (OpenRouter: "Other"), donanım ve RLCD yöntemi; paper, ağırlık veya model card yok (01 açık soru 1; 02).
- $0.042/M fiyatın kalıcılığı (sübvansiyon).
- Doğrudan API'de saklama süresi, alt işleyiciler (subprocessors), DPA ve SLA; MCA ve AUP okunmadı; model çıktılarının lisans şartları doğrulanmadı.
- Her gateway'deki etkin context limiti (64k vs 32k) ve kanal bazında gerçek ZDR durumu.
- 32k sınırına yakın ve büyük ölçüde ilgisiz state ile davranış: üretici zayıflık olarak listeliyor, bağımsız sayılar zayıf.
- **Türkçe performansı:** hiçbir çalışma ölçmedi; Rusça ve İspanyolca düşüyor [S13].
- HLMemo tipi kararlara aktarılabilirlik (bellek yerleştirme, çelişki ve duplikasyon tespiti, librarian için relevance gating): yalnızca Jev-Mem [S28] değiniyor, tek preprint.
- Jev Router'ın nasıl çalıştığı ve yönlendirilen modellerin ücreti: endpoint ve kullanım verisi yok [S5].
- Lansman sonrası sunulan ağırlıkların değişip değişmediği (`jev-1.13-20260917`).
- Seed sonrası finansman: 01'in taradığı ikincil kaynaklarda ~$10B değerlemeyle görüşme haberleri var, doğrulanmadı; 02 yalnızca $200M'ı (Forbes, Wikipedia üzerinden [S12]) buluyor.
- Bağımsız leaderboard yok (Artificial Analysis, LMArena); tek çok sistemli tablo JevBench ve operatörünün ticari çıkarı var.
- Reddit'te ilgili tartışma bulunamadı; X/Twitter gönderileri birinci elden okunmadı (02 §3).

**Kaynak raporlar arası çelişkiler**

| Konu | 01 (primary) | 02 (independent) |
|---|---|---|
| Almeida'nın RLHF rolü | "co-inventor of RLHF/ChatGPT" (`VENDOR-CLAIM` [PR], [primer]) | Christiano et al. (2017) yazarı değil, InstructGPT'nin birincil yazarı [S13] |
| Noul değer aralığı | "`noul` (0–1)" [intro]; quickstart örneği `1.0` basıyor | 0.01'e yuvarlama ve 0.01–0.98 kırpma raporlanıyor [S13] |
| Müşteri sözleşmeleri | docs.typesafe.ai/legal MCA, DPA ve Privacy Policy'yi anıyor (okunmadı) | "no DPA or SLA that I could find"; ayrı API veya ticari şart bulunamadı |
| Rate limit | direkt: 1,200 RPM, 250k token/s [models] | OpenRouter: `limit_rpm` 2000 [S4] (farklı kanal) |
| Değerleme | ikincil kaynaklarda ~$10B görüşme ve $200M seed değerlemesi, doğrulanmamış | yalnızca $200M [S12]; bülten resmi değerleme açıklamıyor [S10] |
| Gizlilik | TypeSafe ZDR yalnız enterprise; Vercel API `zdr: "none"` | OpenRouter ZDR listesinde, `retainsPrompts: false` [S3], [S4] |
| Context | TypeSafe 64k / 32k | OpenRouter 32k [S1] |
