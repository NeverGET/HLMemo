# Jev (TypeSafe AI) — 01 Primary sources (vendor + platform documentation)

Compiled 2026-09-28. All pages fetched on 2026-09-28. No model API was called and no money was spent.

**Labels**
- `DOC-FACT`: a technical specification in the documentation (API shape, limits, IDs, prices, versions).
- `VENDOR-CLAIM`: marketing or self-reported statements (speed, quality, positioning, "can't hallucinate", evals). Vercel/OpenRouter/Cloudflare statements that only restate TypeSafe claims are also marked `VENDOR-CLAIM`.
- `DERIVED`: my own arithmetic or reading of a chart. The input source is still cited.

Quotes are verbatim and ≤ 30 words. `…` marks an elision inside a quote. Markdown formatting from the source, such as bold, italics and links, was removed from the quotes.

**Access notes (how each page was read)**
- vercel.com pages: tavily extract, plus the `.md` variants that Vercel serves (e.g. `…/evaluation.md`). The AI Gateway model page's provider table extracted mostly empty: Context, Latency and similar showed "—". I used the public Gateway model API (`ai-gateway.vercel.sh/v1/models`, `/v1/models/typesafe-ai/jev/endpoints`) as the second route.
- docs.typesafe.ai is Mintlify. I read the `llms.txt` index and fetched about 35 pages as raw `.md` with curl.
- typesafe.ai (home and blog) is a Framer site. The FAQ answers are JS accordions and are not in the static HTML, so I rendered and expanded each one in headless Playwright. The benchmark charts on the blog are images; I downloaded them and read them visually (marked `DERIVED`).
- evals.typesafe.ai is static HTML. The per-point labels hold the exact numbers.
- Press release: the Morningstar copy returned 403. I read the same Business Wire text on Yahoo Finance.
- OpenRouter: docs `.md` pages, the model page's HTML metadata, and the public `/api/v1/models/typesafe/jev-1.13/endpoints`.

---

## 1. What is Jev? Who makes it? What do "System One" and "TypeSafe" mean?

### What it is
- Jev is TypeSafe's flagship model and its first "System One" model. `DOC-FACT` — https://docs.typesafe.ai/introduction — "Jev is TypeSafe’s flagship model and the first System One model. Send state and typed questions; get structured answers your code can use directly."
- It is not a chat or text model. It returns typed decisions and probabilities. `DOC-FACT` — https://docs.typesafe.ai/concepts/system-one — "Like an LLM, a System One model understands natural-language input. It returns typed decisions and probabilities rather than generated text."
- Vercel's framing: `VENDOR-CLAIM` (platform restatement) — https://vercel.com/i/what-is-jev — "Jev is TypeSafe AI's System One model, built to return typed decisions with probabilities from supplied state and questions."
- Vercel's product copy calls it an "evaluation model". `DOC-FACT` — https://vercel.com/ai-gateway/models/jev — "Jev is TypeSafe AI’s System One evaluation model for fast, structured decisions in software."
- The vendor's one-line pitch: `VENDOR-CLAIM` — https://typesafe.ai/blog/introducing-system-one-models-and-jev — "Think of Jev as a frontier-intelligence function call: unstructured state in, typed probabilistic decisions out."

### Who makes it: company, people, funding, dates
- **Legal entity and address.** `DOC-FACT` — https://typesafe.ai/legal/terms — "The Site is offered by TypeSafe AI, Inc., located at 255 California St, Suite 1300, San Francisco, CA 94117." The terms are governed by Delaware law: "These Terms are governed by the laws of the state of Delaware".
- **Founding, HQ, seed round.** `VENDOR-CLAIM` (company press release, Business Wire) — https://finance.yahoo.com/technology/ai/articles/typesafe-ai-emerges-stealth-40m-190000776.html — "today emerged from stealth with $40 million in seed funding led by DCVC." It also says: "Founded in 2024 and headquartered in San Francisco".
- **Founders.** Same press release — "Founded by former OpenAI researcher and co-inventor of RLHF/ChatGPT, Diogo Almeida, with Erik Gafni and Sasha Sheng".
- **Roles, from the team page labels.** `VENDOR-CLAIM` — https://typesafe.ai/team — "CEO … Diogo Almeida … COO … Sasha Sheng … CTO … Erik Gafni".
  - Diogo Almeida: "Diogo co-invented RLHF and InstructGPT, the methods that lead to ChatGPT and GPT4. Previously, he was at Google Brain."
  - Sasha Sheng: "Sasha is an ex-research engineer from Meta/FAIR where she worked on the News Feed, AI Experiences, and AI Research."
  - Erik Gafni: "Erik is a repeat founder (Ravel, multi-modal AI for dna-sequencing), an early employee at two unicorns (Invitae and Freenome)".
  - The docs repeat the RLHF claim. https://docs.typesafe.ai/introduction/machine-learning-primer — "RLHF was used to train InstructGPT and ChatGPT and was co-invented by Diogo Almeida, cofounder of TypeSafe."
- **Investors.**
  - Only DCVC is named, with a quote from DCVC General Partner James Hardiman in the press release: "said James Hardiman, General Partner at DCVC."
  - The team page does not name any investors. `VENDOR-CLAIM` — https://typesafe.ai/team — "We're backed by top-tier investors who share our vision for building the foundation of truly transformative AI."
- **Launch date.** The launch post (Diogo Almeida, "founder, TypeSafe") is dated Sep 15, 2026. `DOC-FACT` — https://typesafe.ai/blog/introducing-system-one-models-and-jev — "After two years in stealth, countless technical challenges, and research breakthroughs… I am beyond excited to announce that today, TypeSafe AI is releasing our first System One Model".
- **Launch press release** is dated the same day: "SAN FRANCISCO, September 15, 2026--(BUSINESS WIRE)--".
- **GitHub org** `typesafe-ai` was created 2024-05-28. `DOC-FACT` — https://api.github.com/orgs/typesafe-ai — `"created_at":"2024-05-28T20:57:18Z"`.
- **Company mission.** `VENDOR-CLAIM` — https://typesafe.ai/manifesto — "Our mission is to pave the shortest path to an AI-based economic revolution by making intelligence composable to catalyze a Cambrian explosion of intelligent software." Slogan: "We're building prod, not God."

### "System One": an explicit reference to Kahneman
- **Yes, the reference is explicit.** `VENDOR-CLAIM` (naming rationale) — https://typesafe.ai/blog/introducing-system-one-models-and-jev (FAQ, rendered) — "We were inspired by Daniel Kahneman, Thinking, Fast and Slow. The model class name draws on the distinction between fast, intuitive System 1 thinking and slow, deliberate System 2 reasoning."
- The docs say the same. `DOC-FACT` — https://docs.typesafe.ai/concepts/system-one — "The System One name comes from the concept Daniel Kahneman popularized in his book Thinking, Fast and Slow." Also: "Here, the emphasis is on fast, focused judgments."
- **They depart from Kahneman on reliability.** Kahneman's System 1 is error-prone; TypeSafe claims its System One can be more reliable. Same blog FAQ — "“System 1 thinking” has also implied error-prone. For reasons we will get into in the future, we believe System One Models can be made more reliable than its alternatives."
- **Vercel's reading of the term.** https://vercel.com/i/what-is-jev — "System One is TypeSafe's name for models that make focused, typed decisions. It refers to the fast-thinking side of Kahneman's distinction".
- **Origin of the name "Jev".** Blog FAQ — "We named Jev after William Stanley Jevons." The press release says: "Jev, a nod to Jevons Paradox, is TypeSafe's first model built around this approach."

### What "TypeSafe" means for outputs
- **Outputs are schema-constrained typed values; no type errors are claimed.** `VENDOR-CLAIM` — https://typesafe.ai/blog/introducing-system-one-models-and-jev (comparison table) — "Type-safe structured values. Possible outputs and structure are defined in advance. The model never makes type errors."
- **"Mathematically impossible" to break the schema.** `VENDOR-CLAIM`, same page — "No type errors: This would be an easy thing to falsify with just a single counter-example, but it is mathematically impossible."
- **Structure only, not correctness.** `VENDOR-CLAIM` — https://typesafe.ai (FAQ "Can Jev still get things wrong?", rendered):
  - "Jev guarantees the shape of its answers, not that every decision is correct."
  - "it can’t invent a category outside that list, but it can choose the wrong one."
- **Vercel's framing.** https://vercel.com/i/what-is-jev — "TypeSafe says Jev's outputs conform to the defined schema. That guarantee concerns answer structure. Semantic correctness still needs evaluation."
- **The "zero hallucination" claim equals the schema guarantee.** It is not measured. `VENDOR-CLAIM` — blog, Hallucination section — "Our number is not empirical. Schema matching is guaranteed, thus we can confidently add 0% into the plots." The launch post also says Jev "can’t hallucinate": "While Jev gives up string generation, it’s optimized for structured outputs and can’t hallucinate."

---

## 2. How does it work?

### Architecture and training (claims only; no paper or technical report found)
- **New architecture, parallel sampler, new RL method (RLCD).** `VENDOR-CLAIM` — https://typesafe.ai/blog/introducing-system-one-models-and-jev — "We built a new stack entirely focused on automation: with a new model architecture, parallel sampler for maximum efficiency, and training method we call Reinforcement Learning for Calibrated Decisions (RLCD)."
- **"Neither small nor an LLM."** No parameter count or architecture is disclosed. `VENDOR-CLAIM` — https://typesafe.ai (FAQ, rendered) — "Jev is neither small nor an LLM, hence being off the intelligence Pareto curve."
- **Parallel instead of autoregressive decoding.** `VENDOR-CLAIM`:
  - Blog table: "Parallel. Generates all outputs in a single query. Incredibly efficient and hardware-aware."
  - Home FAQ: "Jev replaces sequential generation with parallel computation, answering multiple structured questions in a single request."
- **What RLCD optimizes.** `DOC-FACT` (describes the objective; the method itself is not disclosed) — https://docs.typesafe.ai/introduction/machine-learning-primer:
  - "Reinforcement learning for calibrated decisions trains TypeSafe to return decisions and calibrated probabilities instead of generated text."
  - "Higher probability should correspond to a greater chance that the answer is correct."
- **Why a new algorithm (vs RLHF/RLVR).** `VENDOR-CLAIM` — blog FAQ "Why was a new training algorithm needed?" (rendered):
  - "That was the right task for a chat product, but it is the wrong task for automation."
  - "RLVR is great for tasks with simple programmatic verification, but most real-world judgement tasks don’t fit into that shape."
- **Training data.** `VENDOR-CLAIM` — blog FAQ (rendered) — "TypeSafe is primarily a data research lab, which is how the biggest results in AI get made. We make all the data ourselves."
- **Same weights for every customer; no customer fine-tuning.** `DOC-FACT` — https://docs.typesafe.ai/models — "Jev is not fine-tuned or LoRA-adapted with customer data." Also: "the same weights serve every account."
- **Not trained on customer traffic.** `DOC-FACT` (policy) — https://docs.typesafe.ai/models — "Jev is not trained on customer requests or responses."

### Interface (DOC-FACT)
- **Endpoint.** https://docs.typesafe.ai/api — `POST https://api.typesafe.ai/v1/systemone` with `Authorization: Bearer <API_KEY>`. The request has `state` (string | object | array), `model` and `questions` (a map).
- **Three question types ("primitives").** https://docs.typesafe.ai/introduction — Choice returns "`choice`, `probabilities`, `confidence`"; Score returns "`score`, `probabilities`, `confidence`"; Noul returns "`noul` (0–1)".
  - Noul is a yes/no question: https://docs.typesafe.ai/primitives/noul — "A Noul question asks the TypeSafe model to evaluate a yes/no question and return the probability that the answer is yes."
  - Vercel and the AI SDK call the Noul type **`boolean`**, with the answer field **`probability`**. https://vercel.com/docs/ai-gateway/modalities/evaluation — "Returns a probability between 0 and 1."
- **Limits per question.**
  - https://docs.typesafe.ai/api — "You can have a maximum of 255 options per Choice."
  - Score: "A Score should have at least two levels; the API accepts up to 10."
- **Confidence.** Confidence is a statistic of the returned distribution; Noul has none. https://docs.typesafe.ai/confidence — "`confidence` is a statistic computed from the probability distribution the answer already gives you." Also: "(Noul answers don't carry one.)"
- **Independent, parallel evaluation of questions.** https://docs.typesafe.ai/introduction — "Every question is evaluated in parallel and in isolation against the same state in one go. Adding questions barely changes the response time."
- **Question IDs are not seen by the model.** https://docs.typesafe.ai/api — "The key is not sent to the underlying model and is not used in inference."
- **Structured instructions and criteria.** Instructions and criteria may be JSON objects or arrays; the question text refers to `state` fields in backticks. https://docs.typesafe.ai/api — "The `instructions` property can be a string, an object, or an array."
- **Model versions and aliases.** https://docs.typesafe.ai/models
  - Current model: "Jev 1.13 | `jev-1.13.0`".
  - Aliases: `jev-latest` points to `jev-1.13.0` and is "The default in our client SDKs"; `jev-preview` also points to `jev-1.13.0`.
  - Warning: "An alias moves when a new release ships, so the answers behind it can change without a change on your side."

### Modalities, context, language
- **Input is text only.** `DOC-FACT` — https://docs.typesafe.ai/models — "Text only. String, JSON object, or array of text values. No image, audio, or video input."
- **Context length (TypeSafe).** `DOC-FACT` — https://docs.typesafe.ai/models — "64k tokens per request; 32k tokens for `state` plus the longest question".
- **Context length on the platforms.** The listings give 32,000 (see Open questions).
  - `DOC-FACT` — https://ai-gateway.vercel.sh/v1/models — `"context_window": 32000`. The description reads: "Limits: 64,000 tokens total per request; 32,000 tokens for the state plus the longest question."
  - Cloudflare — https://developers.cloudflare.com/ai/models/typesafe/jev/ — "Context Window … 32,000 tokens".
  - OpenRouter — https://openrouter.ai/docs/guides/community/jev.md — "32,000 tokens. That's the `state` you send plus the questions."
- **Language.** `DOC-FACT` — https://docs.typesafe.ai/models — "English is the primary training language and where accuracy is currently best. Other languages, including CJK scripts, are handled but not equally well".

### Output guarantees, determinism, streaming, tool use
- **Typed outputs.** Answers are typed values keyed by your question IDs. Choice probabilities sum to 1 (`DOC-FACT`, https://docs.typesafe.ai/api — "Every option mapped to its probability (floats that sum to 1).").
- **AI SDK validates native answers and rejects invalid ones.** `DOC-FACT` — https://ai-sdk.dev/docs/ai-sdk-core/evaluation — "Invalid output is rejected; native values are preserved, never silently normalized."
- **Determinism is not promised; "consistency" is.** `VENDOR-CLAIM`:
  - https://typesafe.ai (FAQ, rendered) — "Determinism means returning the same result for an identical input. This is less valuable than consistency." Also: "Jev is designed for consistency."
  - https://docs.typesafe.ai/model-jaggedness/jev-1.13 — "`jev-1.13` is extremely consistent, meaning you should expect quantitatively similar outputs for semantically similar inputs."
- **No streaming.** `DOC-FACT`:
  - https://ai-gateway.vercel.sh/v1/models (Jev description) — "No streaming or output-token limit."
  - https://ai-sdk.dev/docs/ai-sdk-core/evaluation — "It does not stream answers, perform multilabel classification, or batch unrelated states."
- **No tool calling, no text generation.** `DOC-FACT` — https://docs.typesafe.ai/introduction/coding-agents — "Coding agents rely on an LLM that streams text, calls tools, and edits files based on natural-language instructions. Jev does none of that."
- **Function calling is done by mapping.** Instead of native tool calls, the vendor cookbook maps function names and closed-set arguments to Choice questions. `DOC-FACT` — https://docs.typesafe.ai/llms.txt — "Turns natural-language trading requests into calls to ordinary typed functions by mapping function names and closed-set arguments to confidence-aware TypeSafe questions."

### Latency and throughput
- **Blog latency claim.** `VENDOR-CLAIM` — https://typesafe.ai/blog/introducing-system-one-models-and-jev — "End-to-end response time is 70ms-500ms for TypeSafe. This can range from 40x-200x faster for the same levels of frontier intelligence for System One shaped queries."
- **Press release latency claim.** `VENDOR-CLAIM` — press release — "The model delivers frontier-level intelligence at less than 100 milliseconds of latency and is up to 100 times faster and less expensive than other frontier models."
- **Home page demo.** `VENDOR-CLAIM` — https://typesafe.ai — Jev "Cost $0.000081 Completed in 0.114s"; the LLMs "Cost $0.013880 Completed in 8.566s".
- **Batching one request vs 13 separate requests.** `VENDOR-CLAIM` (vendor cookbook output) — https://docs.typesafe.ai/cookbooks/parallel_questions — "one call, all 13 1 $0.000497 0.27s" vs "13 calls, one each 13 $0.006090 2.71s".
- **Rate limits (throughput ceiling).** `DOC-FACT` — https://docs.typesafe.ai/models — "250,000 tokens per second / 1,200 requests per minute".
  - The same page warns: "Rate limits are adjusting dynamically." Also: "the limits above can change without notice".
- **Where the service runs.** `VENDOR-CLAIM` — blog — "our published evals are generally run from our laptops on the West Coast (this is where our service is currently based)."

---

## 3. Why does it exist? Problem and positioning

- **The founding question.** `VENDOR-CLAIM` — https://typesafe.ai/blog/introducing-system-one-models-and-jev — "Models have been superhuman at chat for years, so where is all the automation?"
- **The problem: text generators are coerced into decisions.** `DOC-FACT` (docs framing) — https://docs.typesafe.ai/introduction — "you are coercing a text-generation system into outputting structured decisions, then parsing the results back into something your code can depend on."
- **Machine-to-machine thesis.** `VENDOR-CLAIM` — https://docs.typesafe.ai/introduction/machine-learning-primer:
  - "large-scale automation will be dominated by AI-to-AI and AI-to-software interactions".
  - "closer to 99% machine-to-machine interactions and 1% human interaction."
- **Critique of RLHF chat models.** `VENDOR-CLAIM` — https://typesafe.ai — "RLHF creates inherent issues such as mode dropping, overconfidence, and lack of reliability. These flaws mean that LLMs require humans-in-the-loop."
- **Calibrated confidence as the key to automation.** `VENDOR-CLAIM` — blog table — "If a model can do a task 95% of the time but doesn’t say when it’s in the 5%, it can’t automate that task."
- **Vs JSON mode and structured outputs.** `VENDOR-CLAIM` — https://typesafe.ai (FAQ, rendered) — "Valid JSON gives software a format it can read. But forcing an LLM into that format can leave some of its intelligence on the table."
- **Vs reasoning ("System 2") models.** Jev is positioned for fast, common-sense judgments; extended reasoning should go elsewhere. `VENDOR-CLAIM` — https://typesafe.ai (FAQ, rendered) — "Some tasks requiring extended reasoning, such as complex mathematics or chess-like planning, may be better suited to large reasoning models."
  - Escalation path: https://docs.typesafe.ai/concepts/system-one — "you can decide when to act and when to escalate to a person or a reasoning model."
- **Decompose, then compose in code.** `DOC-FACT` (usage guidance) — https://docs.typesafe.ai/introduction — "If the question you want to ask would require extended reasoning or weighs multiple independent factors, decompose it."
- **Manifesto framing.** `VENDOR-CLAIM` — https://typesafe.ai/manifesto — "the bottleneck isn't raw intelligence. It's that today's intelligence is hard to build on."
- **Vercel's positioning vs a general LLM.** `DOC-FACT` (platform guidance) — https://vercel.com/i/jev-vs-gpt-6-astra — "Use GPT-6 Astra when the task also requires generating content or working through a broader problem with tools."
- **The AI SDK's statement on differences from LLM adapters.** `DOC-FACT` — https://ai-sdk.dev/docs/ai-sdk-core/evaluation — "The language-model adapters evaluate all questions in one prompt. They do not provide TypeSafe's native independent-question execution semantics."

---

## 4. Where is it meant to be used? Use cases and integrations

### Use cases (as stated)
- **Vendor list.** `VENDOR-CLAIM` — https://typesafe.ai/blog/introducing-system-one-models-and-jev (use-case row):
  - "AI-Powered Workflows / smart if-statements."
  - "Map-reducing over big data."
  - "Real-time applications."
  - "Verify everything. Score, judge, verify, guardrail, and detect jailbreaks of LLM prompts, reasoning traces, and/or outputs."
- **Vercel list.** `VENDOR-CLAIM` (platform) — https://vercel.com/changelog/typesafe-ai-jev-now-available-on-ai-gateway:
  - "Choosing the next tool or subagent in an agent loop"
  - "Deciding whether to continue, retry, ask the user, or stop"
  - "Scoring urgency or risk before an action"
  - "Verifying model outputs and enforcing guardrails."
- **Vendor cookbooks.** `DOC-FACT` — https://docs.typesafe.ai/llms.txt lists re-ranking, LLM guardrails, citation checking, function calling, hierarchical classification, SDE cascade, date extraction, RAG passage classification and skill suggestion. Example: "Screen every message going into and out of an LLM app with one TypeSafe request".
- **Not a coding-agent LLM.** `DOC-FACT` — https://docs.typesafe.ai/introduction/coding-agents — "Jev is not a drop-in replacement for the LLM behind Claude Code, Cursor, opencode, Copilot, Muse Spark, Grok Bot, or similar tools."
- **Demos.** `VENDOR-CLAIM`, same blog:
  - Doom bot: "The engineer behind it was worried about making 10 queries a second (which ends up costing ~$7/hour)".
  - Wikiracing: "Jev supports a cardinality up to 255."

### Integrations (DOC-FACT unless noted)

| Path | Model ID | How it is called | Source |
|---|---|---|---|
| TypeSafe direct (HTTP) | `jev-latest` / `jev-1.13.0` | `POST https://api.typesafe.ai/v1/systemone` | https://docs.typesafe.ai/api |
| TypeSafe Python SDK | default `jev-latest` | `pip install typesafe-sdk` (Python >= 3.10) | https://docs.typesafe.ai/introduction/quickstart |
| TypeSafe JS/TS SDK | default `jev-latest` | `npm install @typesafe-ai/sdk` (Node.js 20+) | https://docs.typesafe.ai/sdk/javascript |
| Vercel AI Gateway, AI SDK | `typesafe-ai/jev` | `experimental_evaluate` from `ai` (AI SDK 7) | https://vercel.com/docs/ai-gateway/modalities/evaluation |
| Vercel AI Gateway, HTTP | `typesafe-ai/jev` | `POST https://ai-gateway.vercel.sh/v1/evaluate` | same |
| Vercel AI Gateway, TypeSafe-compatible | `typesafe-ai/jev` | base URL `https://ai-gateway.vercel.sh/typesafe` (`/v1/systemone`, `/v1/models`) | https://vercel.com/docs/ai-gateway/sdks-and-apis/typesafe |
| AI SDK, direct provider | `jev-latest` | `typeSafeAi.evaluationModel('jev-latest')` from `@ai-sdk/typesafe-ai` | https://ai-sdk.dev/docs/ai-sdk-core/evaluation |
| Cloudflare Workers AI | `typesafe/jev` | `env.AI.run('typesafe/jev', …)` or REST `/ai/run` | https://developers.cloudflare.com/ai/models/typesafe/jev/ |
| OpenRouter | `typesafe/jev-1.13`, alias `~typesafe/jev-latest` | `POST https://openrouter.ai/api/alpha/decisions` or `POST https://openrouter.ai/api/v1/systemone` | https://openrouter.ai/docs/guides/community/jev.md |
| TanStack AI | per provider | `decide()` with `typesafeDecider` / `vercelGatewayDecider` / `cloudflareDecider` / `openRouterDecider('~typesafe/jev-latest')` | https://vercel.com/i/jev-integrations |
| LangChain (Python) | — | `TypeSafeClassifier`, package `langchain-typesafe` | https://vercel.com/i/jev-integrations |
| eve (Vercel agent framework) | default evaluation model | evaluation, model selection, tool approvals | https://vercel.com/changelog/ai-gateway-now-supports-typesafe-clients-and-http-api-for-jev |
| Coding-agent skill | — | `npx skills add typesafe-ai/skills --skill typesafe-ai` | https://docs.typesafe.ai/introduction/quickstart |

Supporting quotes:
- **AI SDK version requirement.** https://vercel.com/changelog/typesafe-ai-jev-now-available-on-ai-gateway — "AI SDK 7.0.105 onwards supports the `evaluate` API".
- **Gateway endpoints that do not work.** https://vercel.com/docs/ai-gateway/modalities/evaluation — "It is not supported through the OpenAI-compatible, Anthropic-compatible, or Cohere-compatible endpoints."
- **Evaluation fallbacks.** The Gateway can escalate uncertain answers to another model. Same page — "Evaluation fallbacks can rerun a successful evaluation with another model when a Choice or Score has low confidence, a Boolean probability falls inside an uncertain band".
- **eve.** `VENDOR-CLAIM`/`DOC-FACT` — https://vercel.com/changelog/ai-gateway-now-supports-typesafe-clients-and-http-api-for-jev — "eve uses Jev as the default evaluation model for automatic model selection, typed evaluations, and automated tool approvals."
- **OpenRouter access.** https://openrouter.ai/docs/guides/community/jev.md — "There's no waitlist or separate TypeSafe account." Both OpenRouter surfaces "require the same OpenRouter API key and are billed to the same OpenRouter account."
- **Confidence location differs by integration.** In the AI SDK, TypeSafe's confidence is in provider metadata. https://ai-sdk.dev/docs/ai-sdk-core/evaluation — "TypeSafe exposes its separate Choice/Score confidence statistic at `result.providerMetadata?.typesafe?.confidence`, keyed by question ID."

### Vercel Connect and how Jev relates to it
- **What Connect is.** A credential broker for deployed apps and agents. `DOC-FACT` — https://vercel.com/docs/connect — "Your code asks Connect for access at the moment it needs it, so no provider API key ever lives in your environment variables."
  - Authentication: "your code authenticates to Vercel Connect with a Vercel OIDC token or a Vercel access token, and Vercel Connect exchanges the authorized credential with the provider."
- **The Jev connector is an API-key connector.** `DOC-FACT` — https://vercel.com/connect/browse — "Jev TypeSafe AI decisions, classification, and scoring … API Key".
  - API-key connectors are customer-managed. https://vercel.com/docs/connect — "Customer Managed Connector: You register an OAuth client (or generate an API key) with the Third Party Platform yourself and supply the credentials at create time."
- **Jev connector page.** `VENDOR-CLAIM`/`DOC-FACT` — https://vercel.com/connect/jev — "Request Jev credentials only when your application or agent needs them instead of copying secrets into application code." Setup: `vercel connect create jev --name acme-jev`, then `getToken('jev/acme-jev')`.
- **Connect pricing.** `DOC-FACT` — https://vercel.com/docs/connect — "Hobby includes 500 token requests per month at no extra charge. Pro is billed at $3.00 per 1,000 token requests." Beta customers' new pricing took effect "on September 25, 2026" (https://vercel.com/docs/connect/pricing).
- **Relation between Connect and AI Gateway** (`DERIVED` reading). Connect is a separate path from AI Gateway: it stores your own TypeSafe API key and hands it to your runtime, so calls go to TypeSafe and are billed by TypeSafe. AI Gateway instead bills calls through Vercel ("Requests are billed through AI Gateway", https://vercel.com/docs/ai-gateway/sdks-and-apis/typesafe). The Connect page does not show how the returned token is passed to a TypeSafe client; that step is my inference.

---

## 5. Access and pricing

### IDs
See the integration table in §4. Additional IDs:
- **OpenRouter endpoint build tag.** `DOC-FACT` — https://openrouter.ai/api/v1/models/typesafe/jev-1.13/endpoints — `"name":"TypeSafe | typesafe/jev-1.13-20260917"`, `"context_length":32000`, `"max_completion_tokens":28800`.
- **OpenRouter also lists a separate "Jev Router" product.** `DOC-FACT` — https://openrouter.ai/api/v1/models — `typesafe/jev-router`, created 2026-09-25 (epoch 1790363560). Description: "Jev Router picks the best model and reasoning effort for each request, balancing quality, speed, and cost. It runs on Jev". Its pricing shows "-1", which I read as variable. I did not investigate it further.
- **Vercel release date.** `DOC-FACT` — https://vercel.com/ai-gateway/models/jev — Release Date "09/15/2026". The Gateway API gives `"released": 1789430400`, which is 2026-09-15 UTC (`DERIVED`).

### Price
- **TypeSafe list price.** `DOC-FACT` — https://docs.typesafe.ai/models — "Price (per Btok / per Mtok) | $42 / $0.042". Also: "Charged per input token. Output tokens are free."
- **Vercel AI Gateway.** `DOC-FACT` — https://ai-gateway.vercel.sh/v1/models — `"pricing": {"input": "0.000000042", "output": "0"}`. The model page rounds this to "$0.04per 1M" (https://vercel.com/ai-gateway/models/jev).
  - Worked example (`DERIVED`): the Gateway doc's sample response bills 275 input tokens as `"cost": "0.00001155"`, which is 275 × $0.042/M.
- **OpenRouter.** `DOC-FACT` — https://openrouter.ai/~typesafe/jev-latest — "$0.042 per million input tokens, $0 per million output tokens."
- **Cloudflare.** `DOC-FACT` — https://developers.cloudflare.com/ai/models/typesafe/jev/ — "Input (per 1M tokens)$0.042 … Output (per 1M tokens)$0.00".
- **Price comparison claim.** `VENDOR-CLAIM` — https://typesafe.ai — "$42 Per Billion input tokens." Also: "238x Lower input price than Claude Fable 5.1".
- **Are the prices subsidized?** The vendor is inconsistent.
  - `VENDOR-CLAIM` — https://typesafe.ai (FAQ, rendered) — "We can serve Jev profitably at our current prices."
  - Blog — "We can’t prove it isn’t subsidized; we’ll need the long-term to prove the sustainability of our pricing (which we expect to go down, not up)."

### Rate limits
- `DOC-FACT` — https://docs.typesafe.ai/models — "250,000 tokens per second / 1,200 requests per minute". The limits are dynamic. Higher limits are sold: "Higher limits are available on custom and enterprise plans."
- Error codes: https://docs.typesafe.ai/api — `429 Too Many Requests` and `529 Overloaded`. For both: "retry the request with exponential backoff".

### Availability
- **TypeSafe direct: early access with a waitlist.** `VENDOR-CLAIM`:
  - Blog — "Our first public model is Jev, available today in early access."
  - Press release — "Currently available in early access for select developers".
  - https://typesafe.ai FAQ — "Join the waitlist!"
- **Platforms: open to any key holder.**
  - OpenRouter: "There's no waitlist or separate TypeSafe account."
  - Vercel AI Gateway: listed as generally available, with no waitlist mentioned.

### Data handling and ZDR (sources disagree; see Open questions)
- **TypeSafe.** `DOC-FACT` — https://docs.typesafe.ai/legal — "We also offer zero data retention (ZDR) for enterprise customers."
- **Vercel changelog.** `DOC-FACT` — https://vercel.com/changelog/typesafe-ai-jev-now-available-on-ai-gateway — "Jev supports Zero Data Retention and No Training, enabled per request in the example."
- **Vercel Gateway model API.** `DOC-FACT` — https://ai-gateway.vercel.sh/v1/models — `"zdr": "none"`, `"no_training": "all"`. The endpoints API shows `"has_zdr":false`.
- **Cloudflare.** https://developers.cloudflare.com/ai/models/typesafe/jev/ — "Zero data retention | Yes".
- **OpenRouter provider data policy.** https://openrouter.ai/typesafe/jev-1.13 (page JSON) — `"training":false … "retainsPrompts":false`.

### License
- **The model is proprietary.** It is API-only and no weights are published. The customer terms are the Master Customer Agreement, the DPA and the Privacy Policy (`DOC-FACT`, https://docs.typesafe.ai/legal — "the general customer agreements that govern your use of TypeSafe"). I did not read the MCA.
- **Client code is open source.** `DOC-FACT` — GitHub API (https://api.github.com/orgs/typesafe-ai/repos) reports MIT licenses for:
  - `typesafe-sdk-python`
  - `typesafe-sdk-js`
  - `skills`
  - `system-one-adapter-python` ("Drop-in TypeSafeClient replacement backed by LLM APIs")
- **Hugging Face.** There is no official TypeSafe presence: `huggingface.co/api/organizations/typesafe-ai` returns 404. A different account named `TypeSafeAI` hosts a StepFun "Step-5-Preview" mirror; it is not linked from typesafe.ai and its affiliation is unverified. Several community "Jev" models on HF are unrelated to TypeSafe.

### SDK and changelog timeline (DOC-FACT)
- **Python SDK** (https://docs.typesafe.ai/sdk/python/changelog):
  - v0.5.7 (2026-09-14): "initial public release".
  - v0.6.0 (2026-09-15): Score criteria became an ordered sequence (breaking change).
  - v0.7.0 (2026-09-18): switched to pydantic and added `response_model`.
  - v0.7.1 (2026-09-21): "add examples for usage with AI gateways".
  - v0.7.2 (2026-09-26): HTTP/2 extra.
- **JS SDK:** v0.5.7 (2026-09-11) was the initial release; v0.6.0 followed on 2026-09-15 (https://docs.typesafe.ai/sdk/javascript/changelog).
- **Vercel:**
  - Gateway launch changelog: Sep 16, 2026.
  - TypeSafe-compatible API and HTTP API: Sep 21, 2026.
  - Adoption blog post: Sep 18, 2026.

---

## 6. Claimed benchmarks and results (exactly as stated)

**Headline numbers.** `VENDOR-CLAIM` — https://typesafe.ai — "193.6x Faster, 444.6x Cheaper." The footnote reads "*based on workflows for System One tasks (proof)".
- Blog, on where the numbers come from: "This is where the claims of 193.6x faster, 444.6x cheaper on our home page comes from, and we expect that these are on the higher end of real world gains."
- Vercel restates them rounded: "up to 194 times faster and 445 times cheaper than language models" (https://vercel.com/blog/ai-gateway-jev-model-launch).

**Other speed and quality framings from the same vendor** (all `VENDOR-CLAIM`; they are not mutually consistent):
- Blog — "Jev achieves similar levels of intelligence on System One tasks compared to existing LLMs, while being two orders of magnitude faster and more efficient."
- Blog table — "This can range from 40x-200x faster".
- Press release — "up to 100 times faster and less expensive than other frontier models".

**Public benchmarks: deliberately not published.** `VENDOR-CLAIM` — blog FAQ (rendered) — "We deliberately chose not to publish performance against public benchmarks. In fact, we plan to only have one-off evals when we make product updates."

### "Workflow evals": methodology (https://evals.typesafe.ai/)
- **What is measured.** Four hand-built workflows: Security Incidents, Agent Trace Observability, Invoice Processing and Customer Service. Each decomposes a policy into Noul, Choice and Score questions plus code rules. The metric is the accuracy of the final action.
- **The reference is LLM consensus, not ground truth.** `DOC-FACT` (methodology statement) — "the reference labels are generated via an average of the responses of GPT-6 Astra and Claude Fable 5.1, both at high thinking, answering every question in the harness."
- **Baselines use default settings.** "All other models are evaluated using the provider's default reasoning settings."
- **Aggregation.** "Each point averages one model configuration's accuracy, cost and time over the four workflows with equal weight, against the consensus labels."
- **Nuances the vendor states** (blog):
  - "they were made by individuals on our model capabilities team, so some bias could exist."
  - "We use the average of GPT-6 Astra and Fable 5.1 as the reference answer, which biases answers towards OpenAI and Anthropic’s models."
  - LLM baselines ran through the vendor's own wrapper: "The LLMs use our System One LLM wrapper, which constrains LLMs to output structured decisions compatible with our API."
- **Not published:** case counts per workflow and confidence intervals.

### Workflow eval numbers (verbatim point labels from https://evals.typesafe.ai/)

"workflow" means the decomposed harness; "prompt" means the whole policy given as one prompt. Format: accuracy vs the Astra+Fable consensus · cost per case · time per case.

| Model (workflow mode) | Mean of 4 | Security Incidents | Agent Trace Obs. | Invoice Processing | Customer Service |
|---|---|---|---|---|---|
| **Jev** | **67.8% · $0.0004 · 0.4 s** | 61.7% · $0.0001 · 0.3 s | 71.6% · $0.0003 · 0.5 s | 61.8% · $0.0011 · 0.5 s | 76.0% · $0.0001 · 0.4 s |
| sol | 74.1% · $0.0836 · 23.3 s | 62.5% | 76.6% | 79.1% | 78.3% |
| opus 5 | 73.1% · $0.1761 · 37.8 s | 66.2% | 75.2% | 78.4% | 72.4% |
| terra | 67.9% · $0.0304 · 10.1 s | 51.2% | 73.0% | 74.7% | 72.7% |
| sonnet 5 | 67.8% · $0.1174 · 78.1 s | 60.8% | 68.0% | 72.9% | 69.3% |
| luna | 66.8% · $0.0033 · 12.9 s | 52.1% | 76.1% | 67.8% | 71.4% |
| DS v4 pro | 65.5% · $0.0413 · 86.5 s | 41.7% | 71.6% | 72.7% | 76.1% |
| DS v4 flash | 64.4% · $0.0059 · 51.9 s | 37.9% | 73.0% | 69.8% | 76.8% |
| haiku 4.5 | 53.6% · $0.0195 · 12.5 s | 58.8% | 57.2% | 42.9% | 55.4% |

`DERIVED` reading of these numbers:
- On mean accuracy, Jev ties sonnet 5 and is about level with terra (67.9%). It is about 6 points below sol and opus 5.
- Jev is not the most accurate model on any workflow. On Invoice Processing it is below every model except haiku 4.5.
- Its advantage is cost and latency: about $0.0004 and 0.4 s per case, vs $0.003 to $0.18 and 10 to 80 s for the LLMs.
- The blog chart shows the same data. `DERIVED` from the chart image — "Average of 4 workflows: accuracy vs cost", with the frontier line running Jev → luna → terra → sol.

### Structured-output and tool-call error rates (blog chart; values `DERIVED` from the image)
- **Structured output error rate** (lower is better):
  - Jev 0%
  - luna 0.58%
  - terra 0.58%
  - sol 0.83%
  - astra 1.43%
  - gemini 3.1 pro 1.94%
  - gemini 3.8 flash 3.15%
  - opus 5 5.73%
  - fable 5.1 8.25%
  - sonnet 5 13.2%
  - haiku 4.5 45.5%
- **Tool call error rate:**
  - Jev 0%
  - opus 5 0.67%
  - fable 5.1 1.38%
  - haiku 4.5 1.76%
  - sonnet 5 2.07%
  - gemini 3.8 flash 2.15%
  - gemini 3.1 pro 3.17%
  - terra 5.5%
  - luna 7.67%
  - astra 16.6%
  - sol 17.0%
- **Caveats the vendor states** (`VENDOR-CLAIM`):
  - "The numbers for LLMs are from OpenRouter i.e., there almost certainly is bias here".
  - "Our number is not empirical."

### Side-by-side demo
`VENDOR-CLAIM` — blog:
- "the only disagreement with GPT-5.6 Terra is on “Churn likelihood level”."
- "The relatively shorter input paints our model in an advantageous light."

### Cookbook results (vendor-run)
`VENDOR-CLAIM`:
- **Re-ranking.** https://docs.typesafe.ai/cookbooks/rerank_typesafe — "raise top-1 accuracy from 5% to 18% and top-10 accuracy from 38% to 62%" on 40 CLERC legal queries. The run: "1200 TypeSafe calls used 1,536,002 input and 25,200 output tokens, costing $0.0645."
- **Batching questions.** https://docs.typesafe.ai/cookbooks/parallel_questions — "batching every question into one TypeSafe call is 12.2x cheaper and 10.0x faster with no change in answers."

### Platform adoption claim
`VENDOR-CLAIM` (Vercel) — https://vercel.com/blog/ai-gateway-jev-model-launch — "By hour 24, nearly 13% of paid teams were using it. That's 2x the GPT-5.6 family and more than 6x Fable 5.1's share."

---

## 7. Limitations, caveats and known issues (vendor-stated)

**Official "jaggedness" page for `jev-1.13`.** `DOC-FACT` — https://docs.typesafe.ai/model-jaggedness/jev-1.13, "Last reviewed 2026-09-17". It lists nine failure modes. Summary: "It may struggle with tasks that require additional levels of indirection. It can be quite literal in its understanding. It struggles with tasks that require numeric precision."
1. **Literal reading.** "`jev-1.13` answers the question you wrote, not the one you meant."
2. **Math and counting.**
   - "Jev is not a calculator."
   - "`jev-1.13` does not count reliably."
   - Score interpolation: "`jev-1.13`'s score levels are weak in numerical calibration."
3. **Dates.** "`jev-1.13` reads dates as text, not as ordered quantities."
4. **Indirection.** "Instructions carrying double negatives or complex indirection are answered less reliably."
5. **Large irrelevant state (context rot).**
   - "Accuracy falls as the state grows with content unrelated to the decision."
   - Also: "Jev suffers from context rot, so unrelated material in the `state` costs you accuracy."
6. **Adversarial content / prompt injection.** "State is data, and `jev-1.13` does not treat it as hostile by default."
7. **Contradictory instructions and criteria.** "When the `instructions` and the `criteria` ask for different things, `jev-1.13` might get confused."
8. **No structural invariants.**
   - A question and its negation, asked as two Nouls, gave 0.72 + 0.47 = "1.19".
   - Guidance: "Don't carry a threshold tuned on a Noul over to a Choice".
9. **Generation.** "`jev-1.13` is not trained to generate text."

**Other stated caveats:**
- **Calibration holds only in aggregate.** `DOC-FACT` — https://docs.typesafe.ai/concepts/system-one — "Calibration is measured across groups of predictions; it does not guarantee that an individual answer is correct."
- **No explanations.** `DOC-FACT` — https://openrouter.ai/docs/guides/community/jev.md — "No, Jev doesn't return the reasoning it goes through to make its decision."
- **Text only; non-text input must be pre-processed.** `DOC-FACT` — https://vercel.com/i/what-is-jev — "Jev's current inputs are text-based; prepare a text description or transcript before asking it questions about media content."
- **Non-English accuracy is lower.** See §2.
- **Aliases drift.** The answers behind `jev-latest` change when a new release ships. See §2.
- **Rate limits are unstable.** "the limits above can change without notice while we do, as upcoming large GPU deals land" (https://docs.typesafe.ai/models).
- **The AI SDK integration is experimental.** `DOC-FACT` — https://ai-sdk.dev/docs/ai-sdk-core/evaluation — "This API and the evaluation model specification are experimental and may change in patch releases."
- **Fallback caveat on AI Gateway.** `DOC-FACT` — https://vercel.com/docs/ai-gateway/sdks-and-apis/typesafe — "`confidence: 0` and `probabilities: {}` mean those values are unavailable. They do not mean the model measured zero confidence."
- **A type-correct answer can still be wrong.** `DOC-FACT` (platform guidance) — https://vercel.com/i/what-is-jev — "An answer can fit the allowed values and still misinterpret the evidence."
- **Early days.** `VENDOR-CLAIM` — blog — "We’re still in Jev’s early days."

---

## Snippets (verbatim)

**TypeSafe HTTP API (quick start)**, https://docs.typesafe.ai/introduction/quickstart
```bash
curl -X POST https://api.typesafe.ai/v1/systemone \
  -H "Authorization: Bearer $TYPESAFE_API_KEY" \
  -H "Content-Type: application/json" \
  -d @- <<'EOF'
  {
    "state": "Hi, I've been trying to connect my Stripe account for 3 days and the integration keeps failing. I'm losing sales. Please help ASAP.",
    "model": "jev-latest",
    "questions": {
      "urgency": {
        "type": "noul",
        "instructions": "Does this message express urgency?"
      }
    }
  }
EOF
```

**TypeSafe response shape (Choice)**, https://docs.typesafe.ai/api
```json
{
  "model": "jev-1.13.0",
  "answers": {
    "department": {
      "type": "choice",
      "choice": "billing",
      "probabilities": { "billing": 0.88, "technical": 0.12, "sales": 0.0 },
      "confidence": 0.81
    }
  },
  "usage": { "input_tokens": 318, "output_tokens": 34 }
}
```

**TypeSafe Python SDK**, https://docs.typesafe.ai/introduction/quickstart
```python
from typesafe_sdk import Choice, Noul, Score, TypeSafeClient

client = TypeSafeClient()

ticket = "Hi, I've been trying to connect my Stripe account for 3 days and the integration keeps failing. I'm losing sales. Please help ASAP."

response = client.system_one(
    state=ticket,
    questions={
        "department": Choice(
            instructions="Which team should handle this",
            criteria={
                "billing": "Payment or subscription issues",
                "technical": "Bugs or integration problems",
                "sales": "Pricing or account questions",
            },
        ),
        "frustration": Score(
            instructions="How frustrated the customer appears",
            criteria=[
                "Calm, just stating facts",
                "Frustrated but civil",
                "Very angry, strong language",
            ],
        ),
        "is_urgent": Noul(
            instructions="The message conveys urgency or time-sensitivity",
        ),
    },
)

print(response.answers["department"].choice)  # "technical"
print(response.answers["frustration"].score)  # 1.0
print(response.answers["is_urgent"].noul)     # 1.0
```

**AI SDK via Vercel AI Gateway**, https://vercel.com/docs/ai-gateway/modalities/evaluation
```typescript
import { experimental_evaluate as evaluate } from 'ai';

export async function GET() {
  const result = await evaluate({
    model: 'typesafe-ai/jev',
    state: 'The support agent issued a full refund to the customer.',
    questions: {
      refunded: {
        type: 'boolean',
        instructions: 'Was a refund issued?',
      },
    },
  });

  return Response.json(result.answers);
}
```

**AI Gateway HTTP**, same page
```bash
curl https://ai-gateway.vercel.sh/v1/evaluate \
  -H "Authorization: Bearer $AI_GATEWAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "typesafe-ai/jev",
    "state": "I was charged twice for my subscription.",
    "questions": {
      "refund": {
        "type": "boolean",
        "instructions": "Is the customer asking for money back?"
      }
    }
  }'
```

**Existing TypeSafe client pointed at AI Gateway**, https://vercel.com/docs/ai-gateway/sdks-and-apis/typesafe
```diff
  import { TypeSafeClient } from '@typesafe-ai/sdk';

  const client = new TypeSafeClient({
-   apiKey: process.env.TYPESAFE_API_KEY,
+   apiKey: process.env.AI_GATEWAY_API_KEY,
+   baseURL: 'https://ai-gateway.vercel.sh/typesafe',
  });
```

**Gateway evaluation fallback (escalate low confidence)**, same page, excerpt of the request object
```typescript
  providerOptions: {
    gateway: {
      models: [
        {
          model: 'openai/gpt-6-astra',
          when: { question: 'intent', confidenceBelow: 0.6 },
        },
      ],
    },
  },
```

**OpenRouter Decisions API**, https://openrouter.ai/docs/guides/community/jev-tutorial.md (curl variant)
```bash
curl https://openrouter.ai/api/alpha/decisions \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "typesafe/jev-1.13",
    "state": {
      "customer_tier": "enterprise",
      "ticket": "My checkout page shows a blank screen after I click Pay. I have tried two browsers."
    },
    "questions": {
      "is_bug": {
        "type": "noul",
        "instructions": "Is the customer reporting a software defect?",
        "criteria": {
          "true": "The customer describes broken or unexpected product behavior.",
          "false": "The customer is asking a question or requesting a feature."
        }
      },
      "team": {
        "type": "choice",
        "instructions": "Which team should own this ticket?",
        "criteria": {
          "payments": "Checkout, billing, or payment processing issues.",
          "frontend": "Rendering, layout, or browser compatibility issues.",
          "account": "Login, permissions, or profile issues."
        }
      },
      "urgency": {
        "type": "score",
        "instructions": "How urgent is this ticket?",
        "criteria": [
          "Can wait for the next release",
          "Should be fixed this week",
          "Blocking revenue right now"
        ]
      }
    }
  }'
```

**Vercel Connect (Jev connector)**, https://vercel.com/connect/jev
```bash
vercel link
vercel connect create jev --name acme-jev
vercel env pull
```
```typescript
import { getToken } from '@vercel/connect';

const token = await getToken('jev/acme-jev');
```

**Counting in code, one Noul per item** (vendor workaround for the counting weakness; excerpt, imports and setup omitted), https://docs.typesafe.ai/model-jaggedness/jev-1.13
```python
result = client.system_one(
    {"items": items},
    {
        f"item_{i}": Noul(instructions=f"Is `items[{i}]` the name of a fruit?")
        for i in range(len(items))
    },
)

count = sum(result.nouls[f"item_{i}"].noul > YES for i in range(len(items)))
```

---

## Open questions / unverified

1. **Architecture is undisclosed.** There is no paper, technical report or model card; the closest thing to a model card is https://docs.typesafe.ai/models. "New model architecture", "parallel sampler" and "RLCD" are named but not described. Parameter count, base model, tokenizer (OpenRouter says "Other") and hardware are unknown.
   - The `typesafe-ai` GitHub org holds forks of `ML-GSAI/LLaDA` (a diffusion language model) and `vllm-project/vllm`. That hints at the research direction but proves nothing about Jev.
2. **Context length conflict.**
   - TypeSafe says 64k per request, with 32k for the state plus the longest question.
   - Vercel's Gateway model API says `context_window: 32000`, but its description repeats the 64k/32k split.
   - OpenRouter and Cloudflare say 32,000 total ("the `state` you send plus the questions").
   - The effective limit through each gateway is untested.
3. **ZDR conflict.**
   - Vercel's changelog says Jev supports ZDR (per request).
   - The Gateway model API says `zdr: "none"` / `has_zdr: false`.
   - Cloudflare says ZDR "Yes".
   - TypeSafe itself offers ZDR only "for enterprise customers".
4. **Provider name anomaly.** Vercel's endpoint API names the Jev endpoint `"digitalocean | typesafe-ai/jev"` (`provider_name: "digitalocean"`). The model page and the routing metadata say `typesafe-ai`. The hosting arrangement is unexplained.
5. **Speed and cost claims are inconsistent across vendor materials.** They range from "up to 100 times" (press release), "two orders of magnitude" and "40x-200x" (blog), to "193.6x faster, 444.6x cheaper" (home page). Latency is given as "<100 ms" (press release), "70ms-500ms" (blog) and 0.3–0.5 s per case (evals). None has been independently measured here; see file 02.
6. **Eval validity.**
   - "Accuracy" means agreement with the Astra+Fable consensus, not with human ground truth.
   - There are only 4 in-house workflows, with no case counts or confidence intervals.
   - The LLM baselines ran through TypeSafe's own adapter.
   - The hallucination and tool-error chart uses OpenRouter-sourced LLM numbers with no stated method. I read those chart values from an image, so they may be slightly off.
7. **Funding beyond the seed round.** The primary source only confirms the ~$40M seed led by DCVC (2026-09-15); other investors are not named. Search results (hoodline, cryptobriefing, Forbes, tradersunion) report later talks at about a $10B valuation and a $200M seed valuation. These are secondary sources and are **not verified** here; they are left to file 02.
8. **Availability wording conflicts.** TypeSafe direct access is still "early access"/waitlist, while OpenRouter, Vercel and Cloudflare serve Jev to any key holder. Rate limits are "adjusting dynamically".
9. **Calibration.** The claim that Jev is "calibrated" is not backed by any published reliability diagram or ECE in the primary sources.
10. **"Noul".** The etymology of the name is not explained in the docs I read.
11. **Customer terms.** The Master Customer Agreement, the DPA and the Acceptable Use Policy were not read. The license and terms for model outputs are therefore unverified.
12. **Jev Router.** OpenRouter's `typesafe/jev-router` (2026-09-25) is a separate product and was not investigated.
13. **Version dates.** The OpenRouter endpoint tag `jev-1.13-20260917` suggests a 2026-09-17 build, which is later than the 09-15 launch. It is unknown whether the served weights changed after launch.
14. **Illustrative numbers.** The Vercel "what-is-jev" incident example uses invented probabilities ("This is an editorial illustration, not an observed Jev result"). Do not cite them as results.

---

## Sources (all fetched 2026-09-28)

Vercel (platform)
- https://vercel.com/i/what-is-jev — "What is Jev, TypeSafe AI's System One model?" (Ben Sabic)
- https://vercel.com/kb/jev-from-typesafe-ai — "Jev from TypeSafe AI" (KB hub)
- https://vercel.com/ai-gateway/models/jev — AI Gateway model page "Jev"
- https://ai-gateway.vercel.sh/v1/models and https://ai-gateway.vercel.sh/v1/models/typesafe-ai/jev/endpoints — Gateway model API (JSON)
- https://vercel.com/connect/jev — Vercel Connect "Jev" connector page
- https://vercel.com/connect/browse — Connect connector catalog
- https://vercel.com/docs/connect — "Vercel Connect" docs
- https://vercel.com/docs/connect/pricing — "Vercel Connect Pricing"
- https://vercel.com/docs/connect/concepts/connectors — "Connectors"
- https://vercel.com/docs/ai-gateway/modalities/evaluation — "Evaluation" (last_updated 2026-09-22)
- https://vercel.com/docs/ai-gateway/sdks-and-apis/typesafe — "TypeSafe API with AI Gateway"
- https://vercel.com/docs/ai-gateway/models-and-providers/evaluation-fallbacks — "Evaluation Fallbacks" (skimmed)
- https://vercel.com/changelog/typesafe-ai-jev-now-available-on-ai-gateway — changelog, Sep 16, 2026
- https://vercel.com/changelog/ai-gateway-now-supports-typesafe-clients-and-http-api-for-jev — changelog, Sep 21, 2026
- https://vercel.com/blog/ai-gateway-jev-model-launch — "Jev is the fastest-adopted model in AI Gateway history", Sep 18, 2026
- https://vercel.com/i/jev-vs-gpt-6-astra — "TypeSafe AI Jev vs. GPT-6 Astra"
- https://vercel.com/i/jev-integrations — "6 ways to integrate Jev"
- https://vercel.com/kb/guide/typesafe-jev-and-ai-sdk — "How to classify, route, and score with Jev and AI SDK" (published 2026-09-19)
- Also fetched but only skimmed: /i/jev-use-cases, /i/when-to-use-jev, /i/jev-agent-control, /i/jev-probabilities-and-thresholds, /kb/guide/jev-ai-sdk-form-router, /kb/guide/auto-approve-tool-calls-eve-jev, /kb/guide/moderate-product-reviews-jev-tanstack-ai
- https://ai-sdk.dev/docs/ai-sdk-core/evaluation — AI SDK "Evaluation" (experimental_evaluate)

TypeSafe AI (vendor)
- https://typesafe.ai — Home (FAQ expanded in a headless browser)
- https://typesafe.ai/blog/introducing-system-one-models-and-jev — "Introducing System One Models & Jev", Diogo Almeida, Sep 15, 2026 (FAQ expanded; 3 chart images read)
- https://typesafe.ai/team — Team
- https://typesafe.ai/manifesto — "Composable AI: Build Prod, Not God"
- https://typesafe.ai/blog/bitterest-lesson — "The Bitterest Lesson", Sep 10, 2026 (skimmed)
- https://typesafe.ai/legal/terms — Terms of Use (TypeSafe AI, Inc.)
- https://evals.typesafe.ai/ (+ security_incidents, agent_trace_observability, invoice_processing, customer_service pages) — "Workflow evals"
- https://docs.typesafe.ai/llms.txt — docs index
- https://docs.typesafe.ai/introduction · /introduction/quickstart · /introduction/coding-agents · /introduction/machine-learning-primer
- https://docs.typesafe.ai/concepts/system-one · /concepts/state
- https://docs.typesafe.ai/primitives · /primitives/choice · /primitives/score · /primitives/noul · /primitives/advanced
- https://docs.typesafe.ai/confidence
- https://docs.typesafe.ai/models — Models (Jev 1.13, pricing, limits)
- https://docs.typesafe.ai/api — API reference
- https://docs.typesafe.ai/model-jaggedness/jev-1.13 — "Jev 1.13 jaggedness" (last reviewed 2026-09-17)
- https://docs.typesafe.ai/legal — Legal
- https://docs.typesafe.ai/sdk · /sdk/python · /sdk/python/changelog · /sdk/javascript · /sdk/javascript/changelog
- https://docs.typesafe.ai/cookbooks/parallel_questions · /cookbooks/rerank_typesafe (also fetched: sde_cascade, llm_guardrails)
- https://github.com/typesafe-ai (via api.github.com) — org and repo metadata and licenses
- https://finance.yahoo.com/technology/ai/articles/typesafe-ai-emerges-stealth-40m-190000776.html — Business Wire press release "TypeSafe AI Emerges From Stealth With $40M in Funding…", Sep 15, 2026 (the Morningstar copy returned 403)

Other platforms
- https://openrouter.ai/docs/guides/community/jev.md — "Jev Documentation: Using the TypeSafe Decision Model on OpenRouter"
- https://openrouter.ai/docs/guides/community/jev-tutorial.md — "Jev Tutorial: Make Your First Decision Call on OpenRouter"
- https://openrouter.ai/~typesafe/jev-latest and https://openrouter.ai/typesafe/jev-1.13 — model pages (metadata)
- https://openrouter.ai/api/v1/models/typesafe/jev-1.13/endpoints and https://openrouter.ai/api/v1/models — OpenRouter model API
- https://developers.cloudflare.com/ai/models/typesafe/jev/ — Cloudflare Workers AI "Jev"
- https://huggingface.co/api/organizations/typesafe-ai (404) and the HF model search API — checked for official weights; none found
