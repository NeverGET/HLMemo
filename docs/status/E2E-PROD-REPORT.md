# HLMemo — First E2E Production Test Report (R2)

Date: 2026-09-24 (overnight run 01:07–08:00 +03). Author: orchestrator (Claude Opus 5.5), with Claude subagents as implementers, codex gpt-6-sol as adversarial reviewer, and neutral verifiers.
Status: **R2 is LIVE** at https://mcp.hlmemo.com (commit 6902f91, D-079). The librarian runs **ON in the OBSERVER role** (it proposes, never mutates). HLMemo's own memory was imported as a **TEST** into the separate project `hlmemo-e2e`, which is wiped before the final release.

---

## 1. Executive summary

<!-- E2E-SUMMARY -->

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

<!-- E2E-RESULTS -->

## 6. Gaps, bugs and risks (honest list)

<!-- E2E-GAPS -->

## 7. Recommended next steps

<!-- E2E-NEXT -->

## Appendix — decisions taken tonight

D-069 (fan-out + migration renumbering), D-070 (W2f, strict adjudication), D-071 (risk fallback = retrieval-only), D-072 (import temporal rule, G-I4 open), D-073 (W1.5 merged, Item.close), D-074 (observer answers are labels), D-075 (0008 edited in place, unreleased), D-076 (hold-out gates fail → observer only), D-077 (R2 GO despite Sol 50, with promotion stranding as a Phase-5 prerequisite), D-078 (W2e), D-079 (R2 live). Sol consults 40–52 are in docs/consults/.
