# HLMemo — First E2E Production Test Report (R2)

Date: 2026-09-24 (overnight run 01:07–08:00 +03). Author: orchestrator (Claude Opus 5.5), with Claude subagents as implementers, codex gpt-6-sol as adversarial reviewer, and neutral verifiers.
Status: **R2 is LIVE** at https://mcp.hlmemo.com (commit 6902f91, D-079). The librarian runs **ON in the OBSERVER role** (it proposes, never mutates). HLMemo's own memory was imported as a **TEST** into the separate project `hlmemo-e2e`, which is wiped before the final release.

---

## 1. Executive summary

**Verdict: the system works end to end in production, and its safety is proven. Retrieval on the owner's real Turkish/English data is not yet good enough, and the librarian has not yet earned autonomy.**

- **Works as promised (proven in prod):** closed registration + operator-minted devices; import of 363 items in 82 s with 0 errors; queries stay fast under an import burst (p95 ≈ 400 ms incl. network, 0 errors over 1,434 queries); the librarian reviewed all 368 jobs in observer mode with **0 mutations** (byte-identical export before/after); risk_check and synthesis run with an LLM judge behind privacy/spend guards; cost $0.51 for the whole e2e day.
- **Not yet good enough:**
  - corpus-B dev hit@5 **.500** in prod vs .625 on the local baseline; Turkish questions **.278**, because one long Turkish research report crowds the top-5;
  - synthesis made 5/44 cited-but-false claims (11%), so the wrapper default stays **off**;
  - risk_check caught **0/12** real footguns, because the importer produced no lessons (parsing bug);
  - the hold-out librarian gates fail (proposal precision strict .42), so the librarian stays **observer**.
- **One cross-project leak in proposals (not applied):** the librarian proposed closing an item in the real `hlmemo` project based on a document in the test project, because the same device holds grants on both.

These are exactly the kinds of findings the owner's Phase-5 plan (supervised migration, observer → assistant) exists to surface. None of them corrupted data, and all have concrete fixes (§7).

## 2. What was built tonight (roadmap order, nothing skipped)

| Workstream | Content | Review / verification | Decision |
|---|---|---|---|
| W1.5 Import/export | `hlm import` (markdown / automemory / serena / context), `hlm export`, the source contract + code_refs, the D-015 skeleton card, `ops status`; migration 0007 (online, unique index built concurrently, duplicate precheck + `ops sources` reconcile), `Item.close` | Sol 42 → fixes → Sol 43 → fixes | D-072, D-073 |
| W2a Librarian foundation | provider/fallback/breaker, redaction, atomic spend guard, ledger, cassettes, privacy default-deny, the role ladder | (merged earlier; D-062..D-064) | D-062 |
| W2b Placement / contradiction / cross-project | enqueue on every write (priority: lessons 2, writes 3, imports 6), candidates within the caller's current scope, version_signals, proposals + batches, D-067 guards (candidate-only ids, both-side quotes, a verifier call for high-impact proposals, abstention default), the D-057 read-side supersession | Sol (3 rounds on the branch) + Sol 48/49/50 | D-074, D-075, D-077 |
| W2c Questions + `memory.answer` | the question lifecycle; in OBSERVER an answer is a **label** (`accepted_pending`), applied only after a promotion with a full recheck | Sol 48 (critical) → fixed | D-074 |
| W2d `memory.risk_check` + `memory.register_lesson` | deterministic stage + LLM judge (4 s cap, outside any DB transaction, authority recheck), preflight integration | Sol 41 → fixes | D-071 |
| W2e Low-confidence synthesis | `memory.query synthesize:true` (query/2): cited sentences only, post-call recheck, whole-token support guard, 7 s total deadline; wrapper default **off** | Sol 51 → Sol 52 → fixes | D-078 |
| W2f `hlm bench` | bench v1/v2 on the production provider/redaction/shared budget; strict gold adjudication; append-only leaderboard | Sol 40 → fixes | D-070 |
| R2 deploy | llm.env into api + librarian (`install_llm_env.sh`, key over ssh stdin), the post-cutover librarian check (enabled + live + observer, fresh heartbeat), risk/librarian remote gates, probes accept new tools | Sol 48/49 | D-079 |

Every branch had its own worktree and DB, a Sol review, and at least one fix round. Main gates on the release ref: lint clean; unit 535; deploy 132; integration 443 passed / 0 failed.

## 3. Quality gates

| Gate | Result | Note |
|---|---|---|
| G3 synthetic Recall@5 | 0.980 | unchanged since D-055 |
| G4 latency p95 (local) | ~263–274 ms | ≤ 500 |
| **G-L3** query p95 during 100 writes with the librarian ON | local 340–389 ms; **2 vCPU VM 268 ms (neutral) / 375 ms (identifier)** | ≤ 500 |
| G-LIVE-B (placement / contradiction, 3 reps) | luna .984 / .994, false-supersede .000; deepseek .984 / 1.000 | PASS |
| G-LIVE-C (risk_check) | luna catch 1.0, false-warn ≤ .075: PASS; deepseek false-warn .25: **FAIL** → fallback = retrieval-only | D-071 |
| G-LIVE-D (synthesis, weak evidence) | luna +16 pts (0/8 negatives answered after the guard), deepseek +19 | PASS (self-authored fixture) |
| **G-E-TEMP** (hold-out, corpus A, stale-first ≤ 4/15) | 8/15 → 8/15 | **FAIL** (D-076) |
| **G-E-W2b** (hold-out, +3 pts, no category −3) | A hit@5 .793 → .783; B evidence R@5 .451 → .438 | **FAIL** (D-076) |
| Proposal precision (50 blind-labelled) | strict .42 (CI .29–.56), lenient .94 | not promotable |
| G-I4 import recall | 0.794 strict / 0.853 with drilldown | open (D-072) |
| G-SURF (tools/list tokens) | 2807 / 3000 (8 tools) | little room left for a 9th tool |
| Bench v2 (adjusted, strict adjudication) | luna-pro 95.6, luna 94.9, deepseek 89.8, ceiling gpt-6-sol 96.6 | D-070 |

**Interpretation.** The infrastructure, safety and latency gates pass, on the real 2 vCPU hardware too. The librarian's *judgement quality* on real data does not yet pass the hold-out gates. So, per the W-E rule and D-058, it ships as an **observer**: its proposals become the audited training signal of the Phase-5 migration, and nothing is applied. This is the owner's plan working as intended, but it is also the main gap (§6).

## 4. Release path

1. VM rehearsal of R2 (ref 3535bcc) on the 2 vCPU / 8 GiB Ubuntu 26.04 VM: ALL PASS. Cutover 81 s (downtime 12 s); G-L3 as above; a 50-file import burst (207 items, query p95 217 ms over 6,973 queries, 0 errors; the 207 priority-6 jobs drained in 35 min without starving priority-3 jobs; +$0.27); observer safety: 829 proposals, 0 applied, 0 links/closes; kill switch ~3 s; reboot recovery. Evidence: docs/bakeoff/rehearsal-r2/.
2. The final ref (6902f91) added only app-level fixes (W2e + Sol 49/51/52 + the ledger deadline). The deploy path and compose (sha256 99fecbf4…) were unchanged, so there was no second VM pass; the full local suite gated it.
3. Production: `install_llm_env.sh` (0600, observer, luna) → `deploy.sh --accept-compose-change` EXIT 0 (0006 → 0007 → 0008; in-deploy and public route checks PASS; librarian check PASS, heartbeat 6.7 s) → `remote_gates --librarian` 10/10 PASS (risk-check judged=true; a librarian job in 8 s, observer, 0 mutations; WAN p50 134 / p95 161 ms).

## 5. E2E production test (HLMemo self-import, TEST)

Raw public-safe results: `docs/status/e2e/2026-09-24/` (README + e2e-results.json). Per-question data are private (docs/private/realdata-hlmemo/results-e2e/).

| Step | Result |
|---|---|
| Baseline | ready 200, migration 0008, librarian heartbeat 8 s, observer; api 1.2 GiB, worker 856 MiB, librarian 120 MiB |
| Import (`hlm import` markdown + context + automemory → `hlmemo-e2e`) | **363 items**, 249k tokens, **82 s**, 0 failed/rejected/skipped; valid_from: 78 from decision-row dates, 285 = import time; embeddings done after 4.9 min |
| Query load during import (3 callers, 2 q/s) | 1,434 queries, **0 errors**; during import p50/p95/max 268/405/491 ms; import + 10 min 244/401/1,904 ms (includes ~95 ms WAN) |
| Librarian (observer) | 368 jobs, 0 failed, **76.6 min** to drain (3.4 jobs/min under load, 4.9 idle, one job at a time), ~$0.0013/job; **310 proposals** (251 link, 56 contradiction, 3 widen_scope; 169 action / 141 question tier; 345 link inserts + 37 closes proposed; 69 unverified quotes; 12 "supersedes against time"); 13 batches, **0 decided, 0 applied** |
| 0-mutation proof | export before any librarian job vs after the drain: **byte-identical** for all 364 original items; 0 links, closes or scope changes |
| Agent smoke (5 natural questions) | 4/5 answered in the top-3 previews; "add a device in production" needs one drilldown. For "default librarian model" the old deepseek decision ranks above the current one (no temporal preference without applied supersession) |
| End state | ready, 0 jobs waiting, worker peak 985 MiB / 1.5 GiB; `hlmemo-e2e` kept (381 items) |

**Retrieval on real data (corpus-B dev, 80 questions, budget 3000; prod now vs local baseline):**

| Metric | Prod e2e | Baseline |
|---|---|---|
| hit@1 / @3 / @5 | .236 / .375 / **.500** | .208 / .486 / .625 |
| MRR | .349 | .398 |
| evidence R@5 (drill top-3 / top-5) | .306 / .375 | .424 / .451 |
| L2 (top-5) | .542 | .556 |
| stale claims in the top-3 | **.062** | .25 |
| temporal L2 / stale-first | **.438 / .25** | .25 / .562 |
| hit@5 English / **Turkish** | .722 / **.278** | .778 / .472 |

The drop is not caused by the newer tree (68/72 answers are still in their gold file). Turkish questions lose because 74 of 200 top-5 slots go to the 22 sections of the Turkish research report, which is gold for none of them. Temporal improved (the newer tree carries explicit supersession language and dated decision rows). Negatives: 8/8 still report `evidence: matched`.

**Synthesis (hold-out evidence for the wrapper default, D-078):** on the 38 weak-evidence questions, exact-key accuracy fell from .42/.50 without synthesis to .29/.34 with it. Graded by meaning, 36/44 answers are correct and 3 partial, but **5 (11%) contain a cited false claim** (3 give the current value when asked for the earlier one). 8/38 questions flip between answered and abstained across runs. It adds ~1.7 s at p50 (p95 up to 4.4 s). On the positive side: 0/12 false answers on negatives and 0 uncited sentences. **Verdict: keep the wrapper default OFF.**

**risk_check realism (20 scenarios: 12 real HLMemo footguns + 8 neutral):**

| Variant | Catch | False warn | Timeout |
|---|---|---|---|
| A, as imported | 0/12 | 0/8 | 0/20 |
| B, + 3 auto-memory feedback files as lessons | 1/12 | 0/8 | 1/20 |
| C, + 14 single-bullet lessons | 4/12 | 0/8 | 1/20 |

A returns `no_candidates` because the import created **no lessons** (bug 1). Judged calls: p50 ≈ 1.9 s.

**Spend:** $0.509 for the day (librarian ≈ $0.465, synthesis ≈ $0.026, risk judge ≈ $0.018).

## 6. Gaps, bugs and risks (honest list)

| # | Severity | Finding | Where |
|---|---|---|---|
| 1 | High | The auto-memory importer reads `type` at the top level, but Claude Code nests it under `metadata:`, so feedback/lesson files become facts. **No lessons were created**, and risk_check has nothing to match | `src/hlmemo/importers/automemory.py:23` |
| 2 | High | Cross-project librarian proposal: it proposed closing an item in the real `hlmemo` project based on a test-project document (action tier, verifier agreed; NOT applied: observer). Cause: the same device holds grants on both projects | librarian candidates/capabilities; project policy |
| 3 | High | Turkish retrieval .278 (hit@5): a single long Turkish report floods the top-5 | ranking (per-source diversity), query language |
| 4 | High | Hold-out librarian gates fail (D-076): stale-first unchanged, proposal precision strict .42; whole-item closes end still-valid facts | W2b prompts/granularity |
| 5 | Medium | Synthesis: 11% cited-but-false claims; earlier-vs-current value confusion passes the support guard | W2e validator |
| 6 | Medium | The risk judge sees only the first 1,200 chars of a lesson; the matching part can sit further in | `src/hlmemo/librarian/risk_judge.py:77/176` |
| 7 | Medium | Librarian throughput is one job at a time: 76 min for 363 items. A year of memory needs hours per project | worker concurrency |
| 8 | Medium | Promotion stranding (Sol 50) is still open: a Phase-5 prerequisite before any assistant role | `src/hlmemo/librarian/roles.py:137` (D-077) |
| 9 | Low | `eval/realdata` MCP client opens a TLS connection per call (~950 ms vs 115 ms); run_eval's title mapping only splits on ` § ` | eval tooling |
| 10 | Low | `hlm import/export` warns "Tool hlm.export not listed by server"; ops status reports breaker `idle` while the heartbeat says `closed` | CLI/ops cosmetics |
| 11 | Low | The risk judge times out in ~5–24% of calls at the 3.5 s HTTP cap and falls back to retrieval-only | tune the cap on real usage |
| 12 | Info | D-061 (W0a) had been referenced but never logged; logged now | DECISIONS |

Not a finding but worth stating: **no data was corrupted, no secret leaked, no availability was lost** at any point of the test.

## 7. Recommended next steps

1. **Quick fixes (half a day, before any more imports):** the auto-memory `metadata.type` parsing (#1); split lessons into single facts at import; give the risk judge the matching lesson section, not the first 1,200 chars (#6); exclude disposable/test projects from cross-project candidates via a project policy flag (#2); run the librarian 3–4 jobs concurrently within the spend guard (#7).
2. **Retrieval quality for TR/EN (the biggest user-facing gap):** a per-source cap in the top-5 (diversity) plus Turkish↔English query rewriting (bench v2 T7 already exists), measured on corpus A/B with the W-E gate.
3. **Librarian judgement (before any promotion):** fact-level supersession instead of whole-item close; contradiction for doc_chunks; a stricter action tier; the promotion-stranding fix (#8) + Sol re-review; then re-run G-E-TEMP/G-E-W2b.
4. **Fallback-model research (owner's next item):** bench v2 the non-OpenAI pro-tier candidates (deepseek-v4-pro-0813 first, glm-5.3, kimi-k2.6, qwen3.8-27b) for the luna-pro tier, plus a risk_judge fallback that passes G-LIVE-C.
5. **Phase 5 with the owner:** supervised migration of 3–4 owner projects, the librarian in observer, and owner labels on proposals (`memory.answer` = label), feeding the promotion criteria.
6. Housekeeping: `deploy.sh --accept-release` (deletes the retired pre-W0 secret backup on the server) once the owner is happy with R2; delete `hlmemo-e2e` before the final migration; rotate the Hostinger API token.

## Appendix — decisions taken tonight

D-069 (fan-out + migration renumbering), D-070 (W2f, strict adjudication), D-071 (risk fallback = retrieval-only), D-072 (import temporal rule, G-I4 open), D-073 (W1.5 merged, Item.close), D-074 (observer answers are labels), D-075 (0008 edited in place, unreleased), D-076 (hold-out gates fail → observer only), D-077 (R2 GO despite Sol 50, with promotion stranding as a Phase-5 prerequisite), D-078 (W2e), D-079 (R2 live). Sol consults 40–52 are in docs/consults/.
