# R4 release plan: memory.ask with the Gemini 3.8 Flash (medium) writer, the owner's final production test

Status: v2 (2026-09-29). v1 was reviewed by codex gpt-6-astra (reasoning high) in consult 89 (GO-WITH-FIXES, R-1…R-17). v2 carries the round-1 dispositions (§10) and goes to review round 2 (§11) before any prod step.

Changelog v1 → v2:
1. Prod writer = `google-gemini38-flash-medium`, not high (D-198: .74 vs .72, the same within noise, at −28% cost and half the p95); caps HOUR 3 / DAY 8 / MONTH 60 USD in a separate `_BUDGETS_R4` (§1.5, §4.3, §9).
2. Implementation split: branch `r4-code` (src) and branch `r4-deploy` (deploy, RUNBOOK), merged into `r4-rc` after both gates pass (§1).
3. Plan-level fixes: exact link apply/revert commands (R-7, §4.6, §6.1), local load gate and prod concurrency/p95 stop (R-10, §2.1, §5, §6), local smoke target with pass predicates (R-13, §2.2), pre-push checks (R-15, §2.3).
4. Pre-registered meaning of the final test, KEEP / REVERT / OWNER CALL (R-16, §5.1); R4 cannot self-certify D-130 Production Ready.
5. v1 facts contradicted by the review or the code are fixed in place and marked "(corrected v2)"; new §10 (Astra round-1 dispositions) and §11 (review round 2 scope).

Owner directions (2026-09-29):
- a final test by the orchestrator personally, via MCP calls against PRODUCTION running memory.ask with the Gemini 3.8 Flash MEDIUM writer (changed v2 from HIGH, D-198);
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
- **Measured** (D-195, D-197, D-198; blind-reader adjudicated, pr-dev on the replica + 121 curated links; standard tests saturated):

  | Writer | Correct | Contradiction | p95 | $/question |
  |---|---|---|---|---|
  | luna | .63 | .06 | ~15 s | .004 |
  | Gemini 3.8 Flash medium (the R4 prod writer) | .74 | .03 | 33–38 s | .021 (≈ .0405 from 2027-01-01) |
  | Gemini 3.8 Flash high (G3/G4) | .72 | .00 / .04 | 53–69 s | .0298 (≈ .0562 from 2027-01-01 at $1.50/$7.50 per M) |

  High is measured (D-198): .72 vs medium .74, the same within run noise, at +39% cost and ~2× p95. The owner picked MEDIUM for prod and the final test.
- **Blocking code facts** (all must be resolved before prod):
  - **B1:** thinking tokens are not booked. Google's OpenAI-compatible `usage.completion_tokens` excludes thinking; `_actual_cost` (provider.py:978-985) books prompt × in + completion × out; ledger output_tokens (provider.py:1014); per-question tally (research.py:3060-3064). The result: about 65% of the true cost is booked, so the caps under-protect.
  - **B2:** `JOB_MAX_TOKENS["prose"]=3000` is a constant (research.py:152-162, applied at 3286-3292; build_body sets max_tokens AFTER profile.extra, provider.py:495-497). Gemini thinking reached p95 3.2k and max 9.2k tokens at MEDIUM, which truncates the answer: finish=length → schema fail → one retry → a second schema fail ends the question with `schema_fail`. There is NO luna fallback on a schema fail in W (provider.py:615-617, 737-741; research_service.py:1399); luna is used only on Google 5xx/timeout/breaker (provider.py:583). (corrected v2, R-9: v1 said "a silent luna fallback")
  - **B3:** `HTTP_TIMEOUT_S=20` (research.py:167, provider timeout = min(HLM_LLM_TIMEOUT_S, 20) at research.py:3248) and `DETACHED_HOLD_MAX_S=60` (middleware.py:78) are constants. The writer timeout setting (config.py:298, ≤ 120) is therefore effectively capped at 20 s. The deadline `HLM_RESEARCH_TIMEOUT_S` defaults to 25 (≤ 120).
  - **B4:** the spend guard reserves worst case = ceil(in × 1.1) × p_in + max_tokens × p_out, per attempt (profiles.py:64-70; research_service.py:1142-1149, 1234-1245). The r4 manifest pins `HLM_RESEARCH_MAX_USD ≤ 0.01` (check_librarian.py:120), and one Gemini prose attempt already reserves ~$0.011 at 3000 tokens and ~$0.08 at 16k. So every Gemini write would hit budget_stop.
  - **B5:** install_llm_env.sh rewrites llm.env from the template. It preserves only the caps and keys that match `(API_KEY|TOKEN|SECRET|PASSWORD)$` AND already exist in the new text (:116-136). It reads keys only for the primary/fallback/task-fallback profiles (:241-262), never for `HLM_RESEARCH_WRITER_PROFILE`. So a hand-placed GEMINI_API_KEY is lost on the next install. check_librarian `key_set` ignores the writer profile (:245-251), and "a preserved wrong key passes" (D-122 residual: the probe does not authenticate).
  - **B6:** the W template pins MAP_SUMMARY ON (llm.env.example:52), but the evidence says OFF (D-192: summaries made results worse, .30). ANSWER_MODE/ATTRIBUTION/RERANK/WRITER_PROFILE are only commented out (:63-79), while the code defaults are claims / sources / off (config.py:270, 283, 309). Prod would therefore run the WRONG mode unless the template sets them.
  - **B7:** the `hlm links backfill` apply/revert CLI exists only on wf-supersede-backfill (B: cli/links.py:274-337, ops/backfill_links.py), not in W or R3. R3 already has the links table and event kinds (no migration needed).
  - **B8:** prod pulls the ref from GitHub (deploy.sh:34; remote-deploy.sh:73). main is 71 commits ahead of origin; W and B have no upstream.
  - **B9:** Hostinger keeps ONE snapshot per VM, and a new snapshot overwrites the old one. The R3 snapshot 377499 has expired.
  - **B10:** a rollback (`deploy.sh --rollback`) restores the previous commit, image, llm.env snapshot AND the quiesced DB dump. Writes after the deploy (including the prod links) are lost; there is no PITR.

## 1. Scope of R4 (release candidate = branch `r4-rc`: W 8d36f74 + the merges of `r4-code` and `r4-deploy`)
Code commits, each with tests; no behaviour change when the new settings are at their defaults.

**Implementation split (v2, in progress).** Both branches start from W 8d36f74. Each is merged into `r4-rc` only after its own gate passes (§2.1).
- **`r4-code`** (src, profiles, tests) owns: §1.1 cost normalization (B1/R-8); §1.2 configurable limits (B2/B3) with expand kept at 1500 (R-17); §1.3 the two Gemini profiles with `price_valid_until` (R-5); §1.8 the writer retry budget and schema-fail fallback to luna (R-9); §1.9 the writer counters in ops status (R-6); the `python -m hlmemo.ops probe-writer` command of §1.4 (R-14); §1.6 the backfill CLI port with project checks under lock (R-1/R-12); the R-10 load-gate integration test (§2.1).
- **`r4-deploy`** (deploy, RUNBOOK) owns: §1.4 installer and template (B5), FINGERPRINT_KEYS (R-4) and the check_librarian r4 manifest with the trace-dir FAIL (R-11), probe-writer in evaluate (R-14) and the price-date FAIL/WARN (R-5); §1.5 the R4 template values and `_BUDGETS_R4`; §1.10 the behaviour-only rollback via `install_llm_env.sh --release-template` with R3-compatible caps (R-3), the protection of the rollback safety dump with fail-closed recovery (R-2), and the review-77 residuals list; §1.7 the RUNBOOK R4 section.

1. **Cost fix (B1, R-8; `r4-code`).**
   - OpenRouter path: keep `usage.cost` when present. It stays the USD authority; the ledger token counts are normalized separately (next bullet).
   - Otherwise output tokens = the LARGEST of: `completion_tokens`; `completion_tokens` + `completion_tokens_details.reasoning_tokens`, when the profile declares that reasoning is billed on top of completion (Google's OpenAI-compatible API: yes); and `total_tokens − prompt_tokens`, when `total_tokens` is present. v1's rule ("reasoning if present, else total") booked 100 output tokens for prompt 1000 / completion 100 / reasoning 0 / total 3100; the v2 rule books 2100. (corrected v2, R-8)
   - Missing usage, or contradictory usage (e.g. `total_tokens < prompt_tokens + completion_tokens`), is never booked as an exact cost: it settles at the worst-case reservation, as missing usage does today.
   - Apply the same number to the ledger `output_tokens` and the per-question tally.
   - Tests (fixtures per provider profile): an OpenRouter-shaped usage with `usage.cost`; a Google-shaped usage (thinking excluded); reasoning included in completion; reasoning absent; reasoning 0 with a larger total (the 1000/100/0/3100 case); contradictory metadata; a missing-usage case (worst case charged, as today); and a regression on luna.
2. **Configurable limits (B2, B3; `r4-code`), as settings with defaults equal to today's constants:**
   - `HLM_RESEARCH_PROSE_MAX_TOKENS` (default 3000, ≤ 32000; applied in job_spec for prose ONLY; expand keeps its 1500 constant). (corrected v2, R-17: v1 applied it to prose/expand, which doubles expand's 1500 at the default)
   - `HLM_RESEARCH_HTTP_TIMEOUT_S` (default 20, ≤ 180);
   - `HLM_DETACHED_HOLD_MAX_S` (default 60, ≤ 240).
   - The deadline `HLM_RESEARCH_TIMEOUT_S` le raised to 240.
   - Tests: the defaults are unchanged (golden request bodies, including an expand body with max_tokens 1500), and the overrides apply.
3. **Writer profiles (`r4-code`; committed; no key):** `profiles/google-gemini38-flash-medium.toml` (the prod writer; changed v2, D-198) and `profiles/google-gemini38-flash-high.toml` (kept; the r4 manifest accepts either). Both:
   - base https://generativelanguage.googleapis.com/v1beta/openai, model gemini-3.8-flash;
   - `HLM_LLM_API_KEY="env:GEMINI_API_KEY"`;
   - `extra = { reasoning_effort = "medium", response_format = { type = "json_object" } }` (the high profile: `reasoning_effort = "high"`);
   - prices 0.75/3.75 with `price_valid_until = 2026-12-31` (v2, R-5). A profile whose date has passed fails closed: it makes no call, so nothing is spent or reserved at stale prices. `evaluate --release r4` FAILs on an expired date and WARNs before it (`r4-deploy`, §1.4). The 2027-01-01 prices (1.50/7.50) need a new profile commit with new prices and a new date; v1's comment-only note would have booked about half the real cost (10k in / 16k out: reserved $0.06825, real $0.13650);
   - `disabled_tasks = ["risk_judge"]`.
4. **Installer and checker (B5, R-4, R-11, R-14; `r4-deploy`, the probe command in `r4-code`).**
   - install_llm_env.sh also resolves the key names of `HLM_RESEARCH_WRITER_PROFILE` (and `HLM_FALLBACK_PROFILE__RERANK`) from `--key-file`. Every installer run still needs a readable `--key-file`, rollback installs included (install_llm_env.sh:231).
   - The template carries an empty `GEMINI_API_KEY=` line, so an installed value is preserved.
   - check_librarian: `key_set` includes the writer profile's key.
   - **Writer probe (v2, R-14).** v1 put the authenticated probe on the existing `--probe` path, which runs only in the librarian's collect step, so the API's own credential was never checked. v2: `python -m hlmemo.ops probe-writer` (`r4-code`) runs in-process in the **api** container with the API's effective writer profile and credential. It sends one synthetic prompt: one attempt, no retry, no fallback, max_tokens 16, a short timeout. Its output is JSON with the status, the HTTP code and the error type only (never the key, a header or a body). PASS = HTTP 200 and a parseable OpenAI-shaped protocol response. `finish_reason=length` at 16 tokens is a PASS, not a credential failure. The model's JSON is not checked. Cost ≈ $0.0001. `evaluate --release r4` runs it via `stack.sh exec -T api` and FAILs without a PASS (`r4-deploy`).
   - **Tracer OFF (v2, R-11).** `evaluate --release r4` FAILs when the api's effective `research_trace_dir` is non-empty.
   - **Fingerprint (v2, R-4).** `FINGERPRINT_KEYS` (deploy/scripts/llm_env_release.py) gains the writer profile, answer mode, attribution, rerank, the research/HTTP/writer/LLM timeouts, the detached hold, the prose limit, `HLM_RESEARCH_MAX_USD`, the three caps and `HLM_LLM_BUDGET_DISABLED`. Both services (api, librarian) must report them.
   - Tests: the key is preserved across a re-install; a missing key → evaluate FAIL; a wrong key in the api only (librarian key right) → probe FAIL; a sentinel key never appears in the probe's stdout/stderr; a stale `HLM_RESEARCH_TRACE_DIR=/tmp/traces` in the api env → evaluate FAIL; two envs that differ only in the writer, the HTTP timeout or budget-disabled → a fingerprint mismatch is listed (consult 89 got `mismatches=[]`).
5. **R4 template and manifest (B4, B6; `r4-deploy`)** in `deploy/llm.env.example` + check_librarian r4 pins:

   | Key | Value |
   |---|---|
   | `HLM_ENV_RELEASE` | r4 |
   | `HLM_RESEARCH_ENABLED` | true |
   | `HLM_MAP_SUMMARY_ENABLED` | **false** (deviation from the earlier R4 manifest; D-192/D-195 evidence) |
   | `HLM_RESEARCH_ANSWER_MODE` | prose |
   | `HLM_RESEARCH_ATTRIBUTION` | llm |
   | `HLM_RESEARCH_RERANK` | llm |
   | `HLM_RESEARCH_WRITER_PROFILE` | google-gemini38-flash-medium (changed v2, D-198; the manifest also accepts google-gemini38-flash-high) |
   | `HLM_RESEARCH_WRITER_TIMEOUT_S` | 120 |
   | `HLM_RESEARCH_HTTP_TIMEOUT_S` | 150 |
   | `HLM_DETACHED_HOLD_MAX_S` | 180 |
   | `HLM_RESEARCH_TIMEOUT_S` | 170 |
   | `HLM_RESEARCH_PROSE_MAX_TOKENS` | 16000 (unchanged v2; D-198: no truncation at 16k, even at high) |
   | `HLM_RESEARCH_MAX_USD` | 0.12 (unchanged v2). Worst case at 10k input (consult 89 Q2): one Gemini attempt $0.06825 + one luna fallback $0.01420 + planner/rerank/attribution $0.009225 = $0.0917, which fits. A second Gemini attempt (2 × Gemini + luna + aux = $0.1599) does not fit; §1.8 decides that path (v2, R-9) |
   | `HLM_LLM_TIMEOUT_S` | 180 |
   | `HLM_LLM_BUDGET_HOUR_USD` / `_DAY_USD` / `_MONTH_USD` | 3 / 8 / 60 (v2, owner, D-198) |
   | `HLM_LLM_BUDGET_DISABLED` | false |
   | `HLM_RESEARCH_TRACE_DIR` | absent or empty (v2, R-11: evaluate FAILs otherwise) |

   The fallbacks and D-094 mapping are unchanged. The manifest limit check changes to MAX_USD ≤ 0.12. The r4 budgets move to a separate `_BUDGETS_R4` (caps 3 / 8 / 60, month_max_usd 60); R3's `_BUDGETS` (month_max_usd 10) stays unchanged, so an R4 cap change never leaks into R3 and an R3 env never passes with R4 caps (v2, R-3). For the §6.2(b) rollback option, the r4 manifest must also accept an unset writer (= the luna research profile); `r4-deploy` confirms this in round 2.
6. **Backfill CLI (B7, R-1, R-12; `r4-code`):**
   - Port `hlm links backfill` (apply/revert with `--proposals` and `--dry-run`, the preview) + ops/backfill_links.py and its tests from B into `r4-code`, WITH their dependencies: the helper API changes in ops/explicit_links.py (`_record(client=...)`, `explicit_links(by=...)`), and with no import of the excluded proposer modules (`core/supersede_candidates`). (corrected v2, R-12: v1's two-file port does not import or run on W)
   - It is LLM-free on apply/revert and writes ONE librarian event (operator device) per apply. The automatic proposer stays out, or disabled (D-195: precision .69, rejected).
   - **Project check under lock (v2, R-1).** Under the endpoint locks, apply verifies that BOTH heads of every pair are current heads in the requested project. One project mismatch or one stale head rejects the WHOLE apply: zero events, zero links, a non-zero exit. (corrected v2: B skipped stale rows and did not check project membership, backfill_links.py:587-620)
   - Revert is project-wide: it closes ALL live `by=backfill` links of the project in ONE `link_supersede` event, not only the links of one apply event (B backfill_links.py:642). The exact commands are in §4.6 and §6.1.
   - Gate on the local R4 image (R-12): apply → replay → revert; the applied/stale/dropped counts equal the expected set; after the revert there are 0 live backfill links. Plus the R-1 repro: projects A and B, a proposal labelled A that carries two live heads of B → rejected, 0 events, 0 links.
7. **Docs:** a RUNBOOK "R4 release" section (`r4-deploy`), a DECISIONS entry (main), and the MCP tool description updated to say "~10–60 s" (src, `r4-code`).
8. **Writer retry budget and schema-fail fallback (v2, R-9; `r4-code`).** In W a second schema fail, a truncation included, ends the question with `schema_fail` (B2), and a Gemini retry does not fit $0.12 at worst case (§1.5).
   - A Gemini schema fail (finish=length included) is retried only when the retry's worst-case reservation fits the remaining per-question budget. Otherwise, and after a second schema fail, the writer falls back to luna once, instead of `budget_stop` or `schema_fail`.
   - Every fallback is counted by outcome (5xx, timeout, breaker, schema_fail, budget) in the §1.9 counters, and counts toward the §5.1 and §6 fallback share.
   - Tests at MAX_USD 0.12 and 10k input: Gemini + luna + aux completes; a 16k-token invalid-JSON Gemini attempt is followed by luna, not by a second Gemini attempt and not by budget_stop; the booked total is ≤ $0.12; the fallback is counted.
9. **Writer counters (v2, R-6; `r4-code`).** The api records the writer actually used and every fallback. `hlm_ops.sh status` shows `writer_used_24h` (count per profile), `writer_fallback_24h` (count and share of asks) and `writer_outcomes_24h` (count per outcome). Test: Gemini mocked to 5xx and luna answering → both the response flag AND ops status show the fallback.
10. **Rollback hardening (v2, R-2, R-3, D-123; `r4-deploy`).**
    - R-2: while a rollback journal is open, its safety dump is excluded from backup rotation (deploy/backup/retention.py). Backup, restore and recovery share one lock. After a destructive step, a recovery that finds the safety dump missing stops fail-closed and does NOT call `end-rollback` (rollback.sh:230-243, 256-261). This restores the R4 hardening precondition of D-123.
    - R-3: `install_llm_env.sh --release-template <R3 template>` installs the R3 template with R3-compatible caps (HOUR 1 / DAY 2 / MONTH 10) and keeps the keys. The exact argument form goes in the RUNBOOK R4 section.
    - The review-77 residuals list (D-122/D-123) in the RUNBOOK R4 section: each item marked closed or accepted, with its reason.
    - Tests: R-2 fault injection (a `rollback_destructive=true` journal, the safety dump removed by rotation, a retry with a healthy service start → recovery refuses, the journal stays open); R-3 (an R4 env with MONTH 60 → `--release-template` R3 → `check_librarian evaluate --release r3` PASS, empty env_switch journal).

## 2. Pre-deploy validation (local, no prod)
1. The full unit + integration suites, ruff, and the replay gates, on each branch and again on the `r4-rc` merge:
   - K4 replay = prototype;
   - R2 validator counts;
   - temporal render 121/121 on `hlm_research_final`;
   - the tracer's zero-behaviour-change test;
   - the §1.6 backfill gate (apply → replay → revert) and the R-1 repro;
   - **the R-10 load gate (the `r4-code` integration test), a precondition for everything after it (v2):** 4 concurrent memory.ask calls held near the 170 s deadline while memory.query, memory.write and /ready are sampled. PASS = query/write p95 ≤ 2 s, no pool timeout and no 503 on query/write/ready, and a 5th concurrent ask gets `busy`. On a FAIL, reduce the ask's DB parallelism (`PARALLEL_QUERIES = 3`, research_service.py:175, against pool_max_size 8) or reserve pool capacity for the other tools, then re-run.
2. **Local prod-like smoke (v2, R-13).** (corrected v2: v1's command could not run. install_llm_env.sh requires `--state` and reaches its target over SSH, and the prod state would turn this "no prod" step into a prod install.)
   - Target: a disposable local Linux VM (sshd + docker) on the owner's Mac, reached as `hlm-deploy` through `deploy/.local/local-r4/ssh_config`. The same VM serves the full-rollback rehearsal (step 9).
   - Client timeout (R-10): before step 5, set Claude Code's MCP tool timeout to ≥ 180 s (`MCP_TOOL_TIMEOUT=180000` in the owner's settings; ask first), so the client cannot cut a call inside the 170 s deadline. v1 only covered 60–120 s.

   | Step | Action | Pass predicate |
   |---|---|---|
   | 0. Target is local | Inspect the state dir before anything else | `ssh -G -F deploy/.local/local-r4/ssh_config hlm-deploy \| grep '^hostname '` is a loopback or private local address; no file under `deploy/.local/local-r4/` contains `153.92.1.166` or `mcp.hlmemo.com`; the state dir is not `deploy/.local/153.92.1.166` |
   | 1. Stack | On the VM: deploy R3 805f4cd; restore a prod-shaped export (the replica copy; the §3.1 export once it exists) with `deploy/backup/restore.sh DUMP --yes`; deploy the `r4-rc` merge commit with `deploy.sh <local target> <r4-rc full SHA> <local repository URL>` (the optional REPOSITORY_URL argument, so nothing is pushed before §2.3); apply the curated links with the §4.6 commands | /ready 200; the running commit = `git rev-parse r4-rc`; alembic head 0009; live `by=backfill` links in hlmemo = the approved proposal count |
   | 2. Env | `install_llm_env.sh --state deploy/.local/local-r4 --key-file <temp 0600 file> --reset-operator-values` from the r4-rc checkout (R4 template) | exit 0; llm.env mode 0600; only key names printed; the sha256 prefix of the installed GEMINI_API_KEY equals the key file's |
   | 3. Evaluate | `check_librarian evaluate --release r4` | PASS, including probe-writer PASS (one real Google call, ≈ $0.0001), trace dir empty, price date valid, fingerprint reported by api and librarian |
   | 4. Gates | `remote_gates.sh --url https://<local host:port> --state deploy/.local/local-r4 --insecure --librarian` (Caddy internal CA; drill ON, the data is disposable), plus `postgres-closed` | every gate PASS (SKIP only where the release has no subcommand) |
   | 5. Ask | 5 memory.ask calls over MCP HTTP against the local URL, from Claude Code | 5/5 answered; `writer_used` = google-gemini38-flash-medium on 5/5; no finish=length; the ledger cost is within ±10% of the cost from Google's usage (thinking included); p50/p95 recorded; no client-side cut |
   | 6. Status | `hlm_ops.sh --state deploy/.local/local-r4 status` | `writer_used_24h` ≥ 5 for google-gemini38-flash-medium; `writer_fallback_24h` = 0; `writer_outcomes_24h` present |
   | 7. Links revert rehearsal (R-7) | The exact §6.1 command on the VM, first with `--dry-run --json` (the RUNBOOK's revert preview and its PASS one-liner); then the same command without `--project`; then re-apply (step 1) | the revert exits 0, writes one `link_supersede` event, leaves 0 live `by=backfill` links in hlmemo; without `--project` it exits 64; the re-apply restores the count |
   | 8. Behaviour-only rollback rehearsal | §6.2(a): the R3 template on the R4 code via `install_llm_env.sh --release-template` | `check_librarian evaluate --release r3` PASS; memory.ask off; caps ≤ 1 / 2 / 10; keys preserved (sha256 prefix); env_switch journal empty. Then reinstall R4 (step 2) and re-run step 3: PASS |
   | 9. Full rollback rehearsal | `deploy.sh --rollback <local target>` | the VM runs 805f4cd with the R3 image and the R3 llm.env; the pre-R4 dump is restored (alembic head 0008, 0 backfill links); /ready 200; `check_librarian evaluate --release r3` PASS; the rollback journal is closed |
3. **Pre-push checks (v2, R-15).** (corrected v2: v1 scanned main + r4-rc only as a secret scan; a public push publishes every blob in the pushed history, including deleted or now-ignored files.) Before pushing `main` and `r4-rc` to the PUBLIC origin, all of these pass:
   1. `gitleaks detect --source . --redact --log-opts="--full-history main r4-rc"` over the FULL history of both refs, not just the diff: exit 0. There is no `.gitleaks.toml` at the repo root, so the default rules apply with no allowlist.
   2. `git ls-files docs/private deploy/.local` prints nothing, and `git log --format=%H main r4-rc -- docs/private deploy/.local` prints nothing (no private path in the history either).
   3. `git check-ignore -q docs/private/x deploy/.local/x .env.x` exits 0: docs/private (.gitignore:43), deploy/.local (deploy/.gitignore `.local/`) and .env* (.gitignore:3) are covered.
   4. No tracked file > 5 MB, history included: `git rev-list --objects main r4-rc | git cat-file --batch-check='%(objecttype) %(objectsize) %(rest)' | awk '$1=="blob" && $2 > 5242880'` prints nothing.
   5. A grep of tracked content for key prefixes returns 0 matches: `git grep -nP '\b(AIza[0-9A-Za-z_-]{30,}|sk-or-[0-9A-Za-z-]{20,}|sk-[0-9A-Za-z_-]{20,}|ghp_[0-9A-Za-z]{30,})' main r4-rc` (exit 1 = no match). The length suffixes keep words like "risk-" out.
   6. A manual read of the new public docs (DECISIONS D-192…D-198, consults 80+, the RUNBOOK) for private memory content (item bodies, personal data). Anything found is removed before the push.
   7. Push only after 1–6 pass: `git push origin main r4-rc`.
   - Checks 1, 2, 4 and 5 are automated by `deploy/scripts/prepush_check.sh --base origin/main main r4-rc` (r4-rc; exit 0 = PASS). `--base` limits the two HISTORY checks to commits origin does not have yet, because content already public cannot be unpublished by blocking the push. gitleaks stays full-history. (v2.1: a dry run on 2026-09-30 flagged only already-public items: `deploy/.env.prod.example`, a template, and a 14.7 MB synthetic fixture, `tests/fixtures/g3/items.jsonl`, both on origin since 2026-09-22. With `--base origin/main` all four checks PASS.) Check 6 (the manual read) stays manual.

## 3. Prod data preparation (read-only first)
1. **Export** the current prod items of project hlmemo ONLY (filtered by project membership in the query; v2, R-1) (memory_versions current + titles/bodies/valid_from) via SSH → `stack.sh exec -T db psql … COPY … TO STDOUT` → a local file under docs/private (0600). Read-only.
2. **Curation (Claude curator, chain-aware, memory-only)**, as for the replica (D-195). **3 independent readers precision-read ALL proposals.**
   - Gate: precision ≥ .95 and 0 high-harm false.
   - Drop the ambiguous ones and any span that also holds a still-current statement.
   - The approved proposals carry PROD ids of project hlmemo only; apply re-checks this under lock (§1.6).
3. **The final-test question set** (hold-out, never seen by the implementer):
   - an independent question writer builds **60 questions = 50 answerable + 10 negatives** from the PROD export (the pr-dev category mix plus unanswerables), with gold and must_mention (v2.1: was ~40, see §5.1 "Sample size");
   - sealed (sha256 recorded);
   - the orchestrator does not read the gold before asking.

## 4. Deploy (prod, VM 2002259 only)
All `stack.sh` commands below run on the host as `cd /opt/hlmemo/app && HLM_ENV_FILE=/etc/hlmemo/prod.env bash deploy/scripts/stack.sh …` (the RUNBOOK form), reached with `ssh -F deploy/.local/153.92.1.166/ssh_config hlm-deploy '…'`.
1. **Snapshot** VM 2002259 (Hostinger MCP `VPS_createSnapshotV1`; it overwrites the expired R3 snapshot). Wait for completion and record the id. Take NO second snapshot during or after the test: the one slot would overwrite this rollback point (B9; consult 89 Q9).
2. `deploy.sh hlm-deploy <r4-rc full SHA>` **with the R3 env still installed**. Interim check: /ready 200, the R3 behaviour intact (research OFF under the R3 env).
3. **Env switch:**
   - Build a temp key file (0600, in a private dir, deleted after): `GEMINI_API_KEY` from the local .env plus the OpenRouter prod key reference (whatever install_llm_env already uses).
   - Run `install_llm_env.sh --state deploy/.local/153.92.1.166 --key-file <temp> --reset-operator-values` from the r4-rc checkout. (changed v2: v1 ran it WITHOUT `--reset-operator-values` "unless §9 changes them"; §9 now changes the caps, and without the flag the installed R3 caps 1 / 2 / 10 would win over the template's 3 / 8 / 60, install_llm_env.sh:116-136. The flag also takes every key from the key file, so the temp file must hold every key the R4 env needs.)
   - Then `check_librarian evaluate --release r4` (includes probe-writer, the trace-dir check and the price date).
   - The key is never printed; verify only a sha256 prefix.
4. **Gates:** `remote_gates.sh --url https://mcp.hlmemo.com --state … --librarian` (drill ON); `hlm_ops.sh status` (the writer fields present, R-6); `postgres-closed`.
5. **Smoke via MCP** from the orchestrator's Claude Code session: 3 memory.ask calls. Check writer_used == google-gemini38-flash-medium, the latency, `writer_fallback_24h` = 0 in `hlm_ops.sh status`, and the ledger cost vs the Google console.
6. **Apply the curated links** (prod data change; v2, R-7):
   - **(v2.1) The librarian is stopped for the whole of step 6** (D-199 residual (a)): `… stack.sh stop librarian` before, `… stack.sh start librarian` after.
   - **(v2.1) First the explicit links (D-184/D-185).** The prod export of 2026-09-30 has 0 links; the measured replica had 252 `supersedes` links with `by=explicit` (all PHASE0-SPEC chunks → the merged draft consults) plus the curated ones. Without this pass prod would run a different configuration than the one measured.
     - Preview: `deploy/scripts/stack.sh exec -T api hlm links explicit --project hlmemo --dry-run --json`. Pass: exit 0, and the proposal pairs, mapped to source keys, equal the replica's 252 pairs. The mapping file is made from the export in §3.2; any difference is explained item by item before applying.
     - Apply: the same command without `--dry-run`. Record the event and check the live `by=explicit` count equals the preview.
     - Revert (prepared, not run): `… hlm links explicit --project hlmemo --revert`. It is project-wide for explicit links, and it runs AFTER the backfill revert when both are rolled back.
   - Stream the approved proposals file into the api container's /tmp (a 64 MB tmpfs): `… stack.sh exec -T api sh -c 'umask 077 && cat > /tmp/r4-links.jsonl' < <local proposals file>`. (corrected v2: `--proposals` must name an existing file inside the container (B cli/links.py:336-338) and does not read stdin, and `docker compose cp` cannot write into a tmpfs mount.)
   - Preview: `deploy/scripts/stack.sh exec -T api hlm links backfill --project hlmemo --apply --proposals /tmp/r4-links.jsonl --dry-run --json` (the CLI's flag is `--dry-run`; consult 90 N-2). Pass: exit 0 (a stale or foreign record anywhere in the file exits 65), `len(links)` of the JSON = the approved count (a dry run always reports `applied` 0), and the event and link counts unchanged (the RUNBOOK's `counts` and PASS one-liner).
   - Apply: `deploy/scripts/stack.sh exec -T api hlm links backfill --project hlmemo --apply --proposals /tmp/r4-links.jsonl`. Record the event id, then delete /tmp/r4-links.jsonl.
   - Render check (LLM-free) on prod: each older span shows its status.
   - The revert command is prepared, not run: `deploy/scripts/stack.sh exec -T api hlm links backfill --project hlmemo --revert` (§6.1).

## 5. Final test (the orchestrator, via MCP, in this chat)
- Ask every sealed question through `memory.ask` exactly as a consumer would, drilling or pulling raw on the returned handles when needed.
- At most 3 memory.ask calls in flight at any time (v2, R-10).
- During the test, sample memory.query and memory.write latency (≥ 20 samples each, spread over the test; the write samples go to a scratch project, never hlmemo). A p95 > 2 s on either stops the test (§6).
- Record the answers, latency and cost per question.
- Afterwards: blind reader adjudication (3 readers + a calibration overlap) against the sealed gold, and the orchestrator's consumer verdict (C1 utility rubric consumer_v1).
- Report: correct, contradiction, abstain, p50/p95, $/question (by the ledger, binding, and by Google tokens), writer_used share, and fallbacks (the per-question share from `meta.flags.writer_fallback`, §5.1; the outcomes from `writer_outcomes_24h`), each as defined in §5.1's measurement definitions.

### 5.1 Pre-registered meaning of the final test (v2, R-16)
Readers score the sealed hold-out (60 questions, written by an independent writer, never seen by implementers) blind. The orchestrator runs the questions personally via MCP memory.ask against prod. The table is fixed before the first question and is not changed after the results are seen. REVERT is checked first.

| Outcome | Condition | Action |
|---|---|---|
| KEEP | adjudicated correct ≥ .70 AND contradiction ≤ .04 AND abstain ≥ .90 AND writer fallback share ≤ 10% AND measured $/q ≤ $0.03 AND p95 ≤ 60 s AND no HIGH incident (key exposure, cross-project item, cap breach) | Gemini medium stays the prod writer |
| REVERT | correct < .63 (the luna baseline) OR contradiction > .08 (v2.1; was > .06) OR any HIGH incident OR fallback share > 25% | behaviour-only rollback to luna (R3 env or R4 env with the luna writer, §6.2) |
| OWNER CALL | anything in between | the owner decides; the numbers are reported as they are |

#### Measurement definitions (fixed before the first question; round 2, R-16 / V9, consult 90)
Planned split (v2.1): 60 sealed questions = **50 answerable + 10 negatives** (unanswerable from the prod memory). If the sealed set has another split, the formulas below give its integers; they are recorded next to the seal's sha256 before the first question.

- **Asking.** Each sealed question is asked exactly once through memory.ask, and that first response is the one scored (drilldown/raw calls on its handles are allowed; they are not asks). An error response (`E_UNAVAILABLE`, …) scores as not correct on an answerable question and as an abstention on a negative (it states nothing); errors are reported separately (error/timeout > 10% in the first 20 questions is a §6 trigger).
- **correct and contradiction.** Denominator n_ans = the answerable hold-out questions. 3 blind readers grade each answer against the sealed gold (and its must_mention) as correct / incorrect / contradiction; an abstention on an answerable question is incorrect. Per question the majority grade (≥ 2 of 3) counts; with no majority, the stricter grade (contradiction > incorrect > correct). correct = #correct / n_ans, contradiction = #contradiction / n_ans.
  - KEEP needs correct ≥ ⌈.70 · n_ans⌉ and contradiction ≤ ⌊.04 · n_ans⌋. REVERT is correct < .63 · n_ans, i.e. ≤ ⌈.63 · n_ans⌉ − 1, or contradiction > .08 · n_ans, i.e. ≥ ⌊.08 · n_ans⌋ + 1.
  - **n_ans = 50:** .70 × 50 = 35 → KEEP needs **≥ 35 correct**; .63 × 50 = 31.5 → REVERT at **≤ 31 correct** (32–34 = OWNER CALL); .04 × 50 = 2 → KEEP needs **≤ 2 contradictions**; .08 × 50 = 4 → REVERT at **≥ 5 contradictions** (3–4 = OWNER CALL).
  - Other splits, same arithmetic: n_ans = 48 → KEEP ≥ 34, ≤ 1; REVERT ≤ 30 or ≥ 4. n_ans = 52 → ≥ 37, ≤ 2; ≤ 32 or ≥ 5.
- **Sample size and the REVERT contradiction line (v2.1, 2026-09-30, orchestrator; fixed before any question exists).** v2 planned 32 answerable questions with REVERT at contradiction > .06. That line fires on 2 of 32. For a writer whose true contradiction rate is .03 (Gemini medium measured .02–.04, D-197/D-198), P(≥ 2 of 32) ≈ .25, so a quarter of runs would revert on noise alone. It would also revert to luna, whose own measured rate is .06. v2.1 raises the set to 50 answerable + 10 negatives and puts REVERT at > .08 (≥ 5 of 50, clearly worse than luna). At a true rate of .03, P(≥ 5 of 50) ≈ .02 and P(KEEP: ≤ 2 of 50) ≈ .81. KEEP stays as strict as before (≤ .04); everything between the two lines is an OWNER CALL. For correct, at a true rate of .74 (sd .062 at n = 50): P(KEEP ≥ 35) ≈ .79 and P(REVERT ≤ 31) ≈ .04 (exact binomial; the other figures above are exact binomial too: .249, .017, .811). Extra cost: ≈ 20 more asks ≈ $0.45.
- **abstain.** Denominator n_neg = the negative (unanswerable) hold-out questions. A response to a negative is an abstention when it is `abstained: true` (an empty answer with status `insufficient_evidence` is an abstention, D-198) or an error. For an answered response the 3 blind readers decide whether it asserts an answer the memory does not hold (fabricated) or only states that the memory does not hold one (abstention); majority, and with no majority, fabricated. abstain = 1 − fabricated / n_neg. KEEP requires abstain ≥ .90, i.e. **fabricated ≤ ⌊.10 · n_neg⌋**: for **n_neg = 10**, .10 × 10 = 1 → **≤ 1 fabricated**. n_neg = 8 → 0 (7/8 = .875 < .90); n_neg = 12 → ≤ 1. abstain is not a REVERT condition: a fabricated negative moves KEEP to OWNER CALL.
- **Writer fallback share.** Per question, over the hold-out asks only: the hold-out responses whose `meta.flags.writer_fallback` is true (an error response raised after the research loop started carries it in `details.writer_fallback`; one raised before any writer call, e.g. `busy`, counts as false; r4-rc, Astra 90 N-1) ÷ the hold-out asks. The flag is true when any prose writer call of that question (first write, retries, the prose after a refine) used a profile other than the configured writer, whether that fallback answered or failed. For 60 asks: KEEP ≤ 10% → **≤ 6 of 60**; REVERT > 25% → **≥ 16 of 60**. The 24 h numbers of `hlm_ops.sh status` (`writer_fallback_share_24h` per call, `writer_fallback_question_share_24h` per ledger lineage) are operational only and are not used for this decision.
- **$/question.** Binding: the product ledger's normalized cost (R-8: `llm_calls.cost_usd`, Google thinking included) of the hold-out window, `SELECT sum(cost_usd) FROM llm_calls WHERE task IN ('research', 'research.prose', 'rerank') AND created_at BETWEEN <the first hold-out ask's start> AND <the last hold-out answer's end>`, ÷ the number of hold-out questions. No other memory.ask runs in that window (the §4.5 smoke runs before it; the query/write samples make no research call). The rows carry a per-ask `lineage`, but the response does not return it, so the window is the binding scope; each response's `meta.cost_usd` (the same ledger rows, per lineage) is recorded per question, and a difference of more than $0.001 between their sum and the window sum is reported. The Google console total for that day is recorded as a cross-check only. KEEP: ≤ $0.03.
- **p95.** The nearest-rank p95 of the server-side duration of each hold-out memory.ask: the response's **`meta.latency_ms`** (`research_service._ask`: `int((time.perf_counter() - t0) * 1000)` from the start of the tool handler to the assembled answer). An error response has no meta; its duration is the `ms=` of its api log line `mcp tools/call memory.ask device=… outcome=E_… ms=N` (`server/mcp_server.py`). p95 = the ⌈.95 · n⌉-th smallest value: for n = 60 the **57th smallest**, so KEEP (≤ 60 s) allows at most 3 asks above 60 000 ms. p50 is reported the same way (the ⌈.50 · n⌉-th smallest). Client-side times are recorded but not used.

R4 CANNOT self-certify D-130 Production Ready: Gemini fails the C6 gates (p95 ≤ 20 s, ≤ $0.01/q) by design (D-197/D-198). The final test measures quality on prod data; it is not a release certification. The permission to run the test and a Production Ready certification are separate decisions.

## 6. Rollback triggers and procedure
**Triggers**, any of:
- gates FAIL, including `evaluate --release r4` at any time (probe-writer, trace dir, an expired `price_valid_until`);
- /ready not 200 for > 2 min;
- error/timeout > 10% in the smoke or the first 20 questions;
- ledger vs Google cost off by > 25%;
- a spend-cap trip in the first hour;
- any cross-project leak;
- memory.query or memory.write p95 > 2 s during the smoke or the final test (v2, R-10);
- writer fallback share > 25% (per question: the hold-out `meta.flags.writer_fallback` during the final test, `writer_fallback_question_share_24h` otherwise; v2, R-6, consult 90 N-1);
- a REVERT outcome of §5.1 (v2, R-16).

**Procedure** (the `stack.sh` form of §4 applies):
1. Links only: `deploy/scripts/stack.sh exec -T api hlm links backfill --project hlmemo --revert` (run with `--dry-run --json` first: exit 0, `len(links)` = the live backfill link count, counts unchanged). It is project-wide: it closes ALL live `by=backfill` links of hlmemo in ONE `link_supersede` event, not only the links of the §4.6 apply event. (corrected v2, R-7: v1's `hlm links backfill --revert` exits 64 without `--project`, and the links stay live)
2. Behaviour only (corrected v2, R-3: v1's plain R3 re-install kept the R4 caps; MONTH 60 > the R3 manifest's 10, so evaluation failed and the open env_switch journal blocked both deploy and rollback):
   - (a) R3 env: `install_llm_env.sh --state deploy/.local/153.92.1.166 --key-file <temp> --release-template <R3 template>` from the r4-rc checkout (§1.10). It installs the R3 template with R3-compatible caps (1 / 2 / 10), keeps the keys, and turns research OFF. Pass: `check_librarian evaluate --release r3` PASS and an empty env_switch journal.
   - (b) R4 env with the luna writer: `HLM_RESEARCH_WRITER_PROFILE` unset, so memory.ask stays on with luna. Only if the r4 manifest accepts an unset writer (§1.5); otherwise use (a).
   - The 0009 cache table stays; it does not affect either option.
3. Full: `deploy.sh --rollback hlm-deploy` restores R3 code, image, env and the pre-R4 dump. Writes after the deploy are lost, including the curated links AND their events; a later redeploy does not bring them back, a re-verified apply is needed (consult 89 Q7). Accepted, since the memory is read-mostly during the test window. It relies on the R-2 hardening (§1.10): the safety dump is protected from rotation, and a recovery that finds it missing stops without closing the journal.
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
  - Astra-high review of this plan (round 1 = consult 89, GO-WITH-FIXES; round 2 per §11);
  - D-130 gate items knowingly waived for this test: p95 ≤ 20 s and ≤ $0.01/q. The waiver covers the test only; R4 cannot self-certify D-130 Production Ready (§5.1);
  - (v2, D-198) the prod writer and the final test use `google-gemini38-flash-medium`; the high profile stays available and the manifest accepts either;
  - (v2, D-198) prod caps HOUR 3 / DAY 8 / MONTH 60 USD, in a separate `_BUDGETS_R4`; R3's `_BUDGETS` stays unchanged. Medium costs $.0214/q (2026) → $.0405 (2027); the monthly budget including the VPS at 20 asks/day is $24 → $35 (D-198);
  - (v2, D-198) the OpenRouter prod key keeps its provider-side limit of $50/month, the outer guard;
  - (v2) `HLM_RESEARCH_MAX_USD` stays 0.12 and `HLM_RESEARCH_PROSE_MAX_TOKENS` stays 16000.
- **NEEDED before the deploy:**
  - the owner's OK to set `MCP_TOOL_TIMEOUT=180000` in the owner's Claude Code settings (§2.2);
  - after review round 2, the owner's explicit acceptance or rejection of the residual risk (§11).

## 10. Astra round 1 dispositions (v2)
Source: docs/consults/89-astra-r4-plan-review.md (GO-WITH-FIXES; R-1…R-7 HIGH were release blockers).

| R-id | Severity | Disposition | Where | Test or check that proves it |
|---|---|---|---|---|
| R-1 | HIGH | fixed-in-branch + plan-change | `r4-code` §1.6 (both heads checked in the project under lock; a mismatch or stale head rejects the whole apply); plan §3.1, §3.2 (project-filtered export and ids) | `r4-code` test: projects A/B, a proposal labelled A with two live heads of B → non-zero exit, 0 events, 0 links; a stale head → the whole apply rejected; §2.1 backfill gate |
| R-2 | HIGH | fixed-in-branch | `r4-deploy` §1.10 (safety dump out of rotation, a shared lock, fail-closed recovery); plan §6.3 | `r4-deploy` fault-injection test: a destructive journal, the safety dump rotated away, a retry → recovery refuses, no `end-rollback`, the journal stays open; §2.2 step 9 |
| R-3 | HIGH | fixed-in-branch + plan-change | `r4-deploy` §1.10 (`--release-template` with R3 caps), §1.5 (`_BUDGETS_R4`); plan §6.2 | `r4-deploy` test: an R4 env with MONTH 60 → `--release-template` R3 → `evaluate --release r3` PASS, empty env_switch journal; §2.2 step 8 |
| R-4 | HIGH | fixed-in-branch | `r4-deploy` §1.4 (FINGERPRINT_KEYS + both services report) | `r4-deploy` test: two envs that differ only in the writer / HTTP timeout / budget-disabled → mismatches listed (consult 89 got `[]`) |
| R-5 | HIGH | fixed-in-branch | `r4-code` §1.3 (`price_valid_until = 2026-12-31`, fail closed); `r4-deploy` §1.4 (evaluate FAIL when expired, WARN before) | `r4-code` test with the clock at 2027-01-01 → no call, no reservation; the review's 10k/16k fixture ($0.06825 vs $0.13650); `r4-deploy` evaluate FAIL test; §2.2 step 3 |
| R-6 | HIGH | fixed-in-branch + plan-change | `r4-code` §1.9 (writer counters in ops status); plan §4.5, §5, §5.1, §6 (fallback share > 25% trigger) | `r4-code` test: Gemini 5xx + luna OK → the response flag and `hlm_ops status` both show the fallback; §2.2 step 6 |
| R-7 | HIGH | plan-change | plan §4.6, §6.1 (exact commands; the revert is project-wide) | flag names checked against B cli/links.py:274-337; §2.2 step 7: one `link_supersede` event, 0 live backfill links, exit 64 without `--project` |
| R-8 | MEDIUM | fixed-in-branch (corrected v2) | `r4-code` §1.1 (max rule, worst case on missing/contradictory usage, `usage.cost` stays the USD authority) | `r4-code` fixtures incl. 1000/100/0/3100 → 2100 output tokens; §2.2 step 5 (ledger within ±10% of Google) |
| R-9 | MEDIUM | fixed-in-branch + plan-change (corrected v2) | `r4-code` §1.8; plan B2 corrected, §1.5 MAX_USD row | `r4-code` test at $0.12 / 10k input: a 16k-token invalid-JSON Gemini attempt → luna, no second Gemini attempt, no budget_stop, booked ≤ $0.12, the fallback counted |
| R-10 | MEDIUM | fixed-in-branch + plan-change | `r4-code` load-gate integration test; plan §2.1 (precondition), §2.2 (client timeout ≥ 180 s), §5 (≤ 3 concurrent asks), §6 (p95 > 2 s stop) | §2.1 load gate PASS (query/write p95 ≤ 2 s, no pool timeout/503); the prod query/write samples during §5 |
| R-11 | MEDIUM | fixed-in-branch | `r4-deploy` §1.4 (evaluate FAIL on a non-empty trace dir); §1.5 row | `r4-deploy` test: `HLM_RESEARCH_TRACE_DIR=/tmp/traces` in the api env → `evaluate --release r4` FAIL; §2.2 step 3; §4.3 |
| R-12 | MEDIUM | fixed-in-branch (corrected v2) | `r4-code` §1.6 (port with dependencies, no proposer import) | on the local R4 image: apply → replay → revert with counts equal to the expected set (§2.1) |
| R-13 | MEDIUM | plan-change (corrected v2) | plan §2.2 (a disposable local VM target, 10 steps with predicates) | §2.2 step 0 (the target is local) passes before any other step |
| R-14 | MEDIUM | fixed-in-branch | `r4-code` `python -m hlmemo.ops probe-writer`; `r4-deploy` wiring into evaluate (§1.4) | tests: a wrong key in the api only → probe FAIL; the sentinel key is absent from stdout/stderr; finish=length at 16 tokens = PASS; §2.2 step 3 |
| R-15 | MEDIUM | plan-change (corrected v2) | plan §2.3 | checks 1–6 of §2.3 pass before `git push` |
| R-16 | MEDIUM | plan-change | plan §5.1, §6, §9 | the §5.1 table is committed before the test; the final report applies it unchanged |
| R-17 | LOW | fixed-in-branch (corrected v2) | `r4-code` §1.2 (expand keeps 1500) | a golden expand request body with max_tokens 1500 at the defaults |

Checklist items answered NO or PARTIAL in consult 89:
- **Q1 (PARTIAL):** §1.1 v2 rule (R-8): the max of the token readings, worst case on missing or contradictory usage; the caps settle in USD through the reservation.
- **Q2 (PARTIAL):** §1.5 MAX_USD row and §1.8 (R-9): Gemini + luna + aux = $0.0917 fits $0.12; a Gemini retry is budget-checked and otherwise falls to luna.
- **Q3 (PARTIAL):** R-11 (the trace-dir FAIL, §1.4) and R-14 (the probe on the api credential, safe JSON output, a sentinel test). The temp key file stays private + 0600 + deleted (§4.3); the container env and `docker inspect` stay inside the host trust boundary and are never shared.
- **Q4 (PARTIAL):** §1.4: every install needs `--key-file`; the probe runs in the api container (R-14): one attempt, no retry or fallback, a protocol-level parse, ≈ $0.0001.
- **Q5 (PARTIAL):** R-10: the §2.1 load gate, the §2.2 client timeout ≥ 180 s, ≤ 3 concurrent asks in §5, the p95 > 2 s stop in §6.
- **Q6 (NO):** R-6 counters (§1.9) and R-9 schema-fail fallback to luna (§1.8); the B2 claim is corrected.
- **Q7 (PARTIAL):** R-1/R-12 (§1.6: project check under lock, whole-apply reject, full port) and R-7 (§6.1: the exact, project-wide revert). A full rollback drops the links and their events; a re-verified apply is needed (§6.3).
- **Q8 (NO):** R-4 fingerprint keys (§1.4); the R-2 hardening (§1.10) restores D-123's R4 precondition; the review-77 residuals list goes in the RUNBOOK R4 section.
- **Q9 (PARTIAL):** R-3 `--release-template` with R3 caps (§6.2) and the exact R-7 revert (§6.1); one snapshot before the deploy only, none after (§4.1); both rollbacks rehearsed locally (§2.2 steps 8–9).
- **Q10 (PARTIAL):** R-11 makes tracer OFF an evaluate gate (§1.4). Round 1 found no cross-project path; a cross-project item in the final test is a HIGH incident (§5.1) and a rollback trigger (§6).
- **Q11 (PARTIAL):** R-15: §2.3 runs gitleaks over the full history of both refs (default rules, no repo allowlist), checks private paths in the history, inventories blobs > 5 MB, greps key prefixes, and reads the new public docs by hand.
- **Q12 (NO):** each missing item now has a home: the backfill port (§1.6), the local installer route (§2.2), the load gate (§2.1), the API writer probe (§1.4), tracer OFF (§1.4), the public-push checks (§2.3), the quality-based decision (§5.1), the D-123 hardening (§1.10).

## 11. Review round 2 scope (v2)
- Reviewer: codex gpt-6-astra, reasoning high, as in round 1. The threat model (§7) and the severity rubric (§8) stay fixed.
- Scope, and ONLY this:
  1. the §10 dispositions: does each fix close its finding, and does the named test or check prove it;
  2. the branch diffs `git diff 8d36f74..r4-code` and `git diff 8d36f74..r4-deploy`, and the `r4-rc` merge result.
- Out of scope: the owner's recorded decisions (§9), the writer choice, and new features.
- A HIGH finding needs a test that reproduces it.
- Max 2 rounds in total (D-125): round 2 is the last one. After round 2 the owner accepts or rejects the residual risk explicitly, recorded in DECISIONS. No §4 prod step starts before that decision.
- The exchange is recorded in docs/consults/ as the next numbered consult.
