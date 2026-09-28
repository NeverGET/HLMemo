# 02b — Jev: the game demos, customization, and tuning without weights

Compiled 2026-09-28. $0 spent. No model API was called. Only public pages, repos, HN (Algolia API), X posts via the public fxtwitter mirror, and the vendor's Vimeo demo videos were read. I looked at the videos as frames extracted with ffmpeg.

This file extends `01-primary-sources.md`, `02-independent-evidence.md` and `03-JEV-DOSSIER.md`. It does not repeat them. Where a fact is already there, it points to it ("see 02 §x").

**Labels**
- `VENDOR-CLAIM`: TypeSafe's own statements, docs, cookbooks, videos and CEO remarks.
- `INDEPENDENT`: a third party built, measured or observed it. Community repos are self-reported unless stated otherwise.
- `INFERENCE`: my own reasoning or arithmetic from the cited facts.

When I read on-screen text from a vendor video, the text is labelled `VENDOR-CLAIM (video frame, ~t s)`, because the content is the vendor's and only the reading is mine. Quotes are verbatim and at most 30 words, with markdown formatting removed.

---

## Answer to the owner's question, in short

**Was Jev tuned to play games?** No evidence of it, and some evidence against it. Every Jev game demo, the vendor's and the community's, uses the same pattern:
- Code turns the game into text or JSON state and lists the legal actions (plus facts about each) as Choice/Noul options.
- The one shared `jev-1.13` model picks among those options, typically 1 to 10 times per second.
- Code then does the aiming, pathfinding and arithmetic.

Behaviour is changed by rewording instructions or typing a "standing order", not by training. The game demos that *were* trained are open replica models (NanoJev, PlayJev, Von, and others). They copy Jev's interface; they are not Jev.

**Can we fine-tune or calibrate Jev?**
- Fine-tuning: not offered. The docs say the same weights serve every account.
- Future fine-tuning: the CEO says he "could imagine it", explicitly "not a promise".
- Calibration and tuning *around* the model: yes, and this is well evidenced (§3).
  - Refitting its probabilities on a few hundred labels.
  - Decomposing into signal questions plus a small learned combiner.
  - Rewriting criteria and question shape, including with automated optimizers (DSPy/GEPA).
  - Measuring thresholds for a cascade.

---

## 1. Game demos

### 1.1 Vendor demos (TypeSafe): Doom and Wikiracing only

The launch post lists exactly two "Fun Demos", Doom and Wikiracing. `VENDOR-CLAIM` https://typesafe.ai/blog/introducing-system-one-models-and-jev
- Its embedded videos are the Vimeo files **"Jev-Demo-Doom-Full"** (162 s, https://vimeo.com/1227495732/5c335e90e5) and **"Jev-Demo-Wikirace"** (244 s, https://vimeo.com/1227495711/88074bcc80), both by "TypeSafe AI".
- The CEO's 176 s launch video on X (https://x.com/CompleteSkeptic/status/2099925682726002904) shows no game footage. I checked one frame every 5 s. `INDEPENDENT` (my frame review)
- The docs "Demos" page lists only a smart-home demo. `VENDOR-CLAIM` https://docs.typesafe.ai/demos — "Smart Home Assistant Demo - Evaluate user smart home requests with speculative questions and LLM fallback."

#### Doom (vendor)

**What was claimed**
- Rate and cost: "The engineer behind it was worried about making 10 queries a second (which ends up costing ~$7/hour)". `VENDOR-CLAIM` (blog)
- Input: "The demo is on structured state as a data structure with text, not on images (yet…)". `VENDOR-CLAIM` (blog)
- Quality: "A non-AI doom bot could play better, but we wanted a bot that was reactive to different representations of game state". `VENDOR-CLAIM` (blog)
- The CEO on the input format: "inputs are structured program state. there is an example at around second 30 of the doom demo". `VENDOR-CLAIM` https://news.ycombinator.com/item?id=49719064
- Who built it: the CEO calls it "Ali's Doom demo" (Latent Space, 02:08:32, https://www.latent.space/p/jev). TypeSafe's DevRel on X is "Allie the Icon", whose profile reads "DevRel at @TypesafeAI" (https://x.com/allietheicon). `INFERENCE`: the demo was built by vendor staff, likely DevRel.

**How it actually played** (read from the video frames)
- **State.** A JSON "SITUATION REPORT" with a measurement context, distance bands, bearings and player stats. `VENDOR-CLAIM (video frame, ~30–36 s)`:
  - `"control_rate": "decisions update every ~4 ticks (~0.1 seconds); controls persist continuously between decisions"`
  - `"controls": "aiming, movement, and the trigger operate simultaneously"`
  - `"distance_unit": "Doom map unit; not inches, feet, meters, or miles. The player's body is 32 units wide."`
- **Questions per decision.** Several typed questions go in one request. `VENDOR-CLAIM (video frames, ~12–60 s)`:
  - FIRING (fire / hold_fire): "Should the player's trigger be held down right now?"
  - GOAL (upgrade weapon, scout, kill enemies, stock ammo, add armor, restore health): "Considering `player`, `enemies`, and `items`, what is the player's highest-priority goal right now?"
  - DODGE (carry_on, dodge_left, dodge_right, dodge_back…): "The player's current top priority is trying to restore health with medikit D. What does this exact moment call for?"
  - MOVEMENT: "…Given the current situation, how should the player move right now?"
  - TURN (on the level map): "Where should the player point right now? Looking and aiming are one act: the gun goes where the eyes go." This was read from a small frame, so the wording may be slightly off.
- **Decision graph (a controller around the model).** The on-screen GRAPH shows chained nodes. `VENDOR-CLAIM (video frames, ~48–60 s)`:
  - "offers goals → 6 goals" → "goal → restore_health"
  - "which enemy → cacodemon B", "which health pickup → stimpack A", then "goal + subject → objective"
  - "movement → walk to stimpack A" → "answer → destination → (-768, 512)" → "MOVE → (-327, 281)"
  - "facing → look left" → "pick/turn → bearing → 274°" → "FACE"
  - "trigger → hold_fire", "weapon → keep"

  `INFERENCE`: Jev picks semantic options. Code turns them into coordinates and bearings. The answer to GOAL is written into the next questions' instructions ("current top priority is…").
- **Harness settings.** The "NEW SESSION" dialog. `VENDOR-CLAIM (video frame, ~97 s)`:
  - slot "brain: helm"
  - JUDGE "TS Research"
  - CONTEXT "facts repeated in the question"
  - GUIDE "strategy guide closes the state"
  - NAVIGATOR "pathfinder"
  - IWAD "The Ultimate Doom", map "E1M1"
  - opponent bots named Rambo, McClane, Ripley and others

  The session row reads "helm · research/v13_snowy_elephant +ctx +guide". So a strategy guide is added to the state, facts are repeated inside the questions, and a code pathfinder does the navigation.
- **Language control, no retraining.**
  - Frames at ~72–96 s show the standing order "Do not fire, simply dodge" typed into the ORDERS box, with FIRING then at hold_fire. `VENDOR-CLAIM (video frames)`
  - A viewer describes the same moment: "they just changed a part of the prompt to "don't shoot, just dodge" and the behavior changed immediately." `INDEPENDENT` https://news.ycombinator.com/item?id=49854820

**Rate, cost and outcome**
- Top bar: "last 188ms / avg 119ms | 295 calls · 1.7M tok". `VENDOR-CLAIM (video frame, ~18 s)`
  - That is about 5,800 input tokens per call, or about $0.00024 per call at $0.042/M. At 10 calls/s this comes to about $8.7/h, the same order as the blog's "~$7/hour". `INFERENCE`
- Result: no aggregate result was published. Frames show on-screen counters such as "DIRECTOR spawned 22 killed 17" in an arena mode, and level progress on E1M1 with a map view. `VENDOR-CLAIM (video frames)`
- **Code is not public.**
  - The blog promised it: "we intend to not only release an in-depth walkthrough, but also host some events to hack on this." `VENDOR-CLAIM` (blog)
  - As of 2026-09-28 the docs' Demos page does not list it (above). The HN critique that "nothing of the Doom demo was shared" is in 02 §3. `INDEPENDENT`
  - Search snippets mention a TypeSafe × AI Collective community hackathon on 2026-09-26. I could not open a primary page, so this is **unverified**.
- **Tuning.** None is claimed. Asked "Is this actually a general model, or does it need training on the the data set it answers?", the CEO replied: "1. yes a general model 2. no training at all". `VENDOR-CLAIM` https://news.ycombinator.com/item?id=49719245 (parent: https://news.ycombinator.com/item?id=49719167)

#### Wikiracing (vendor)

- **Setup.** A four-panel race on a localhost harness ("TypeSafe AI WIKIRACE"), labelled **"Jev 1.13.0"**, against LLMs. "Model time excludes Wikipedia loading." `VENDOR-CLAIM (video frames)`
- **Results shown** `VENDOR-CLAIM (video frames)`:
  - Baseball → Sun: Jev 1.13.0 finished in 0.419 s with 3 hops. Claude Sonnet 5 took 3.724 s and 4 hops, Claude Haiku 4.5 4.975 s and 6 hops. GPT-5.6 Terra took 9.453 s and 6 hops, with "1 hallucination".
  - Rubber duck → Lamport's bakery algorithm: Jev 0.544 s, 5 hops ("8.63× faster, 2.3× cheaper").
  - Rubber duck → Fisher-Yates shuffle: Jev 0.730 s, 5 hops ("9.97× faster, 70.8× cheaper").
- **Wrapper for more than 255 links.** "For the higher cardinality choices, we do a 2 stage-system of scoring independently then making an explicit choice, hence the occassional slowdown." `VENDOR-CLAIM` (blog)
- **Baselines handicapped.** "this is against the non-reasoning modes of the models (except Astra which was set to the lowest reasoning setting)". `VENDOR-CLAIM` (blog)
- **Community version.** A Wikipedia path finder with the same shape: "Chromium reads the articles. Jev ranks their outgoing links. Python controls the search." It publishes no aggregate results. `INDEPENDENT` https://github.com/komikat/jev-bfs

### 1.2 Community game projects (independent; mostly self-reported)

All of these use the public model: `jev-latest`, `jev-1.13.0`, or `typesafe-ai/jev` via Vercel/OpenRouter. None fine-tunes Jev.

| Game | How the state is given / what Jev decides | Rate / cost | Result | Source (label) |
|---|---|---|---|---|
| **Pokémon Red** (full game) | "the harness reads the game's memory, lists the legal options with some facts about each, and Jev picks one." A* pathing and menus are code; loop guard: "If the same failing choice keeps coming back, the harness samples an alternative." | ~0.4 s per decision; 16,150 decisions; "~$1.65" total | "Jev beat the game." 37 h 40 m, 16 wipes (14 at the Elite Four) | https://github.com/christianmat/jev-pokemon `INDEPENDENT` |
| Same Pokémon run, critique | "Yet another absolutely fake demo where Jev is flipping the coin and harness is doing 99% of the work." | – | – | https://news.ycombinator.com/item?id=49857575 `INDEPENDENT` |
| Pokémon Red, no harness | Only move history and memory, with no pathfinder or route: "Jev was not able to even get to Professor Oak’s lab to get a starter." | – | Failed | https://news.ycombinator.com/item?id=49854051 `INDEPENDENT` |
| Pokémon Red (to the first rival) | RAM becomes a typed snapshot; Jev is asked only at branches. "The catch, up front: it cannot plan. It never sees more than the current decision." | "about 1.3 decisions/sec, $0.14/hour" | Wins the lab rival battle; v0.1 stops at Route 1 | https://github.com/valentynkit/jev-plays-pokemon-red `INDEPENDENT` |
| Pokémon Showdown (1 battle vs Opus 5) | Legal moves as options | "Jev: $0.0029, 37s thinking / Opus 5: $2.35, 6m 29s thinking" | Jev won one battle; the author: "Obviously just one battle as a demonstration" | https://x.com/sid19arya0/status/2100458351440048258 , https://news.ycombinator.com/item?id=49746027 `INDEPENDENT` |
| **Chess** (LLM Chess leaderboard) | "state: { fen, side_to_move }" and "one Choice: all legal UCI moves as options (SAN in the criteria text), max 255" | ~$0.0015 per game, ~36 s per game, 80 games | "jev-latest is around #59, Elo ~243, sitting next to qwen3.6-27b and o4-mini-medium". Against the Dragon engine (L1–L3, 10 games each): 0 wins, 50% draws | https://dev.to/maximsaplin/typesafe-jev-played-chess-and-landed-next-to-reasoning-models-28ga `INDEPENDENT` |
| Tic-tac-toe (checked against a perfect solver) | Four board representations, four prompt versions; `jev-1.13.0` | 36,725 requests, $1.09 | Best-move rate 55.4% to 83.3% depending on wording (see §3.3); "Forks are the floor." | https://github.com/robwent/jev-tic-tac-toe `INDEPENDENT` |
| **Tetris** vs LLMs, real time | "Code enumerates every legal placement and describes each outcome in words" | ~220 ms per move | Real-time versus: Jev beat Haiku 4.5, Gemini 3.8 Flash and Laya. With no clock, Gemini beat Jev: "Given unlimited time it places pieces better than Jev on this seed (0.32 lines per piece against 0.25)" | https://github.com/trungdq88/jev-tetris `INDEPENDENT` |
| Slay the Spire 2 | A mod exposes the state; text plus legal options; one Choice per screen | a few hundred ms per call | "it made it to the first boss on its first real run and died there." | https://github.com/Zboubkiller/jev-plays-sts2 `INDEPENDENT` |
| Minecraft (Canadian-flag build) | "Code still supplies observations, nearest candidate coordinates, relative distances, the fixed flag blueprint"; a direct controller picks 250 ms primitives | 1,848–2,266 decisions per run | "The latest controller completed a recorded run in 801 seconds (13m21s), with 1,848 model decisions". Also: "The earlier video and 131-decision result used high-level control." | https://github.com/ellistev/typesafe-minecraft-demo `INDEPENDENT` |
| StarCraft (shareware mission "Strongarm") | Structured state; game paused during inference | 421 decisions, median 383 ms | "Jev completed Strongarm." on attempt 16. Model: "jev-1.13.0 (requested as jev-latest)" | https://github.com/phyous/tsai-sc `INDEPENDENT` |
| Super Mario Bros. | "The model does not receive screenshots." RAM becomes object-centric JSON; 7 controller actions | every 4–8 frames | Best live run "reaching x=1594 after 110 decisions without dying"; "full-level completion is not yet demonstrated" | https://github.com/fhshaik/typesafe-mario , https://github.com/shantanugoel/mario-jev `INDEPENDENT` |
| Pong | "Pong is entirely numbers, so no coordinate is ever sent to the model." Code buckets positions into phrases | ~379 ms per call | Design write-up; no win rate | https://github.com/safzanpirani/pong-jev `INDEPENDENT` |
| Doom / ViZDoom (community) | A YAML situation report; Jev picks the macro goal, target and movement at "~10 Hz"; code aims: "Exact relative bearing trigonometric calculations steer the crosshair directly onto enemies with zero delay." | ~10 Hz | No score published | https://github.com/AmoghCreator/doom-jev `INDEPENDENT` |
| **ViZDoom, Maze, Snake** (Jev as a baseline) | The same observation interface for Jev, NanoJev and untuned Qwen3-0.6B | – | Jev: ViZDoom Basic 56/128, Predict Position 11/128, Maze 7/10, Snake 8/8. The *trained* NanoJev: 128/128, 27/128, 4/10, 8/8 | https://github.com/TianyuCodings/NanoJev `INDEPENDENT` |
| ViZDoom arena (Jev as a baseline) | Structured scene text | ~115 ms (API) | "TypeSafe Jev (typesafe/jev-1.13) … 5.62 kills" vs Von 9.00; author-reported, own protocol | https://github.com/wfzyx/von `INDEPENDENT` |
| Clash Royale | Computer vision turns video into a JSON state once a second; all 12 strategies, 4 cards and legal squares as options | 1 Hz | Anecdotal | https://github.com/bytelabs-oss/clash-jev `INDEPENDENT` |
| Wordle, checkers | Not found. Searched "Jev Wordle" and HN Algolia | – | – | – |

**What the pattern shows** (`INFERENCE`, from the rows above)
1. **Input.** In every Jev game project, the input is text or JSON produced by code: RAM, engine APIs, a FEN, or computer vision.
   - Jev never sees pixels. That matches the docs, "Text only… No image, audio, or video input" (01 §2).
   - PlayJev notes: "Every Jev-shaped game demo before this one feeds the model text." `INDEPENDENT` https://github.com/OmniJev/PlayJev
2. **Jev's role.** It chooses among legal, pre-annotated options.
   - Perception, pathfinding, arithmetic, look-ahead and aiming are code.
   - Where code does less, results drop sharply: Pokémon without a harness failed; ViZDoom Basic is 56/128 without code aiming.
   - This is consistent with the vendor's own jaggedness notes (numbers, counting, indirection; 01 §7).
3. **"Tuning" in these projects means editing the harness, not the model.** For example: "nothing is hidden behind config files or fine-tuning… Change the wording and the bot's behavior changes with it, no retraining needed". `INDEPENDENT` https://github.com/Zboubkiller/jev-plays-sts2
4. **The trained game players are other models.** When a video shows a "Jev-like" model trained for a game, it is a replica. Such videos are the likeliest source of the impression that "Jev was tuned".
   - NanoJev: a Qwen3-0.6B model trained on 16,333 ViZDoom questions.
   - PlayJev: "Qwen3.5-0.8B-Base fine-tuned to play ten classic browser games from raw pixels".
   - Blocks.ai rebuilt the API on Gemma 4 and played Doom (via WebFetch: "We used Gemma4 base model (not instruct) with max_token 1."; https://blocks.ai/blog/jev-open-model-doom).
   - Others on HN: OpenDecision 400M "plays Doom" (https://news.ycombinator.com/item?id=49787404), and "Minecraft PvP against DiffusionGemma-as-Jev" (https://news.ycombinator.com/item?id=49870906).

---

## 2. Customization options (fine-tuning, custom models, private deployments)

| Offer | Status | Evidence (label) |
|---|---|---|
| Per-customer fine-tuning or LoRA | **Not offered** | "Jev is not fine-tuned or LoRA-adapted with customer data." `VENDOR-CLAIM` https://docs.typesafe.ai/models (the "Customizing Jev" section) |
| Custom weights | **None; shared model** | "the same weights serve every account." `VENDOR-CLAIM`, same page |
| The vendor's customization route | Through the request only | "You shape its answers to your domain through the request rather than through per-account weights" `VENDOR-CLAIM`, same page. The route: proprietary content in `state`; rules in `instructions`/`criteria`; "Decompose broad judgments into atomic questions and combine the outputs in code", including "training a downstream classical model on Jev's probabilities". |
| "Custom and enterprise plans" | Exist, but only stated for **rate limits** and **ZDR** | "Higher limits are available on custom and enterprise plans. Contact sales@typesafe.ai." `VENDOR-CLAIM` https://docs.typesafe.ai/models. ZDR "for enterprise customers" (01 §5). No custom-model wording appears anywhere in the docs (`INFERENCE`, from grepping https://docs.typesafe.ai/llms-full.txt). |
| Private or self-hosted deployment, weights | None | No weights, parameter count or self-hosting (02 §5, [S11]) `INDEPENDENT` |
| Distillation from customer data | Not offered; not mentioned | Also, no training on customer traffic: "Jev is not trained on customer requests or responses." `VENDOR-CLAIM` https://docs.typesafe.ai/models |
| Roadmap: fine-tuning | **Possible, explicitly not promised** | The interviewer: "And so then I would want to fine-tune it or something, right? Which you don’t offer, but you could." The CEO: "On the question of fine-tuning, I could imagine, I could imagine it in the cards." And: "This is not a promise. This is a desire." `VENDOR-CLAIM` https://www.latent.space/p/jev (01:07:45–01:11:54) |
| The CEO's preferred alternative to fine-tuning | Calibration plus a cascade | "My hope is calibration gets that. Calibration plus a cascade of models." His stated concern is that generality helps edge cases, and narrow tuning might lose that. He says being good at "a million other tasks than this one narrow task might make it better at edge cases in that task". `VENDOR-CLAIM`, same |
| Roadmap: other modalities | Open | The interviewer: "the first thing they want is vision ‘cause of the Doom demo". The CEO: "Everything is in the cards". `VENDOR-CLAIM`, same (01:25:12) |
| Version stability | Fixed per version; no LTS yet | "We will not change our models when we deploy them." Also: "we are not promising long-term support for the models". `VENDOR-CLAIM`, same (00:48:11) |

Community expectations such as "Fine tuning Jev once available could address certain concerns" (https://news.ycombinator.com/item?id=49745752) are speculation, not a vendor statement. `INDEPENDENT`

`INFERENCE`: today the only "customization" is the request itself plus whatever you build around it. Treat any future fine-tuning as unannounced.

---

## 3. Tuning without weights, as practised by users

Ranked by evidence strength. The sizes are the authors' own numbers.

### 3.1 Calibration refits (temperature, Platt, isotonic, conformal)

- **Isotonic regression on a few hundred labels** `INDEPENDENT` https://github.com/AnthusAI/Jev-Calibration (jev-1.13.0; 8,801 sentiment examples; 5,280 calibration / 3,521 test)
  - "Isotonic regression, fit on a calibration split, cut the error (ECE) of P(positive) from 0.117 to 0.008 on held-out data, where Platt scaling reached 0.052."
  - "A few hundred labeled examples capture most of the benefit". Isotonic matched or beat Platt at every size from 20 to 5,280.
  - Choice's raw confidence is uninformative below 95%: accuracy is 50–57% across the 50–95% bands. The practical advice: "don't rely on Choice's raw confidence".
- **Two-parameter refit** `INDEPENDENT` https://github.com/Adilmp/does-jev-confidence-mean-anything (civil_comments; 8,000 judgments; jev-1.13.0)
  - "Recalibration is fitted on one half and scored on the half it never saw."
  - ECE fell from 0.157 to 0.023 (19.2% base rate) and from 0.209 to 0.007 (2.9% base rate).
  - But wording alone could not fix it: "No wording fixed it: the strictest still predicted 26% where reality was 5%."
- **The direction of the error depends on the question type** `INDEPENDENT` https://github.com/scienthoon/jev-ood-calibration (900 synthetic tickets)
  - After the author's 2026-09-22 correction, the refit temperatures are Choice 1.30 and Score 1.92 (overconfident) and Noul 0.66 (underconfident).
  - A label the text cannot reveal stays unfixable: 44.7% accuracy on the priority rule, with a mean stated probability of 0.74.
  - `INFERENCE`: fit one refit per question type and per question, never one global temperature.
- **Crash narratives** (2,416 human labels): a refit reduced calibration error 3.3×. This is already in 02 §2.2. `INDEPENDENT`
- **Conformal thresholds** `INDEPENDENT` https://github.com/nikkoxgonzales/jev-certify (CLINC150; 2,412 answers; $0.23)
  - "At a 5% risk target: Jev settles 84.75% of traffic itself with a 2.65% error rate among settled queries". The bound held on held-out data.
  - There is a hard floor: "Jev returns exactly 1.0 on 56.4% of answers and 9 of those are wrong".
  - The bound breaks under prevalence shift (3.6× over the certified bound).
- **Size.** Large for calibration: ECE falls 3× to about 20×, with a few hundred labels. `INFERENCE`: a monotone refit does not change the accuracy or ranking of a single yes/no answer. It fixes the meaning of the probability, which is what thresholds rely on.

### 3.2 Decomposition plus a learned combiner

- **Phishing** `INDEPENDENT` https://github.com/anisselbd/jev-phishing-bench (2,000 emails; jev-1.13.0)
  - One verdict question scores 62.6% (02 §2.1).
  - Five signal questions plus logistic regression: "The logistic regression on Jev's five signals reaches 95.0% [93.5%, 96.2%], AUROC 0.982, against 91.8% for the regression on the two regex features".
  - Labels used: "1 000 emails (half A) choose the signal, its threshold and the regression weights; the other 1 000 (half B) give the numbers."
  - Claude Haiku 4.5 answering the same five questions reaches 93.2%.
- **The vendor's AutoResearch cookbook** `VENDOR-CLAIM` https://docs.typesafe.ai/cookbooks/autoresearch_feature_discovery
  - An LLM proposes questions, Jev answers them for every row, and CatBoost trains on the answers (wine scores; 1,200 dev rows / 800 held out).
  - RMSE: 3.09 predicting the mean; 1.87 after "18 questions from one proposal call, no loop"; 1.77 after "38 questions after five rounds of the loop".
  - The loop's added value: "round 1 -> round 5 on the held-out rows: -0.097 points, 95% CI [-0.147, -0.050]".
  - Run on an older model: "The numbers came from TypeSafe `jev-1.12` and `claude-sonnet-5` on 2026-08-03."
- **Rule decomposition in DSPy** `INDEPENDENT` https://github.com/leepokai/llm-prompt-techniques-on-jev (jev-1.13)
  - Chain (ask the sub-conditions first) moves LegalBench diversity_5 from 81.3 to 85.3, and to 90.7 with the rule applied in code.
  - Three dependent memo questions: 72 → 89 all-correct (chain plus refine).
- **Tic-tac-toe with code look-ahead** over Jev "is there a line" judgments: "reaches about 94%", against 83.3% for the best wording alone. `INDEPENDENT` https://github.com/robwent/jev-tic-tac-toe
- **Size.** The largest accuracy lever, +10 to +30 points, when the task can be split into facts Jev reads well. It needs a labelled set of hundreds to about 1,000 rows to fit the combiner.

### 3.3 Criteria, option wording and question shape

- **Criteria wording** `INDEPENDENT` https://github.com/RastislavDujava/jev-classification-prompting ("Across six domains, 41 paired test items"; jev-1.13.0)
  - A naive question scores 70% (57/82); "criteria written properly" scores 96% (79/82).
  - Improving `instructions` with `criteria` untouched gave −0.04. The lever is the criteria.
- **Representation and one sentence of wording** `INDEPENDENT` https://github.com/robwent/jev-tic-tac-toe (held-out positions; wording tuned on a dev split)
  - Board format and wording give 55.4% (drawn grid) → 57.6% (named cells) → 69.4% (lines spelled out) → 83.3% ("win if you can, otherwise block").
  - "One sentence was worth 14 points."
- **Where the options go** `INDEPENDENT` https://github.com/leepokai/llm-prompt-techniques-on-jev (MMLU-Pro, 500 held-out items)
  - "Where the options go is worth 5.8 points; no prompting technique is worth more than 0.6 in either formulation".
  - Also: "Jev has no scratchpad, so "reason harder" prompts do nothing."
- **An abstain option** `INDEPENDENT` https://github.com/jujumilk3/jev-calibration-audit (KoBBQ)
  - Removing the "unknown" option: "Accuracy 0.950 → 0.000 on unanswerable items, stereotype rate 0.03 → 0.79, at 0.79 confidence. ECE 0.023 → 0.793".
- **Option order.** It matters on hard items. `INDEPENDENT` https://github.com/RINNECODER/jev-behavior-study (arithmetic items, per the robustness index): accuracy is 88.0% (95/108) when the correct option is listed first and 57.4% (62/108) when it is fourth.
  - On most other tasks the effect is negligible: "reversing two options moved probability by 0.005 and flipped 0 of 400 argmaxes" (calibration-audit, as summarized by https://github.com/Yifan-Lan/awesome-jev-robustness).
  - Option-name binding (AUC .81 → .58) is already in 02 §2.4.
- **A label-and-grade loop** `INDEPENDENT` https://github.com/smkrv/jev-calibrate: "the first draft of a frustration question got 18 of 26 labelled messages right, and four of its eight wrong answers came with a confidence of 0.94 or more."
- **Vendor guidance** `VENDOR-CLAIM` https://docs.typesafe.ai/primitives/advanced
  - Criteria can carry structured `what` / `not_for` / `examples` fields: "The example tells the model what each option does and does not cover."
  - The jaggedness notes say Jev reads literally (01 §7).
- **Size.** Up to about +25 points on the task you wrote. It is task-specific and needs a small labelled dev set (tens of items) to avoid fooling yourself.

### 3.4 Few-shot examples inside `state`

- **Allowed.** `VENDOR-CLAIM` https://docs.typesafe.ai/concepts/state — "State can also be a JSON object or array containing related context, examples, and other information that helps the model answer the associated questions."
  - Per-option `examples` inside `criteria` are allowed too: "The field names question, focus, what, not_for, and examples are not part of the API, and none are reserved." (https://docs.typesafe.ai/primitives/choice)
- **Effect** `INDEPENDENT` https://github.com/leepokai/llm-prompt-techniques-on-jev (the DSPy adapter puts demos into the request as structured `examples`)
  - It helps when the examples carry task information the question lacks. On LegalBench diversity_5, direct is 81.3; five shots give 88.7; fifty shots ("many-shot") give 94.7.
  - It is neutral or harmful when Jev already knows the task. On MMLU-Pro "exemplars … cost 2 to 5" points. On BBH the mean is flat (90.4 vs 90.6); examples lift disambiguation_qa and hurt object tracking.
- `INFERENCE`: examples also add input tokens, and the vendor warns about context rot from unrelated state (01 §7). Use a few targeted boundary examples inside `criteria`, not a long generic example block.

### 3.5 Thresholds and cascades; the Jev-Mem control plane

- **The vendor's position** `VENDOR-CLAIM`
  - https://docs.typesafe.ai/confidence — "Start with conservative thresholds, test with your own data, and adjust as you observe results."
  - https://docs.typesafe.ai/models — "If you have tuned confidence thresholds against a specific version, pin that version's ID instead of the alias".
- **Measured cascades** `INDEPENDENT` https://github.com/FirasSX914/Janus (500 items per dataset)
  - Banking77: "80.2% at threshold 0.67 -- better than either model alone", at $0.1033 against $0.2207 for the fallback alone.
  - Web of Science: "no threshold beats the better single model, and the verdict is DO NOT ROUTE."
  - Its advice: "Start with a few hundred labelled rows".
- **Thresholds do not transfer out of distribution** `INDEPENDENT` https://github.com/scarif-labs/jev-software-decision-benchmark (1,102 in-distribution / 185 OOD cases)
  - "JEV's frozen in-distribution threshold did not transfer safely. The threshold 0.62 produced 30 auto-merges at 50.0% precision".
- **A threshold fitter** `INDEPENDENT` https://github.com/abhixhek/jevcal
  - It asks for "a few hundred real examples" and warns: "jev-latest is an alias that moves on every release, so a threshold tuned today can drift tomorrow."
- The cascade pro/con evidence (¼–½ of the cost, versus "at most 1.5 points") is already in 02 §2.1, and the Vercel Gateway fallbacks are in 01 §4.
- **Jev-Mem's control plane** uses fixed, hand-set gates, not fitted ones. `INDEPENDENT` https://arxiv.org/html/2609.23986
  - "Evidence-based stopping requires evidence_sufficient to be at least 0.95 and both missing_evidence and contradiction to be below 0.15."
  - A merge or promote needs a probability of at least 0.85.
  - "These model-reported values are not assumed to be calibrated probabilities."
  - I found no threshold-tuning procedure in the text. `INFERENCE`
- **Size.** Thresholds and cascades trade coverage against error, for example 84.75% automated at 2.65% error. They add little accuracy (+1.4 points on Banking77, none on Web of Science). They must be re-measured per version and when traffic drifts.

### 3.6 Prompt-optimization tools ("DSPy-like")

- **`dspy-jev` (DSPy adapter; GEPA and MIPROv2)** `INDEPENDENT` https://github.com/leepokai/llm-prompt-techniques-on-jev
  - GEPA, with Claude Sonnet 4.5 as the reflection model and Jev as the task model, "~730 Jev calls (a few cents) per task". Results:
    - LegalBench diversity_5: 81.3 → 100.0
    - LegalBench diversity_6: 82.0 → 96.0
    - LegalBench hearsay: 68.0 → 68.0 ("It did nothing on hearsay, where the validation split is 14 rows.")
    - BBH causal_judgement: 67.0 → 74.0
  - The splits were 100 train / 50 val / 150 test on the diversity tasks.
- **JevHarness** (an LLM writes the harness; GEPA evolves it) `INDEPENDENT` https://github.com/TianyuCodings/JevHarness
  - "after 5 reflection rounds, the selected harness improved Eval win rate from 25% (3/12) to 75% (9/12)" on Pokémon Showdown.
  - Caveat from the author: "Eval was used for selection". The gain is optimistic.
- **Vendor AutoResearch** (see §3.2): an automated question-proposal loop. `VENDOR-CLAIM`
- **Tools without published gains** `INDEPENDENT`:
  - tenbin: "Question lint, evaluation, batch evaluation (threshold calibration)" (https://github.com/simota/tenbin).
  - Augustus: a "bounded improvement loop with untouched confirmation data and rollback" (https://github.com/24601/Augustus).

### Summary: the top weight-free levers

| Lever | Typical gain (authors' numbers) | Labels needed | Main caveat |
|---|---|---|---|
| Decompose plus combiner (LR, CatBoost, code rules) | +10 to +32 points (62.6 → 95.0% phishing; LegalBench 81 → 91 with code) | about 1,000 to fit the combiner; hundreds can work | the task must split into facts Jev reads well; a regex may already get close (91.8%) |
| Criteria and question shape (incl. GEPA auto-rewrite) | +5.8 (option placement) to +26 points (criteria 70 → 96%; GEPA 81 → 100) | tens (manual) to ~150 (GEPA train/val) | task-specific; can overfit a small dev set; no effect where Jev already knows the task |
| Calibration refit (isotonic / Platt / temperature, per question type) | ECE 3× to ~20× lower (0.117 → 0.008; 0.209 → 0.007) | a few hundred | fixes the probabilities, not accuracy; Choice confidence needs it most; redo per version |
| Thresholds / cascade | e.g. 84.75% automated at 2.65% error; +1.4 points accuracy on Banking77 | hundreds | does not transfer out of distribution; can be "DO NOT ROUTE" |
| Few-shot in state/criteria | +3 to +13 on rule tasks (LegalBench diversity_5/6); −2 to −5 on knowledge tasks (MMLU-Pro) | 5 to 50 examples | adds tokens; context rot |

---

## 4. Does the model behind the demos differ from the public `jev-1.13`?

**Evidence**
- **The Doom demo ran a research checkpoint name, not a public model ID.**
  - The session label reads "helm · research/v13_snowy_elephant +ctx +guide", and the JUDGE is set to "TS Research". `VENDOR-CLAIM (video frames, ~18 s and ~97 s)`
  - The public docs list only `jev-1.13.0`, with aliases `jev-latest` and `jev-preview` pointing to it; "There is no preview build available right now." `VENDOR-CLAIM` https://docs.typesafe.ai/models
  - `INFERENCE`: "v13" suggests the 1.13 line. Whether that checkpoint's weights equal the served `jev-1.13.0` is unknown.
- **The Wikiracing demo names the public version.** On-screen labels read "Jev 1.13.0". `VENDOR-CLAIM (video frames)`
- **The launch video labels the model differently.** Its structured-output-error chart reads "TypeSafe Jev-1.0 0.00%". `VENDOR-CLAIM (launch video frame, ~100 s)` `INFERENCE`: this is a naming inconsistency, probably a marketing label.
- **Vendor cookbook numbers come from an unreleased earlier version.**
  - "The numbers came from TypeSafe `jev-1.12` and `claude-sonnet-5` on 2026-08-03." `VENDOR-CLAIM` https://docs.typesafe.ai/cookbooks/autoresearch_feature_discovery
  - The re-ranking cookbook sets `TYPESAFE_MODEL = "jev-1.12"` (https://docs.typesafe.ai/cookbooks/rerank_typesafe).
  - "jev-1.12" appears 27 times across the docs' cookbooks. `INDEPENDENT` (my grep of https://docs.typesafe.ai/llms-full.txt)
  - A re-run on 1.13 found "Most of the jump over the published 18 / 35 / 62 is the model version, not the formulation". `INDEPENDENT` https://github.com/leepokai/llm-prompt-techniques-on-jev
- **The served build is dated after the launch videos.** OpenRouter's served build is tagged `jev-1.13-20260917`, two days after launch (01, open question 13).
- **The version policy allows fast change.** "We will not change our models when we deploy them" and "we are not promising long-term support for the models". `VENDOR-CLAIM` (Latent Space, 00:48)
- **No sign of a game-specialized model.**
  - The CEO says "no training at all" (above).
  - The Doom harness supplies a strategy guide, repeated facts and a pathfinder (§1.1).
  - Independent runs on the public `jev-1.13.0` also drive games through harnesses: a StarCraft mission win, a full Pokémon Red completion, Tetris wins.
  - Where code does less of the work, the public model is weak: ViZDoom Basic 56/128 (NanoJev table). `INDEPENDENT`

**Verdict** (`INFERENCE`): the Doom demo was probably not run on the exact public model ID. It used a research checkpoint of the 1.13 line with a heavy harness. Nothing suggests game-specific training. The Doom demo's quality cannot be reproduced exactly, because its harness, state encoding and prompts are unpublished (02 §3). The Wikiracing demo claims the public `jev-1.13.0`.

---

## Coverage gaps

- The vendor's Doom harness code, the promised walkthrough, and a primary page for the 2026-09-26 hackathon were not found.
- The YouTube stream of the Pokémon Red run was not watched; its README summary was used instead.
- Several community results (Von, NanoJev, JevHarness) are author-reported, with their own protocols. They are not controlled head-to-heads.
- I could not tell which cookbook ran on which exact `jev-1.12` build.
- I did not inspect the TypeSafe homepage's "Game of Life" widget.

## Sources (all read 2026-09-28)

**Vendor**
- https://typesafe.ai/blog/introducing-system-one-models-and-jev
- Vimeo: https://vimeo.com/1227495732/5c335e90e5 (Jev-Demo-Doom-Full), https://vimeo.com/1227495711/88074bcc80 (Jev-Demo-Wikirace)
- https://x.com/CompleteSkeptic/status/2099925682726002904 (launch video)
- https://docs.typesafe.ai/models, /demos, /concepts/state, /primitives/advanced, /primitives/choice, /confidence, /cookbooks/autoresearch_feature_discovery, /cookbooks/rerank_typesafe, /llms-full.txt
- https://www.latent.space/p/jev (CEO interview transcript)
- HN (CEO): https://news.ycombinator.com/item?id=49719245 , https://news.ycombinator.com/item?id=49719064
- https://x.com/allietheicon

**Press and HN**
- https://www.theregister.com/ai-and-ml/2026/09/16/typesafe-ai-debuts-model-for-machines-that-plays-doom/5296711 — "It can, for example, play Doom, when fed structured data describing the player's game state."
- https://news.ycombinator.com/item?id=49854820 , https://news.ycombinator.com/item?id=49857575 , https://news.ycombinator.com/item?id=49854051 , https://news.ycombinator.com/item?id=49746027 , https://news.ycombinator.com/item?id=49745752 , https://news.ycombinator.com/item?id=49787404 , https://news.ycombinator.com/item?id=49870906

**Community games**
- https://github.com/christianmat/jev-pokemon
- https://github.com/valentynkit/jev-plays-pokemon-red
- https://x.com/sid19arya0/status/2100458351440048258
- https://dev.to/maximsaplin/typesafe-jev-played-chess-and-landed-next-to-reasoning-models-28ga
- https://github.com/robwent/jev-tic-tac-toe
- https://github.com/trungdq88/jev-tetris
- https://github.com/Zboubkiller/jev-plays-sts2
- https://github.com/ellistev/typesafe-minecraft-demo
- https://github.com/phyous/tsai-sc
- https://github.com/fhshaik/typesafe-mario
- https://github.com/shantanugoel/mario-jev
- https://github.com/safzanpirani/pong-jev
- https://github.com/AmoghCreator/doom-jev
- https://github.com/lukaske/jev-doom-agent
- https://github.com/bytelabs-oss/clash-jev
- https://github.com/komikat/jev-bfs
- https://github.com/TianyuCodings/NanoJev
- https://github.com/OmniJev/PlayJev
- https://github.com/wfzyx/von
- https://blocks.ai/blog/jev-open-model-doom
- https://typesafe-ai-playground.vercel.app/doom (community; the README says "not an official TypeSafe AI product")
- Directories: https://github.com/hellogumbo/awesome-jev , https://github.com/Yifan-Lan/awesome-jev-robustness

**Tuning evidence**
- https://github.com/AnthusAI/Jev-Calibration
- https://github.com/Adilmp/does-jev-confidence-mean-anything
- https://github.com/scienthoon/jev-ood-calibration
- https://github.com/nikkoxgonzales/jev-certify
- https://github.com/anisselbd/jev-phishing-bench
- https://github.com/leepokai/llm-prompt-techniques-on-jev
- https://github.com/TianyuCodings/JevHarness
- https://github.com/RastislavDujava/jev-classification-prompting
- https://github.com/jujumilk3/jev-calibration-audit
- https://github.com/RINNECODER/jev-behavior-study
- https://github.com/smkrv/jev-calibrate
- https://github.com/FirasSX914/Janus
- https://github.com/scarif-labs/jev-software-decision-benchmark
- https://github.com/abhixhek/jevcal
- https://github.com/simota/tenbin
- https://github.com/24601/Augustus
- https://arxiv.org/abs/2609.24052
- https://arxiv.org/html/2609.23986 (Jev-Mem)
