# R4 release plan: memory.ask with the Gemini 3.8 Flash (high) writer, the owner's final production test

Status: DRAFT v1 (2026-09-29), for a codex gpt-6-astra (reasoning high) review before any implementation.
Owner directions (2026-09-29):
- a final test by the orchestrator personally, via MCP calls against PRODUCTION running memory.ask with the Gemini 3.8 Flash HIGH writer;
- the plan is reviewed by Astra high first, under strict rules;
- the orchestrator places the existing Gemini key on the prod VPS;
- curated supersession links are placed on the prod memory before the test.
Gates relaxed or waived by the owner are listed in §9.

## 0. Current state (facts, file:line on wf-memory-ask 8d36f74 = "W", unless marked)
- **Prod:** R3 = 805f4cd on Hostinger VM 2002259 (the ONLY VM we may touch; 4 other VPSs, including a mail server, are off-limits).
  - URL https://mcp.hlmemo.com; alembic head 0008.
  - Caps HOUR 1 / DAY 2 / MONTH 10 USD; OpenRouter prod key limit $50/month.
  - llm.env at /etc/hlmemo/llm.env, with env_file on api + librarian only.
  - DB port 5432 closed; access only via SSH, then `stack.sh exec` inside the containers.
  - Prod links = 0; project hlmemo holds 552 imported items.
- **W = R3 code + 46 commits** (the memory.ask research librarian, tracer, validator fixes, map newest-10, K4 order, opt-in LLM rerank, temporal read fixes, prompt v3.2 opt-in).
  - Its only migration is 0009_memory_map: a create-only cache table; downgrade = DROP TABLE.
  - No compose change, so `--accept-compose-change` is not needed.
- **Measured** (D-195, D-197; blind-reader adjudicated, pr-dev on the replica + 121 curated links; standard tests saturated):

  | Writer | Correct | Contradiction | p95 | $/question |
  |---|---|---|---|---|
  | luna | .63 | .06 | ~15 s | .004 |
  | Gemini 3.8 Flash medium | .74 | .03 | 33–38 s | .021 |
  | Gemini 3.8 Flash high (first run G3) | – | – | 53 s | .0285 (≈ .05 from 2027-01-01 at $1.50/$7.50 per M) |

  The quality reading for high is in progress (G3/G4).
- **Blocking code facts** (all must be resolved before prod):
  - **B1:** thinking tokens are not booked. Google's OpenAI-compatible `usage.completion_tokens` excludes thinking; `_actual_cost` (provider.py:978-985) books prompt × in + completion × out; ledger output_tokens (provider.py:1014); per-question tally (research.py:3060-3064). The result: about 65% of the true cost is booked, so the caps under-protect.
  - **B2:** `JOB_MAX_TOKENS["prose"]=3000` is a constant (research.py:152-162, applied at 3286-3292; build_body sets max_tokens AFTER profile.extra, provider.py:495-497). Gemini thinking reached p95 3.2k and max 9.2k tokens at MEDIUM, which truncates the answer (finish=length → schema fail → a silent luna fallback).
  - **B3:** `HTTP_TIMEOUT_S=20` (research.py:167, provider timeout = min(HLM_LLM_TIMEOUT_S, 20) at research.py:3248) and `DETACHED_HOLD_MAX_S=60` (middleware.py:78) are constants. The writer timeout setting (config.py:298, ≤ 120) is therefore effectively capped at 20 s. The deadline `HLM_RESEARCH_TIMEOUT_S` defaults to 25 (≤ 120).
  - **B4:** the spend guard reserves worst case = ceil(in × 1.1) × p_in + max_tokens × p_out, per attempt (profiles.py:64-70; research_service.py:1142-1149, 1234-1245). The r4 manifest pins `HLM_RESEARCH_MAX_USD ≤ 0.01` (check_librarian.py:120), and one Gemini prose attempt already reserves ~$0.011 at 3000 tokens and ~$0.08 at 16k. So every Gemini write would hit budget_stop.
  - **B5:** install_llm_env.sh rewrites llm.env from the template. It preserves only the caps and keys that match `(API_KEY|TOKEN|SECRET|PASSWORD)$` AND already exist in the new text (:116-136). It reads keys only for the primary/fallback/task-fallback profiles (:241-262), never for `HLM_RESEARCH_WRITER_PROFILE`. So a hand-placed GEMINI_API_KEY is lost on the next install. check_librarian `key_set` ignores the writer profile (:245-251), and "a preserved wrong key passes" (D-122 residual: the probe does not authenticate).
  - **B6:** the W template pins MAP_SUMMARY ON (llm.env.example:52), but the evidence says OFF (D-192: summaries made results worse, .30). ANSWER_MODE/ATTRIBUTION/RERANK/WRITER_PROFILE are only commented out (:63-79), while the code defaults are claims / sources / off (config.py:270, 283, 309). Prod would therefore run the WRONG mode unless the template sets them.
  - **B7:** the `hlm links backfill` apply/revert CLI exists only on wf-supersede-backfill (B: cli/links.py:274-337, ops/backfill_links.py), not in W or R3. R3 already has the links table and event kinds (no migration needed).
  - **B8:** prod pulls the ref from GitHub (deploy.sh:34; remote-deploy.sh:73). main is 71 commits ahead of origin; W and B have no upstream.
  - **B9:** Hostinger keeps ONE snapshot per VM, and a new snapshot overwrites the old one. The R3 snapshot 377499 has expired.
  - **B10:** a rollback (`deploy.sh --rollback`) restores the previous commit, image, llm.env snapshot AND the quiesced DB dump. Writes after the deploy (including the prod links) are lost; there is no PITR.

## 1. Scope of R4 (release candidate = branch `r4-rc`, cut from W 8d36f74)
Code commits, each with tests; no behaviour change when the new settings are at their defaults:
1. **Cost fix (B1).**
   - OpenRouter path: keep `usage.cost` when present.
   - Otherwise book output = `completion_tokens` + `completion_tokens_details.reasoning_tokens` if present. Else, when `total_tokens > prompt_tokens + completion_tokens`, use `total_tokens − prompt_tokens`.
   - Apply the same number to the ledger `output_tokens` and the per-question tally.
   - Tests: an OpenRouter-shaped usage, a Google-shaped usage (thinking excluded), a missing-usage case (worst case charged, as today), and a regression on luna.
2. **Configurable limits (B2, B3), as settings with defaults equal to today's constants:**
   - `HLM_RESEARCH_PROSE_MAX_TOKENS` (default 3000, ≤ 32000; applied in job_spec for prose/expand);
   - `HLM_RESEARCH_HTTP_TIMEOUT_S` (default 20, ≤ 180);
   - `HLM_DETACHED_HOLD_MAX_S` (default 60, ≤ 240).
   - The deadline `HLM_RESEARCH_TIMEOUT_S` le raised to 240.
   - Tests: the defaults are unchanged (golden request bodies), and the overrides apply.
3. **Writer profile** `profiles/google-gemini38-flash-high.toml` (committed; no key):
   - base https://generativelanguage.googleapis.com/v1beta/openai, model gemini-3.8-flash;
   - `HLM_LLM_API_KEY="env:GEMINI_API_KEY"`;
   - `extra = { reasoning_effort = "high", response_format = { type = "json_object" } }`;
   - prices 0.75/3.75, with a comment noting 1.50/7.50 from 2027-01-01;
   - `disabled_tasks = ["risk_judge"]`.
4. **Installer and checker (B5).**
   - install_llm_env.sh also resolves the key names of `HLM_RESEARCH_WRITER_PROFILE` (and `HLM_FALLBACK_PROFILE__RERANK`) from `--key-file`.
   - The template carries an empty `GEMINI_API_KEY=` line, so an installed value is preserved.
   - check_librarian: `key_set` includes the writer profile's key, and an **authenticated** writer probe (1 tiny request with max_tokens 16, JSON; must return 200 + parseable) runs in `evaluate --release r4`.
   - Tests: the key is preserved across a re-install, a missing key → evaluate FAIL, a wrong key → probe FAIL.
5. **R4 template and manifest (B4, B6)** in `deploy/llm.env.example` + check_librarian r4 pins:

   | Key | Value |
   |---|---|
   | `HLM_ENV_RELEASE` | r4 |
   | `HLM_RESEARCH_ENABLED` | true |
   | `HLM_MAP_SUMMARY_ENABLED` | **false** (deviation from the earlier R4 manifest; D-192/D-195 evidence) |
   | `HLM_RESEARCH_ANSWER_MODE` | prose |
   | `HLM_RESEARCH_ATTRIBUTION` | llm |
   | `HLM_RESEARCH_RERANK` | llm |
   | `HLM_RESEARCH_WRITER_PROFILE` | google-gemini38-flash-high |
   | `HLM_RESEARCH_WRITER_TIMEOUT_S` | 120 |
   | `HLM_RESEARCH_HTTP_TIMEOUT_S` | 150 |
   | `HLM_DETACHED_HOLD_MAX_S` | 180 |
   | `HLM_RESEARCH_TIMEOUT_S` | 170 |
   | `HLM_RESEARCH_PROSE_MAX_TOKENS` | 16000 |
   | `HLM_RESEARCH_MAX_USD` | 0.12 (covers one worst-case Gemini attempt ≈ $0.08 + luna planner/rerank/attribution ≈ $0.01 + margin) |
   | `HLM_LLM_TIMEOUT_S` | 180 |

   The fallbacks and D-094 mapping are unchanged. The manifest limit checks change to MAX_USD ≤ 0.12, and the month cap to the owner's value (§9).
6. **Backfill CLI (B7):** port `hlm links backfill` (apply/revert/dry-run with `--proposals`) + ops/backfill_links.py and its tests from B into r4-rc. It is LLM-free on apply/revert and writes ONE librarian event (operator device) per apply. The automatic proposer stays out, or disabled (D-195: precision .69, rejected).
7. **Docs:** a RUNBOOK "R4 release" section, a DECISIONS entry, and the MCP tool description updated to say "~10–60 s".

## 2. Pre-deploy validation (local, no prod)
1. The full unit + integration suites, ruff, and the replay gates:
   - K4 replay = prototype;
   - R2 validator counts;
   - temporal render 121/121 on `hlm_research_final`;
   - the tracer's zero-behaviour-change test.
2. **Local prod-like smoke:** build the R4 image, run the prod compose locally against a COPY of the replica, and install the R4 template via `install_llm_env.sh --key-file <temp 0600 file>`. Then:
   - `check_librarian evaluate --release r4` PASS, with the authenticated writer probe;
   - 10 memory.ask calls: writer_used == google-gemini38-flash-high on ≥ 9/10; no truncation; ledger cost within ±10% of the Google token cost; p95 recorded;
   - the MCP client (Claude Code) completes a 60–120 s tool call. If the client default cuts it, set `MCP_TOOL_TIMEOUT` (owner's settings; ask first).
3. **Secret scan (gitleaks)** over main + r4-rc. Then push main and r4-rc to origin (the public repo; docs/private is gitignored).

## 3. Prod data preparation (read-only first)
1. **Export** the current prod items (memory_versions current + titles/bodies/valid_from) via SSH → `stack.sh exec -T db psql … COPY … TO STDOUT` → a local file under docs/private (0600). Read-only.
2. **Curation (Claude curator, chain-aware, memory-only)**, as for the replica (D-195). **3 independent readers precision-read ALL proposals.**
   - Gate: precision ≥ .95 and 0 high-harm false.
   - Drop the ambiguous ones and any span that also holds a still-current statement.
   - The approved proposals carry PROD ids.
3. **The final-test question set** (hold-out, never seen by the implementer):
   - an independent question writer builds ~40 questions from the PROD export (the pr-dev category mix plus unanswerables), with gold and must_mention;
   - sealed (sha256 recorded);
   - the orchestrator does not read the gold before asking.

## 4. Deploy (prod, VM 2002259 only)
1. **Snapshot** VM 2002259 (Hostinger MCP `VPS_createSnapshotV1`; it overwrites the expired R3 snapshot). Wait for completion and record the id.
2. `deploy.sh hlm-deploy <r4-rc full SHA>` **with the R3 env still installed**. Interim check: /ready 200, the R3 behaviour intact (research OFF under the R3 env).
3. **Env switch:**
   - Build a temp key file (0600, in a private dir, deleted after): `GEMINI_API_KEY` from the local .env plus the OpenRouter prod key reference (whatever install_llm_env already uses).
   - Run `install_llm_env.sh --state deploy/.local/153.92.1.166 --key-file <temp>` from the r4-rc checkout, WITHOUT `--reset-operator-values`, so the owner's caps are kept, unless §9 changes them.
   - Then `check_librarian evaluate --release r4`.
   - The key is never printed; verify only a sha256 prefix.
4. **Gates:** `remote_gates.sh --url https://mcp.hlmemo.com --state … --librarian` (drill ON); `hlm_ops.sh status`; `postgres-closed`.
5. **Smoke via MCP** from the orchestrator's Claude Code session: 3 memory.ask calls. Check writer_used, the latency, and the ledger cost vs the Google console.
6. **Apply the curated links** (prod data change):
   - `stack.sh exec -T api hlm links backfill --project hlmemo --apply --proposals /tmp/<file>`, with the file copied in via `stack.sh cp` or stdin; /tmp is a 64 MB tmpfs.
   - Record the event id.
   - Render check (LLM-free) on prod: each older span shows its status.
   - The revert command is prepared (`--revert`, one link_supersede event).

## 5. Final test (the orchestrator, via MCP, in this chat)
- Ask every sealed question through `memory.ask` exactly as a consumer would, drilling or pulling raw on the returned handles when needed.
- Record the answers, latency and cost per question.
- Afterwards: blind reader adjudication (3 readers + a calibration overlap) against the sealed gold, and the orchestrator's consumer verdict (C1 utility rubric consumer_v1).
- Report: correct, contradiction, abstain, p50/p95, $/question (by Google tokens and by the ledger), writer_used share, and fallbacks.

## 6. Rollback triggers and procedure
**Triggers**, any of:
- gates FAIL;
- /ready not 200 for > 2 min;
- error/timeout > 10% in the smoke or the first 20 questions;
- ledger vs Google cost off by > 25%;
- a spend-cap trip in the first hour;
- any cross-project leak.

**Procedure:**
1. Links only: `hlm links backfill --revert` (one event).
2. Behaviour only: re-install the R3 env with the R4 code kept (research OFF). This is allowed by the provenance flow; verify it.
3. Full: `deploy.sh --rollback hlm-deploy` restores R3 code, image, env and the pre-R4 dump. Writes after the deploy are lost; accepted, since the memory is read-mostly during the test window.
4. Last resort: Hostinger snapshot restore (~30 min).

## 7. Threat model (for the review)
| Id | Threat |
|---|---|
| T1 | Cost runaway: uncounted thinking, caps too high or too low, retries/fallbacks multiplying worst-case reservations, 2027 price doubling, the Google bill vs the ledger drifting |
| T2 | Secret exposure: GEMINI_API_KEY in git, logs, traces, process args, shell history, check output, temp files, container env dumps, or the llm.env snapshot files kept by the rollback machinery (they will now contain the Gemini key) |
| T3 | Data integrity: curated links on prod (false supersession marking current facts outdated, stale ids, partial apply, revert correctness, replay determinism, interaction with rollback) |
| T4 | Availability: long requests (≤ 170 s) holding workers/DB connections, MAX_IN_FLIGHT 4, breaker behaviour, head-of-line blocking for other tools (memory.query/write), Caddy/uvicorn timeouts, the MCP client cutting calls |
| T5 | Privacy: prod memory content sent to Google (paid tier) and OpenRouter (ZDR); tracer must stay OFF in prod |
| T6 | Release safety: provenance/fingerprint checks with the new keys, interrupted installs, a snapshot overwrite, only VM 2002259 touched, the review-77 residuals (D-122) still open |
| T7 | Scope isolation: no cross-project leakage via memory.ask (C7 tests) |
| T8 | Silent quality degradation: the writer silently falling back to luna, alias drift, a wrong profile, truncation |
| T9 | Push exposure: pushing to the public repo publishes everything tracked; `docs/private` must stay untracked |

## 8. Severity rubric (fixed before the review)
| Severity | Definition |
|---|---|
| CRITICAL | Could leak a secret or private memory content, irreversibly corrupt or destroy memory data, cause unbounded or uncapped spend, or touch a VPS other than 2002259 |
| HIGH | Likely to cause a prod outage, a failed or incomplete rollback, spend beyond the configured caps, an incorrect data change needing a restore, or a silent wrong writer/config in prod |
| MEDIUM | Degraded quality or latency, operational burden, or a missing check with a clear workaround |
| LOW | Documentation, naming, cosmetic |

## 9. Owner decisions (recorded or needed)
- **Recorded:**
  - a paid Google project; the key is used directly and placed by the orchestrator;
  - curated links on prod before the test;
  - Astra-high review of this plan;
  - D-130 gate items knowingly waived for this test: p95 ≤ 20 s and ≤ $0.01/q.
- **NEEDED before the deploy:** the prod spend caps. Proposed HOUR 3 / DAY 8 / MONTH 60 USD for the test month. The measured high cost is ~$.03/q (2026) → ~$.05 (2027); 20 questions/day ≈ $17–30/month plus the librarian.
- **NEEDED:** whether the OpenRouter prod key limit ($50/month) stays.
