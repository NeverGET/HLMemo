# 02 — Jev (TypeSafe AI): independent evidence and context

Status: research input for `03-JEV-DOSSIER.md`. Written 2026-09-28 (sources read 2026-09-28, 15:00–16:15 UTC).
Scope: OpenRouter data (free, unauthenticated endpoints only), third-party evaluations, community reception, background
concepts, red flags. Vendor docs and Vercel pages are covered separately in `01-primary-sources.md`.
Money spent: $0. No model API was called (only public metadata endpoints and web pages).

Labels on every claim:
- `INDEPENDENT`: a third party measured or stated it.
- `VENDOR-ECHO`: a third party repeating a TypeSafe claim.
- `INFERENCE`: my own reasoning from the cited facts.

`[S#]` points to the numbered source list at the end. Quotes are verbatim and at most 30 words. Some pages say "Jev
1.13" and others "jev-1.13.0"; both mean the one production model, and every study that states a version used
`jev-1.13.0`.

---

## TL;DR

- **OpenRouter IDs.** `typesafe/jev-1.13` is the real model. `~typesafe/jev-latest` is an alias that currently resolves
  to 1.13. `typesafe/jev-router` is a separate LLM router that is "powered by" Jev and has no usage data yet.
  - Price: $0.042 per 1M input tokens, $0 per output token, no per-request fee.
  - Context 32k. `supported_parameters` is **empty**: no `response_format`, `structured_outputs`, `tools` or
    `reasoning`.
  - Output modality is a new type, `decisions`. Calls go to `POST /api/alpha/decisions` or `/api/v1/systemone`, not to
    chat completions.
- **Accuracy (independent).** Level with mid-price LLMs and 6.5–11.6 points behind frontier LLMs on classification
  with human labels. It is much weaker on hard or sealed items: JevBench sealed accuracy is 37% against 95.5% for the
  best system, with chance at 29.3%.
- **Calibration (independent).** Better out of the box than LLMs' *verbalized* confidence. It is not better than LLMs
  whose probabilities are read directly, and the direction of its error changes by domain. A small temperature refit
  fixes most of it.
- **Speed and cost (independent).** Server p50 is about 0.24 s on OpenRouter, and clients measure 0.24–0.65 s. The p99
  on OpenRouter is 13.4 s.
  - Measured cost advantage runs from 0.6x (Jev dearer) to 478x. It is driven mostly by free output.
  - Measured speed advantage runs from 0.5x (slower than a local Gemma) to 12.1x. TypeSafe's 193.6x / 444.6x figures
    are self-tested.
- **Robustness (independent).** Schema errors are 0%, but decisions flip:
  - one appended opinion flips 12.1%;
  - an optimized context flips 61.4%;
  - swapping the yes/no option names drops AUC from .81 to .58.
- **Red flags.**
  - Architecture, base model, size and RLCD are all undisclosed. Press reports that outside observers suspect an
    open-weight LLM base.
  - The vendor published no public benchmarks and no calibration metrics.
  - Pricing may be subsidized ("can't prove it isn't").
  - Unaffiliated lookalike domains resell keys.
  - The privacy picture is good via OpenRouter (ZDR-listed, no training, no prompt retention). The direct-API privacy
    policy has only a vague retention clause.

---

## 1. OpenRouter (free, unauthenticated endpoints)

Raw JSON is saved in `docs/research/jev/raw/`:
- `openrouter-models-jev.json`
- `openrouter-endpoints-jev.json`
- `openrouter-jev-1.13-page-excerpts.json`

Compact excerpts are in the appendix.

### 1.1 Model records (`GET /api/v1/models`)

**Discovery gotcha.** The default `GET /api/v1/models` (460 models) returns only `typesafe/jev-router`. The actual
model `typesafe/jev-1.13` and the alias `~typesafe/jev-latest` appear only with `?output_modalities=all` (631 models).
Their output modality is `decisions`, not `text`. `INDEPENDENT` (my API query) [S1]

| Field | `typesafe/jev-1.13` | `~typesafe/jev-latest` | `typesafe/jev-router` |
|---|---|---|---|
| name | TypeSafe: Jev 1.13 | TypeSafe: Jev Latest (alias_target → `typesafe/jev-1.13`) | TypeSafe: Jev Router |
| canonical_slug | `typesafe/jev-1.13-20260917` | `~typesafe/jev-latest` | `typesafe/jev-router` |
| created | 1789689684 = 2026-09-18T00:01:24Z | 2026-09-18T00:01:25Z | 1790363560 = 2026-09-25T19:12:40Z |
| context_length | 32,000 | 32,000 | 1,000,000 |
| top_provider.max_completion_tokens | 28,800 | 28,800 | null |
| pricing.prompt / completion | 0.000000042 / 0 ($0.042 / $0 per 1M) | same | "-1" / "-1" (variable, router) |
| pricing.request | not present | not present | not present |
| supported_parameters | `[]` | `[]` | `[]` |
| architecture | text → decisions; tokenizer "Other" | text → decisions; tokenizer "Router" | text+image+file+audio+video → text; tokenizer "Router" |
| hugging_face_id | null | null | null |
| knowledge_cutoff | null | null | null |

Description of `typesafe/jev-1.13` (full text from the model page) [S4]:
> "Jev is a structured decision model from TypeSafe, and the first of its System One models. System One models make
> fast, structured decisions for software, returning a typed choice rather than free-form text."

Description of the router [S5]:
> "Jev Router picks the best model and reasoning effort for each request, balancing quality, speed, and cost. It runs on
> Jev, TypeSafe's first System One model"

Findings:
- **No standard LLM parameters.** `supported_parameters` is `[]`, so there is no `response_format`, `structured_outputs`,
  `tools`, `reasoning` or `temperature`. The typing lives in the request body of a separate API. `INDEPENDENT` (API data)
  [S1][S2]
- **Separate endpoint.** OpenRouter documents two surfaces, `POST https://openrouter.ai/api/alpha/decisions` and
  `POST https://openrouter.ai/api/v1/systemone`. `INDEPENDENT` [S6]
  - [S7]: "The Decisions API is at https://openrouter.ai/api/alpha/decisions , outside the /api/v1 prefix the chat
    client uses."
  - [S6]: "It is not a drop-in replacement for a chat model."
- **The 28,800 max-completion figure is a placeholder.** It is not a real output budget: output is a fixed decision
  object and is billed at $0. It looks like 90% of context. `INFERENCE` [S1]
- **A small new model class has formed on OpenRouter.** There are 7 entries with output modality `decisions`:
  - `jaredpalmer/kev-4b` (2026-09-25): "built as a LoRA adapter and pointer head on Qwen3.5-4B-Base and served over the
    same /v1/systemone contract as TypeSafe's...". Same $0.042/M price, SiliconFlow fp8.
  - `respan/span-01` and `span-01-lite` (2026-09-26).
  - `upstage/solar-decide` (created today, 2026-09-28): "served as a System One endpoint on Solar Mini 4".

  `INDEPENDENT` [S1]. So Jev's interface is already being copied by competitors. `INFERENCE`
- **Jev Router is not a way to use Jev directly.** It is a routing product that picks LLMs. The API lists **no
  endpoints** for it (`"endpoints":[]`), and the page says "No usage data available yet." The page also says "The
  pricing shown on this page for Jev Router is zero". `INDEPENDENT` [S5]. What the router's routed-model charges will
  be is unknown. `INFERENCE`

### 1.2 Endpoints (`GET /api/v1/models/<id>/endpoints`)

`typesafe/jev-1.13` has **exactly one provider**, and `~typesafe/jev-latest` and `typesafe/jev-router` return
`"endpoints": []`. `INDEPENDENT` [S2]

| Field | Value |
|---|---|
| name | TypeSafe \| typesafe/jev-1.13-20260917 |
| provider_name / tag | TypeSafe / `typesafe` |
| upstream (model page) | `baseUrl: https://api.typesafe.ai/v1`, `provider_model_id: jev-1.13.0`, adapter `TypeSafeDecisionsAdapter` |
| quantization | `unknown` |
| max_completion_tokens / max_prompt_tokens | 28,800 / null |
| supports_tool_choice | none/auto/required/function all `true` (generic flags; `supported_parameters` still `[]`) |
| status | 0 |
| uptime_last_5m / 30m / 1d | 100 / 100 / 100 (at fetch) |
| latency_last_30m / throughput_last_30m | null / null in this API |
| supports_implicit_caching | false |
| limit_rpm / limit_rpd (model page) | 2000 / null |
| is_hipaa_eligible (model page) | false |

What the model page's embedded endpoint stats show (30-minute window, as labelled by OpenRouter) [S4]:
- p50 = **242.5 ms**, p75 = 314 ms, p90 = 1,191 ms, p95 = 2,203 ms, **p99 = 13,422 ms**.
- `request_count: 2945627` in that window. Throughput is null, since there is no text output.
- `INDEPENDENT` (OpenRouter telemetry)

Daily uptime from the same page: 2026-09-28 = 98.32% (953 judged minutes, day in progress), 09-27 = 100%, 09-26 = 100%.
`INDEPENDENT` [S4]

**Data policy on the OpenRouter route** [S4]:
- `"training":false,"trainingOpenRouter":false,"retainsPrompts":false,"canPublish":false`.
- The endpoint appears in OpenRouter's public ZDR list (`GET /api/v1/endpoints/zdr`, 917 endpoints) [S3].
- `INDEPENDENT` (OpenRouter's provider metadata; it states the terms OpenRouter has with TypeSafe, and I have not
  audited them).

### 1.3 Model page (`https://openrouter.ai/typesafe/jev-1.13`)

- **No benchmarks.** OpenRouter's embedded benchmark widgets are all empty for Jev: `benchmarkScores`
  `{"scores":[],"lookback_days":32}`, Artificial Analysis `[]` and Design Arena `records: []`. `INDEPENDENT` [S4]
- **Top public apps by traffic** (window not stated) [S4]:
  - "blask datos labelling" (maxtesla.com): 39.2B tokens, 26.2M requests.
  - mirasim.ai: 16.0B tokens.
  - "onet-crosswalk pilot-01": 14.9B tokens.
  - magnific.com "AI Assistant": 14.1B tokens.
  - Portkey AI: 13.6B tokens.

  About 1,500–4,000 tokens per request. Heavy bulk labelling and gateway traffic ten days after launch. The label for
  these rows [S4]: "Public apps that send the most traffic to this model." `INDEPENDENT`
- **Available to everyone through OpenRouter.** [S6]: "typesafe/jev-1.13 (or the ~typesafe/jev-latest alias) is
  available to anyone with an OpenRouter API key". `INDEPENDENT` (OpenRouter's own statement about its own service)
- **OpenRouter hosts Jev guides and cookbooks** (tutorial, SDK, tool-call gate, verified cascade, classification) and a
  "Jev Lab" [S4][S6]. OpenRouter is a distribution partner, so these are promotional, not evaluative. `INFERENCE`

---

## 2. Third-party evaluations

Summary: **no Artificial Analysis, LMArena or OpenRouter-benchmark entry exists.** Queries tried:
- "artificialanalysis.ai Jev TypeSafe"
- the OpenRouter model page's AA widget, which is empty [S4]

LMArena does not apply: the model generates no text. Evaluation has come instead from about 20 arXiv preprints (all
unrefereed and days old), GitHub benchmark repositories, one independent leaderboard (JevBench) and an aggregate review.

### 2.1 Accuracy against LLMs (human or public labels)

| Study | n / labels | Result | Label |
|---|---|---|---|
| Ibrahim & Zaki, arXiv 2609.24574 [S17] | 7,977 items, 18 computational-social-science tasks, 19 LLMs | "trails the per-task best LLM on 14 of 15 evaluation tasks, with a median deficit of 11.6 macro-F1 points, at a median 44 times lower measured cost" | `INDEPENDENT` |
| Janardhan, jev-frontier-bench [S14] | 200 items (BANKING77, BoolQ, Yelp, ChaosNLI); all via OpenRouter; logs committed | Jev 72.5% vs Claude Fable 5.1 84.0%, GPT-6 Astra 79.0%, DeepSeek V4.1 Flash 76.0%, MiniMax M3 75.5%, Kimi K3 74.5% | `INDEPENDENT` |
| Rao & Callison-Burch, arXiv 2609.29769 [S18] | 9 rubric panels, 7 benchmarks, vs 3 flash-tier LLM judges | "Jev's accuracy differs significantly from an LLM judge's in only 8 of 27 paired comparisons" | `INDEPENDENT` |
| Li, Miao, Krishnan, arXiv 2609.26550 [S19] | vs 16 judges, blinded human adjudication | "within three percentage points of a state-of-the-art LLM judge … at 0.36% of the comparator's fee" | `INDEPENDENT` |
| Rafe & Das, arXiv 2609.24052 [S20] | 2,416 blinded human judgments; 195,857 crash narratives coded | "Against human labels the typed model attains an F1 of 0.908. One frontier model gains 0.059 and the other is indistinguishable from it." | `INDEPENDENT` |
| Zhang et al., arXiv 2609.27678 (ContractNLI) [S21] | vs 9 LMs | "Jev has the lowest cost and median response time …, while hosted language models achieve higher baseline accuracy." | `INDEPENDENT` |
| Huang et al., arXiv 2609.27607 (radiology) [S22] | RadEvalX / RadEvalExpert | "Local RadMatch achieves stronger agreement on clinically significant errors in both expert datasets" | `INDEPENDENT` |
| jev-phishing-bench [S15] | 2,000 emails (PhishNChips v5.2), vs Claude Haiku 4.5 | "Jev's own verdict loses clearly on accuracy (McNemar p < 0.0001) and wins on speed and cost." 62.6% vs 81.3% | `INDEPENDENT` |
| Same repo, decomposed [S15] | 5 narrow signal questions + logistic regression | 95.0% vs Haiku with the same five questions 93.2%, "not significant (McNemar p = 0.063)", "on a dataset that a regex already separates at 91.8%" | `INDEPENDENT` |
| JevBench v1.4.2.2 (Benchmark Heaven) [S16] | "534 public + 308 sealed aggregate decisions · one request at a time from a server in Germany" | Details below the table | `INDEPENDENT` (conflict noted in §5) |

**JevBench details for Jev** [S16]:
- Intelligence 53.1, Calibration 76.3, cost $0.040 per 1k decisions, median latency 0.65 s.
- Sealed-set accuracy 37%, where "the best sealed accuracy among ranked systems is 95.5% ( GPT-6 Luna , # 37 ); chance
  is 29.3%".
- By family: temporal/numeric 28%, routing 100%, trap/adversarial 83%.
- For comparison, GPT-6 Luna at low reasoning effort scores Intelligence 96 at $0.127 per 1k decisions.

**Aggregate review** (xbill, dev.to; covers 14 arXiv preprints, 104 GitHub repos and 33 posts, and re-scores committed
outputs where available) [S13]:
> "On accuracy, Jev sits level with mid-price LLMs and 6.5 to 11.5 points behind the frontier in the cleanest
> comparison."

`INDEPENDENT`. The author discloses: "Sources were gathered and audited with AI assistance (Claude)" and is a Google
Developer Expert.

**Where it does well** (independent):
- Binary and few-class English decisions. Janardhan's yes/no task: "94%, AUROC 0.970 … Jev ties" [S14].
- Radiology false-negation detection, AUROC 0.977 [S22].
- As a cheap **first stage in a cascade**: "routing low-confidence items to an LLM matches or exceeds the LLM alone at
  a quarter to half of its cost" [S17]. `INDEPENDENT`
- **Counter-evidence on cascades:** "The LLM judges repeat nearly all of Jev's most confident errors", so a replayed
  cascade gains "at most 1.5 points over the best single judge" [S18]. `INDEPENDENT`

**Where it breaks** (independent):
- Non-English. [S13]: "Russian XNLI dropped from 88.3% to 77.3% with ECE tripling, and Spanish cost 3 to 6 points."
  This is directly relevant to Turkish content. `INFERENCE`
- Confident failure when the answer is absent from the input. [S17]: "on one task, empathy in peer-support dialogues,
  the model reports high confidence while performing near chance."
- Hard or sealed items: 37% on the JevBench sealed set [S16].
- Numbers, dates, counting. TypeSafe's own guidance, repeated by a third party [S30]: "Numbers, dates and counting stay
  in code." `VENDOR-ECHO`

### 2.2 Calibration

- **Better than LLMs' stated confidence, not better than LLM probabilities.**
  - [S17]: better "than the verbalized confidence of 16 of the 19 LLMs, yet three frontier models show lower median
    calibration error (0.157 against 0.066)".
  - Janardhan: Jev ECE 0.161, the worst of the 6 models, against LLMs returning full probability distributions [S14].
  - `INDEPENDENT`
- **Human label disagreement.** [S14]: "A uniform one-third guess scores 0.127, which beats Jev, GPT-6 Astra and
  DeepSeek" (Jensen-Shannon divergence against 100 annotators on ChaosNLI). `INDEPENDENT`
- **No vendor calibration metrics.** [S13]: "TypeSafe publishes no calibration error, reliability plot, Brier score or
  log loss for Jev on any dataset." `INDEPENDENT`
- **Refit works.** One temperature fitted on 50 to a few hundred labels fixes most of the error; Rafe & Das report
  "Recalibration on the same labels reduces calibration error by a factor of 3.3" [S20]. `INDEPENDENT`
- **Coarse output.** [S13]: "The API also rounds every probability to 0.01." It also reports `noul` clamped to
  0.01–0.98. `INDEPENDENT` (reported)
- **Nondeterminism.** [S13]: "1.33% of answers changed between identical passes in one study, 2.2% in the phishing
  benchmark". `INDEPENDENT`

### 2.3 Speed and latency

- **Vendor claim.** "Jev's response time supposedly ranges from 70ms-500ms, 40x-200x faster than traditional LLMs." [S8]
  `VENDOR-ECHO`
- **Measured.**
  - OpenRouter server-side p50 242.5 ms and p99 13.4 s [S4].
  - Client p50 from France: "239 ms (network floor 163 ms)" [S15].
  - From India: "0.43 / 0.55 s" median/p95 [S14].
  - From Germany: median 0.65 s [S16].
  - From Japan: "Median Latency … 0.661s" (n = 40) [S29].
  - `INDEPENDENT`
- **Range across studies.** [S13]: "Speed runs from 0.5x, slower than a local Gemma, to 12.1x faster; cost runs from
  0.6x, dearer, to 478x cheaper". `INDEPENDENT`
- **Self-hosting is at cost parity.** [S13]: "plain Gemma 4 26B on one EC2 L4 at full load cost at most $5.43 per
  million decisions, against Jev's $5.54 at the same 132-token prompts." `INDEPENDENT`
- **Checking the headline.** [S13]: "Averaged over all eight workflow setups, the figures are 97.8x faster and 149.2x
  cheaper". That is against 193.6x / 444.6x headlined. `INDEPENDENT` (arithmetic check of vendor data)

### 2.4 Robustness and adversarial behaviour

| Finding | Source | Label |
|---|---|---|
| "one unverified opinion appended to the state flips 12.1% of decisions, statistically tied with the strongest injected command (10.1%)" | JevAdvBench, arXiv 2609.31142 [S23] | `INDEPENDENT` |
| Optimized natural context "redirect[s] Jev on 312 of 508 initially correct decisions (61.4%)" | JevOut, arXiv 2609.30243 [S24] | `INDEPENDENT` |
| On the hosted model, swapping yes/no option names "changes AUC from .8146 to .5806"; "the type-error rate remains 0%, even when decision accuracy degrades substantially." | arXiv 2609.26758 [S26] | `INDEPENDENT` |
| Prompt injection success "rises from 1.8% to 3.5%" under adaptive attack; "schema-defined outputs change but do not eliminate prompt-injection risk" | arXiv 2609.28613 [S25] | `INDEPENDENT` |
| A router given bare option names "sent all 40 hard tasks to the cheap model, at a median confidence of 0.96" | review [S13] | `INDEPENDENT` (reported) |

### 2.5 Memory-system evidence (relevant to HLMemo)

- **Jev-Mem** (arXiv 2609.23986) uses Jev as the "System-One control plane" for agentic memory: typing, routing,
  retrieval budget, candidate scoring. On LoCoMo it reports "an overall LLM-as-a-Judge score of 0.777, an 11.0% relative
  improvement over the strongest baseline". It also reports "reducing memory construction time to 158 s, a 6.6×
  speedup". `INDEPENDENT` (a single unrefereed preprint; LLM-judge metric) [S28]
- **A GitHub issue** in another agent project proposes the same idea: "Research: use Jev (TypeSafe System One model)
  for low-cost judgement in routing, skill/memory recall, and guards". `INDEPENDENT` (intent, not a result) [S41]

---

## 3. Community reception

**Scale.** The Hacker News launch post "Introducing System One Models and Jev" had **1,984 points and 520 comments**
(Algolia API, 2026-09-15) [S31][S52]. arXiv 2609.30216 counted "2,170 publicly available Jev projects collected from
GitHub as of September 22, 2026" [S27]. `INDEPENDENT`

### Positive (practitioners)

- tylermarques, early access [S31 #49718890]: "IMHO This type of model works incredibly well in concert with LLMs, not
  as a replacement." `INDEPENDENT`
- lubujackson [S31 #49718626]: "After much fumbling around with prompts and evals, this is exactly how I am using LLMs in
  production, to narrowly make choices and return structured data." `INDEPENDENT`
- TechCrunch developer anecdotes [S9]:
  - Bryo AI CTO: "In his test, Gemini was slightly more accurate, but 10 to 20 times more expensive." `INDEPENDENT`
    (reported, not reproducible)
  - Vercel engineer: "When Vercel replaced OpenAI's Luna with Jev, it got results five to 18 times more quickly and
    with greater accuracy." `VENDOR-ECHO`-grade: Vercel resells Jev through AI Gateway, so this is a conflict of
    interest.
- Armin Ronacher (Earendil) on the trade-off [S9]: "At the end of the day, it delegates the hallucination problem a
  little bit to the user". `INDEPENDENT`

### Skeptical and negative

- jacobgold (top comment) [S31 #49718492]: "Sure, it can't emit an invalid type, but it can still emit a completely
  wrong valid value." `INDEPENDENT`
- ramon156 [S31 #49718120]: "This sounds good but so far all claims just sound like marketing terms. I'd love to see
  real proof." `INDEPENDENT`
- sonink [S31 #49727858]: "It does absolutely hallucinate - and seems to me that the claim is largely misleading."
  `INDEPENDENT`
- bregmandiv on prior art [S31 #49719883]: "We already had encoder models that skipped text generation for giving us a
  numerical output that could be computed as a probability." `INDEPENDENT`
- mortsnort on what it is [S31 #49722331]: "The documentation makes it sound like they're taking a pretrained LLM and
  then giving it their unique post-training." `INDEPENDENT`
- hbrn on the Doom demo (JevBench thread, 151 points) [S32]: "Don't you find it suspicious that nothing of the Doom
  demo was shared: no harness, no control loop, no state encoding, no prompts - nothing." `INDEPENDENT`
- prometheus1992 on launch language (Laya thread, 1,360 points) [S33]: "To me personally it seemed like a
  parody/con/shady at first." `INDEPENDENT`
- mushufasa on vendor onboarding [S31 #49718111]: "It's hard to justify adding new model vendors directly with all the
  heightened concerns about privacy and security". This is the reason to use it via OpenRouter. `INDEPENDENT`
- Flavio Copes, a well-known developer blogger whose "deep dive" is largely a restatement [S30]: "I have console access
  but haven't put Jev into production yet." `INDEPENDENT` (a caution about how much the popular "deep dives" add)

### Vendor responses on HN (the CEO posts as "CompleteSkeptic")

- [#49718824]: "architecture is close to the chest for now, but we have talked about writing a paper". `VENDOR-ECHO`
  (the vendor's own words)
- [#49718780]: "because these models are probabilistic, it's also possible to be confidently wrong". `VENDOR-ECHO`
- [#49718849] on constrained decoding: "constrained decoding (OpenAI-style structured outputs) make models dumber
  unfortunately". This is a vendor claim. It is partly supported by the literature (§4.2). `VENDOR-ECHO`
- A TypeSafe statement quoted on HN [S33 #49770857]: "Jev doesn't have deep knowledge of niche domains, but you can
  supply context to help it decide." `VENDOR-ECHO`

### Operations signals

- TechCrunch [S9]: "the company briefly lost the ability to serve users from its API because demand was so high."
  `INDEPENDENT`
- **Signups paused.** An unaffiliated news site, citing TypeSafe's X account [S39]: "TypeSafe posted at 06:19 UTC on
  September 22 that new signups are paused while existing accounts keep working." It says this still held on
  September 25. `INDEPENDENT` (secondary; I did not see the X post itself)
- **Fail-open integrations.** A GitHub issue in one integration [S40]: "Make Jev outages visible in the session;
  optional fail-closed mode for the danger gate". `INDEPENDENT`

### Coverage gaps

- **Reddit:** no relevant discussion surfaced. Queries tried: "reddit Jev TypeSafe System One model experience" and
  "reddit.com r/LocalLLaMA Jev TypeSafe" (only unrelated hits). Tavily search was rate-limited (HTTP 429) during this
  session.
- **X/Twitter:** no direct access. Mentions were seen only as links from HN or press (for example @CompleteSkeptic's
  launch video and @danshipper's early-user thread). None were read first-hand.

---

## 4. Background concepts

### 4.1 "System 1 vs System 2" in LLMs

- **The framing.** Kahneman's split, applied to LLMs: fast single-pass answers against deliberate reasoning (CoT,
  o1/R1-style). [S45]: "While System 1 excels in quick, heuristic decisions, System 2 relies on logical reasoning for
  more accurate judgments and reduced biases." `INDEPENDENT`
- **Where reasoning helps.** Only on some tasks. [S42]: "CoT gives strong performance benefits primarily on tasks
  involving math or logic, with much smaller gains on other types of tasks." `INDEPENDENT`
- **Where System 1 can be better.** [S43]: "state-of-the-art models exhibit significant performance drop-offs with CoT
  (up to 36.3% absolute accuracy for OpenAI o1-preview compared to GPT-4o)" on tasks where deliberation hurts humans.
  `INDEPENDENT`
- **Distillation.** Reasoning can be moved into fast models. [S44] reports "improved results compared to the original
  System 1 performance, and with less inference cost than System 2." `INDEPENDENT`
- **Consistency check.** Jev's evidence fits this literature. It competes on classification and judgment, where CoT
  adds little, and fails on temporal, numeric and derivation-checking items (JevBench 28% temporal/numeric; [S19]
  "Larger gaps arise when judgments require checking a derivation"). So "System One" is a real task split, not only
  branding. `INFERENCE`

### 4.2 "Type-safe" and structured-output LLMs

- **How other vendors guarantee schemas: constrained decoding.** At each step the decoder masks tokens the grammar
  forbids.
  - OpenAI: "we convert the supplied JSON Schema into a context-free grammar (CFG)" and "gpt-4o-2024-08-06 with
    Structured Outputs scores a perfect 100%" on their schema evals. There is a known cost: "the first request with a new
    schema incurs a latency penalty". [S46] `VENDOR-ECHO` (OpenAI's own claim)
  - Open tooling: Outlines builds "an index over a language model's vocabulary" [S47]; XGrammar "can achieve up to 100x
    speedup over existing solutions" [S48]. `INDEPENDENT`
- **Known pitfalls.**
  - *Quality loss:* [S50]: "we observe a significant decline in LLMs reasoning abilities under format restrictions."
    `INDEPENDENT`
  - *Distribution distortion:* [S51]: constrained decoding "can distort the LLM's distribution, leading to outputs that
    are grammatical but appear with likelihoods that are not proportional". `INDEPENDENT`
  - *Coverage and engineering gaps:* JSONSchemaBench evaluates six frameworks (Guidance, Outlines, llama.cpp, XGrammar,
    OpenAI, Gemini) and notes "there is poor understanding of the effectiveness of the methods in practice." [S49]. A
    practical limit was observed in the Jev study, where Anthropic's API refused a 78-option schema: "compiled grammar
    is too large" [S14]. `INDEPENDENT`
  - *Type-safe ≠ correct:* [S26]: "the type-error rate remains 0%, even when decision accuracy degrades substantially."
    `INDEPENDENT`
- **How Jev differs.** Jev avoids decoding entirely: a fixed answer shape is scored in one pass. The review names the
  prior art for that read-out: "end the prompt where the answer would start, take the model's scores for only the
  allowed label tokens, and apply a softmax over them" [S13]. It adds that "vLLM exposes it through
  `logprob_token_ids`, SGLang through `/v1/score`". So the schema guarantee is structural. The accuracy question is
  separate. `INDEPENDENT`

---

## 5. Red flags

1. **Unsubstantiated headline claims.**
   - [S10]: "These are company-generated results, not independent measurements."
   - [S11]: "The reference answer is the average of GPT-6 Astra and Fable 5.1." Also: "The 0% figure is not empirical.
     Answers can still be wrong."
   - [S13]: "TypeSafe states that it chose to publish no public-benchmark results." The Latent Space chapter is titled
     "Why TypeSafe Rejects Public Benchmarks" [S34].
   - `INDEPENDENT` / `VENDOR-ECHO` as marked.
2. **Undisclosed model and a possible fine-tune.**
   - [S9]: "Almeida is tight-lipped about the model's architecture, which outside observers suspect is built on top of
     an open-weight LLM." `INDEPENDENT`
   - [S11]: "TypeSafe has not published weights, a parameter count, or a self-hosting option." `INDEPENDENT`
   - The CEO on Latent Space [S34]:
     - A chapter is titled "Why Diogo Wouldn't Pre-Train with $1 Billion".
     - On model identity: "I am not going to put into the models that you are Jev from TypeSafe."
     - He agreed that all data is synthetic (Swyx: "all your data is synthetic." Almeida: "Yep.").
   - `VENDOR-ECHO`. My reading: this points to post-training an existing pretrained base, not pretraining from scratch.
     That is unconfirmed. `INFERENCE`
   - The open replicas reproduce the interface on Qwen3.5-4B or Gemma. The review says "Every piece of the mechanism"
     predates Jev [S13]. `INDEPENDENT`
3. **Price sustainability.** "We can't prove it isn't subsidized" (TypeSafe launch post, quoted in [S13]; [S11]:
   "TypeSafe says it cannot prove the price is unsubsidized."). `VENDOR-ECHO`
4. **The company is new but not obscure.**
   - Timeline: typesafe.ai was registered on 2024-05-07 (RDAP) [S38], out of stealth on 2026-09-15, with a $40M seed
     led by DCVC [S8][S12].
   - Forbes, via Wikipedia [S12]: "Forbes reported that the round valued TypeSafe at US$200 million."
   - [S10]: "The funding announcement does not disclose revenue, customers, ownership percentages or a formal
     valuation."
   - [S13]: "He is not an author of Christiano et al. (2017), which introduced
     it; he is a primary author of InstructGPT".
   - `INDEPENDENT`
5. **Privacy and data retention.**
   - Via OpenRouter: training false, prompt retention false, ZDR-listed, not HIPAA-eligible [S3][S4]. `INDEPENDENT`
   - The direct TypeSafe privacy policy says: "We will not train or fine tune any artificial intelligence or machine
     learning models on your prompts or other Input." [S35]
   - But its retention clause is vague: "We retain personal data about you for as long as reasonably necessary to
     provide you with the Services, or otherwise in support of our business or commercial purposes." [S35]
   - The site terms cap liability at "$100 USD" [S36].
   - I did not find separate API or commercial terms. `INDEPENDENT` (the documents) / `INFERENCE` (the "vague"
     judgement)
   - **Implication:** use the OpenRouter route, not the direct API, for anything sensitive. `INFERENCE`
6. **Lookalike, unaffiliated resellers.**
   - jevtypesafeai.com (registered 2026-09-18) sells "an instant hosted key from us" and states "Not affiliated with or
     endorsed by TypeSafe AI." [S37][S38]
   - thejevai.com (registered 2026-09-20) "has no stated connection to TypeSafe" [S13].
   - jevaiguide.com and jevainews.com were both registered 2026-09-18 [S38].
   - A naming collision: jev-router.com is run by JevBench's operators (see item 8) and is unrelated to OpenRouter's
     `typesafe/jev-router` [S16].
   - `INDEPENDENT`. Calling Jev through any of these would send data to an unknown third party. `INFERENCE`
7. **Operational maturity.**
   - One provider on OpenRouter, so no fallback [S2].
   - p99 of 13.4 s [S4].
   - An API overload during launch week [S9], and signups paused on 2026-09-22 [S39].
   - The alias `~typesafe/jev-latest` moves between releases, so pin `typesafe/jev-1.13`. The review also notes that
     the alias makes it hard to record which version answered [S1][S13].
   - `INDEPENDENT` / `INFERENCE`
8. **Conflicts of interest among evaluators.**
   - JevBench's operators run jev-router.com. [S16]: "Neutrality disclosure: it is run by the authors of this benchmark;
     it receives no scoring advantage and is not a ranked entrant."
   - Vercel, OpenRouter and LangChain distribute or integrate Jev (see [S9][S6] and the review [S13]).
   - The aggregate review was AI-assisted [S13].
   - `INDEPENDENT`

---

## What is still unknown

- Architecture, base model, parameter count, and the RLCD method. There is no paper, weights or model card.
- Whether the $0.042/M price lasts (subsidy).
- The retention period and subprocessors on the direct TypeSafe API. There is also no DPA or SLA that I could find.
- **Turkish performance.** No study measured it. Russian and Spanish degrade [S13].
- Behaviour near the 32k context limit, and with long, mostly irrelevant state. The vendor lists this as a weakness,
  but independent numbers are thin.
- How Jev Router actually works. It has no endpoints or usage yet, so there is no evidence either way.
- Whether results on public benchmarks transfer to HLMemo-shaped decisions:
  - memory placement
  - contradiction and duplicate detection
  - relevance gating for the librarian

  Only Jev-Mem [S28] touches this, as a single preprint.
- No leaderboard (Artificial Analysis, LMArena) covers it. JevBench is the only multi-system board, and its operator
  has a commercial interest.

---

## Appendix A — OpenRouter raw data (excerpts)

The full files are in `docs/research/jev/raw/`. They were fetched 2026-09-28, about 15:20–16:00 UTC.

`GET https://openrouter.ai/api/v1/models?output_modalities=all` returns the `typesafe/jev-1.13` record:

```json
{
 "id": "typesafe/jev-1.13",
 "canonical_slug": "typesafe/jev-1.13-20260917",
 "hugging_face_id": null,
 "name": "TypeSafe: Jev 1.13",
 "created": 1789689684,
 "description": "Jev is a structured decision model from TypeSafe, and the first of its System One models. System One models make fast, structured decisions for software, returning a typed choice rather...",
 "context_length": 32000,
 "architecture": {"modality": "text->decisions", "input_modalities": ["text"], "output_modalities": ["decisions"], "tokenizer": "Other", "instruct_type": null},
 "pricing": {"prompt": "0.000000042", "completion": "0"},
 "top_provider": {"context_length": 32000, "max_completion_tokens": 28800, "is_moderated": false},
 "per_request_limits": null,
 "supported_parameters": [],
 "default_parameters": {},
 "knowledge_cutoff": null,
 "expiration_date": null,
 "links": {"details": "/api/v1/models/typesafe/jev-1.13-20260917/endpoints"}
}
```

The same query returns the alias `~typesafe/jev-latest`:

```json
{"id": "~typesafe/jev-latest", "alias_target": {"name": "TypeSafe: Jev 1.13", "slug": "typesafe/jev-1.13"},
 "created": 1789689685, "context_length": 32000,
 "architecture": {"modality": "text->decisions", "tokenizer": "Router"},
 "pricing": {"prompt": "0.000000042", "completion": "0"}, "supported_parameters": []}
```

It also returns the router `typesafe/jev-router`, the only one of the three in the default list:

```json
{"id": "typesafe/jev-router", "created": 1790363560, "context_length": 1000000,
 "architecture": {"modality": "text+image+file+audio+video->text", "tokenizer": "Router"},
 "pricing": {"prompt": "-1", "completion": "-1"}, "supported_parameters": [],
 "top_provider": {"context_length": null, "max_completion_tokens": null, "is_moderated": false}}
```

`GET https://openrouter.ai/api/v1/models/typesafe/jev-1.13/endpoints`. The alias and the router return
`"endpoints": []`.

```json
{"name": "TypeSafe | typesafe/jev-1.13-20260917", "model_id": "typesafe/jev-1.13", "context_length": 32000,
 "pricing": {"prompt": "0.000000042", "completion": "0", "discount": 0},
 "provider_name": "TypeSafe", "tag": "typesafe", "quantization": "unknown",
 "max_completion_tokens": 28800, "max_prompt_tokens": null, "supported_parameters": [],
 "supports_tool_choice": {"none": true, "auto": true, "required": true, "function": true},
 "status": 0, "uptime_last_30m": 100, "uptime_last_5m": 100, "uptime_last_1d": 100,
 "supports_implicit_caching": false, "latency_last_30m": null, "throughput_last_30m": null}
```

`GET https://openrouter.ai/api/v1/endpoints/zdr` (917 endpoints) contains the same endpoint object with
`"tag": "typesafe"`.

Model page `https://openrouter.ai/typesafe/jev-1.13`, from the embedded state:

```json
{"endpoint_stats": {"p50_latency": 242.5, "p75_latency": 314, "p90_latency": 1191, "p95_latency": 2203.45,
  "p99_latency": 13422.49, "request_count": 2945627, "window_minutes": 30, "p50_throughput": null},
 "uptime_daily": [{"date": "2026-09-28", "uptime": 98.32}, {"date": "2026-09-27", "uptime": 100}, {"date": "2026-09-26", "uptime": 100}],
 "provider_dataPolicy": {"training": false, "trainingOpenRouter": false, "retainsPrompts": false, "canPublish": false,
  "termsOfServiceURL": "https://typesafe.ai/legal/terms", "privacyPolicyURL": "https://typesafe.ai/legal/privacy-policy"},
 "provider_baseUrl": "https://api.typesafe.ai/v1", "provider_model_id": "jev-1.13.0",
 "limit_rpm": 2000, "is_hipaa_eligible": false, "supports_reasoning": false, "supports_tool_parameters": false,
 "benchmarkScores": {"scores": [], "lookback_days": 32}, "artificialAnalysis": [], "designArena": {"records": []}}
```

Other `decisions`-modality models on OpenRouter, for context:

| id | created | $/1M input | context |
|---|---|---|---|
| jaredpalmer/kev-4b | 2026-09-25 | 0.042 | 8,192 |
| respan/span-01 | 2026-09-26 | 0.02 | 0 |
| respan/span-01-lite (+ `:free`) | 2026-09-26 | 0 | 0 |
| upstage/solar-decide | 2026-09-28 | 0.05 | 524,288 |

---

## Sources

**OpenRouter**
- S1 — OpenRouter models API: https://openrouter.ai/api/v1/models (and `?output_modalities=all`)
- S2 — OpenRouter endpoints API: https://openrouter.ai/api/v1/models/typesafe/jev-1.13/endpoints (also `~typesafe/jev-latest`, `typesafe/jev-router`)
- S3 — OpenRouter ZDR endpoint list: https://openrouter.ai/api/v1/endpoints/zdr
- S4 — OpenRouter model page: https://openrouter.ai/typesafe/jev-1.13
- S5 — OpenRouter Jev Router page: https://openrouter.ai/typesafe/jev-router
- S6 — OpenRouter Jev guide: https://openrouter.ai/docs/guides/community/jev
- S7 — OpenRouter cookbook, Jev-verified cascade: https://openrouter.ai/docs/cookbook/evaluate-and-optimize/jev-verified-cascade

**Press and reference**
- S8 — The Register (Claburn, 2026-09-16): https://www.theregister.com/ai-and-ml/2026/09/16/typesafe-ai-debuts-model-for-machines-that-plays-doom/5296711
- S9 — TechCrunch (Fernholz, 2026-09-18): https://techcrunch.com/2026/09/18/a-new-kind-of-ai-model-from-a-chatgpt-inventor-is-thrilling-developers/
- S10 — TechStock² (2026-09-17): https://ts2.tech/en/typesafe-ai-raises-40-million-for-jev-but-its-445x-cost-claim-is-still-self-tested/
- S11 — MarkTechPost (2026-09-19): https://www.marktechpost.com/2026/09/19/typesafe-ai-releases-jev/
- S12 — Wikipedia, "Jev (AI model)": https://en.wikipedia.org/wiki/Jev_(AI_model)

**Evaluations and benchmarks**
- S13 — xbill, "Jev After Eight Days of Independent Tests" (dev.to, 2026-09-24): https://dev.to/aws-builders/jev-after-eight-days-of-independent-tests-level-with-mid-price-llms-behind-the-frontier-1c60 — full report: https://github.com/xbill9/gemma4-dev/tree/main/jev
- S14 — Janardhan, jev-frontier-bench: https://github.com/manjunathshiva/jev-frontier-bench
- S15 — jev-phishing-bench: https://github.com/anisselbd/jev-phishing-bench
- S16 — JevBench v1.4.2.2, Benchmark Heaven: https://benchmarkheaven.com/jev-models

**arXiv preprints on Jev**
- S17 — Ibrahim & Zaki, arXiv 2609.24574: https://arxiv.org/abs/2609.24574
- S18 — Rao & Callison-Burch, arXiv 2609.29769: https://arxiv.org/abs/2609.29769
- S19 — Li, Miao, Krishnan, arXiv 2609.26550: https://arxiv.org/abs/2609.26550
- S20 — Rafe & Das, arXiv 2609.24052: https://arxiv.org/abs/2609.24052
- S21 — Zhang et al., arXiv 2609.27678: https://arxiv.org/abs/2609.27678
- S22 — Huang et al., arXiv 2609.27607: https://arxiv.org/abs/2609.27607
- S23 — JevAdvBench, arXiv 2609.31142: https://arxiv.org/abs/2609.31142
- S24 — JevOut, arXiv 2609.30243: https://arxiv.org/abs/2609.30243
- S25 — Decision Hijacking, arXiv 2609.28613: https://arxiv.org/abs/2609.28613
- S26 — "Type-Safe Is Not Error-Free", arXiv 2609.26758: https://arxiv.org/abs/2609.26758
- S27 — "Jev in the Wild", arXiv 2609.30216: https://arxiv.org/abs/2609.30216
- S28 — Jev-Mem, arXiv 2609.23986: https://arxiv.org/abs/2609.23986

**Practitioner posts and interviews**
- S29 — Classmethod DevelopersIO, Jev for model routing: https://dev.classmethod.jp/en/articles/jev-for-llm-model-routing/
- S30 — Flavio Copes, "A deep dive into Jev": https://flaviocopes.com/jev/
- S31 — HN launch thread: https://news.ycombinator.com/item?id=49717558 — comments cited by id: https://news.ycombinator.com/item?id=<id>
- S32 — HN JevBench thread comment: https://news.ycombinator.com/item?id=49816019
- S33 — HN Laya thread comments: https://news.ycombinator.com/item?id=49767192 , https://news.ycombinator.com/item?id=49770857
- S34 — Latent Space, "Jev: System One models for Prod, not God": https://www.latent.space/p/jev

**TypeSafe legal, domains and operations**
- S35 — TypeSafe privacy policy: https://typesafe.ai/legal/privacy-policy
- S36 — TypeSafe terms: https://typesafe.ai/legal/terms
- S37 — jevtypesafeai.com (unaffiliated): https://jevtypesafeai.com/
- S38 — RDAP registrations: https://rdap.org/domain/typesafe.ai , /jevtypesafeai.com , /thejevai.com , /jevaiguide.com , /jevainews.com
- S39 — Jev News (unaffiliated), signups paused: https://jevainews.com/news/typesafe-signups-paused/
- S40 — GitHub issue, the-jev-enator #36: https://github.com/jakenbear/the-jev-enator/issues/36
- S41 — GitHub issue, rockbot #601: https://github.com/MarimerLLC/rockbot/issues/601

**Background: System 1 / System 2**
- S42 — Sprague et al., "To CoT or not to CoT?", arXiv 2409.12183: https://arxiv.org/abs/2409.12183
- S43 — Liu et al., "Mind Your Step (by Step)", arXiv 2410.21333: https://arxiv.org/abs/2410.21333
- S44 — Yu, Xu, Weston, "Distilling System 2 into System 1", arXiv 2407.06023: https://arxiv.org/abs/2407.06023
- S45 — Li et al., "From System 1 to System 2" survey, arXiv 2502.17419: https://arxiv.org/abs/2502.17419

**Background: structured outputs**
- S46 — OpenAI, "Introducing Structured Outputs in the API" (2024-08-06; read via the Wayback Machine because openai.com returned 403): https://openai.com/index/introducing-structured-outputs-in-the-api/
- S47 — Willard & Louf, "Efficient Guided Generation for LLMs" (Outlines), arXiv 2307.09702: https://arxiv.org/abs/2307.09702
- S48 — XGrammar, arXiv 2411.15100: https://arxiv.org/abs/2411.15100
- S49 — JSONSchemaBench, arXiv 2501.10868: https://arxiv.org/abs/2501.10868
- S50 — Tam et al., "Let Me Speak Freely?", arXiv 2408.02442: https://arxiv.org/abs/2408.02442
- S51 — Park et al., "Grammar-Aligned Decoding", arXiv 2405.21047: https://arxiv.org/abs/2405.21047

**Tools**
- S52 — HN Algolia API, thread metadata and points: https://hn.algolia.com/api/v1/items/49717558
