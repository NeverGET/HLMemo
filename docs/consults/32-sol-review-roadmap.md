## Verdict

**No.** The roadmap needs contract and resource changes before it can serve as an implementation spec.

## Defects

| # | Severity | Roadmap section | Problem | Fix |
|---|---|---|---|---|
| 1 | Critical | [W0a](/Users/cemalkurt/Projects/HLMemo/docs/decisions/PHASE2-4-ROADMAP.md:86) | Registration defaults to `open`. Changing an example file does not change the live VPS’s existing environment; the release train uses `deploy.sh`. D-052 fails if an upgrade leaves registration open. | Fail closed in production, migrate the existing environment explicitly, and verify every public route after deploy. |
| 2 | High | W0a | The private listener binds *inside* the API container, so the proposed host `ssh -L` tunnel cannot reach it. Closing public `/devices/revoke` also removes self-revocation promised by [Phase 0 §2](/Users/cemalkurt/Projects/HLMemo/docs/decisions/PHASE0-SPEC.md:336). | Provide and test a host-loopback path, or use ops only; retain a self-only revoke route or log a contract change. |
| 3 | High | [CC-3, W2c, W4a](/Users/cemalkurt/Projects/HLMemo/docs/decisions/PHASE2-4-ROADMAP.md:74) | Authority limited to the triggering device’s grants cannot write reserved `hlm-librarian` or `hlm-global`; a project writer accepting a question must not thereby gain authority over another project. | Define narrow internal capabilities; recheck current grants and every affected project when applying a job or answer. |
| 4 | Critical | [W3a, §6](/Users/cemalkurt/Projects/HLMemo/docs/decisions/PHASE2-4-ROADMAP.md:217) | Average-linkage clustering of 20,000 items needs about **1.6 GiB for pairwise distances alone**. The 512 MiB limit budgets only the embedding array. | Use bounded clustering; gate 20,000-item peak memory, runtime, and concurrent API latency on a 2 vCPU / ~7 GiB host. |
| 5 | High | [W3d](/Users/cemalkurt/Projects/HLMemo/docs/decisions/PHASE2-4-ROADMAP.md:241) | `derived_from → document item` has no target memory version: `documents` is a separate table. Quarantined chunks are immediately retrievable into preflight, leaving an injection path despite prompt delimiters. | Define a versioned document item; exclude unapproved content from default preflight and bound parser expansion, time, and memory. |
| 6 | High | [W1.5](/Users/cemalkurt/Projects/HLMemo/docs/decisions/PHASE2-4-ROADMAP.md:203) | File `mtime` is not when a fact became true; using it as `valid_from` can misdate copied or edited files and exceed the future-time limit. | Keep server `recorded_at`; store original `mtime` as provenance, set valid time only from explicit evidence, and log the D-020 deviation. |
| 7 | High | [W2a](/Users/cemalkurt/Projects/HLMemo/docs/decisions/PHASE2-4-ROADMAP.md:139) | Checking completed ledger rows before concurrent calls cannot enforce the promised *hard* spend caps. | Atomically reserve worst-case call cost with bounded output tokens, then settle the reservation. |
| 8 | High | [CC-2/CC-5](/Users/cemalkurt/Projects/HLMemo/docs/decisions/PHASE2-4-ROADMAP.md:72) | “Verbatim” LLM output in `payload.request` conflicts with W2a’s redacted-content rule and Phase 0’s meaning of `request` as client arguments. Cassette replay cannot prove current model quality. | Specify a redacted, versioned LLM audit payload and make live quality gates release-blocking. |
| 9 | High | [Final acceptance](/Users/cemalkurt/Projects/HLMemo/docs/decisions/PHASE2-4-ROADMAP.md:352) | Counts of lessons and references, or a hit bearing a source path/D-id, do not prove correct reconstruction under D-022. Step 11 also requires `hlm export`, absent from the workstreams. | Add the export deliverable and grade factual answers, evidence spans, and stale claims against frozen repo truth. |
| 10 | Medium | [Scope/Phase 4](/Users/cemalkurt/Projects/HLMemo/docs/decisions/PHASE2-4-ROADMAP.md:258) | W2e is optional despite the report’s low-confidence synthesis promise and D-051’s “fully work” goal. W4b adds chain backfill and standby export despite CC-1’s no-rewrite claim and D-051’s deferred off-host copies. | Decide and log the synthesis scope; narrow sync to a demonstrated gap and give any event-chain migration its own contract. |

## Gates

Keep cassettes in CI for parsing, application, idempotency, and replay. **They are insufficient for release.** Require a cost-bounded live gate before R2–R4 and after model, profile, or prompt changes, including the fallback. Pin versions and fixture hashes; retain scored outputs and error counts. Live quality is statistical, so report repetitions and the scoring rubric.

The [Phase 0 spec](/Users/cemalkurt/Projects/HLMemo/docs/decisions/PHASE0-SPEC.md:670) explicitly owes a real-data regression. Before librarian mutations, freeze held-out HLMemo questions with gold facts, evidence spans, temporal status, and negative cases. Measure retrieval and stale-claim errors before and after placement, topics, consolidation, and archive. D-020 additionally needs sampled recall against actual source-memory answers. Eighty contradiction pairs are too few to justify automatic invalidation; audit real conflicts and near misses in shadow first.

Replace W0’s `<250 ms` test with an ASGI receive that fails if read. Treat external `nmap` as an operational IPv4/IPv6/UDP smoke check. Replace “any gold member **or topic**” with drilldown to the correct cited fact; remove pre-accessed gold from archive tests; allow correct bi-temporal supersession while checking historical access. Extraction hashes and an imperative-text regex do not establish document reading order or injection resistance: use labeled document facts and live adversarial cases.

## Cut / defer

Defer W4b’s hash chain, standby export, and offline read cache pending a demonstrated need; the authoritative server already synchronizes online writers, and off-host copies are deferred by D-051. Stage W0b with the outer firewall first; Tailscale adds recovery work. Keep a minimal packed-share capability for Phase 4. Any omission of low-confidence synthesis needs an explicit scope decision.

## Order

1. W0 closure and reachable operator path.
2. Import plumbing; freeze real-data truth and retrieval baselines.
3. W2a and bench; live provider gate.
4. W2b/c/d in shadow; audit proposed mutations.
5. Bounded W3a, then ingestion.
6. Consolidation and archive shadow; real-data regression.
7. Minimal Phase 4 contracts and gates.
8. Deploy, reconstruct HLMemo, and run fresh-chat acceptance.

## Answers

**(a)** `source.mtime` is useful provenance, and server-time `recorded_at` preserves the Phase 0 invariant. `valid_from = mtime` is wrong by default: filesystem modification time does not establish factual validity. Record an ADR resolving D-020’s wording and use an evidenced valid date or import time.

**(b)** Publicly close `POST /devices/register`, all `/admin/*`, and `POST /devices/approve`, `POST /devices/revoke`, `POST`/`DELETE /devices/grant`, and `GET /devices/list`—subject to preserving self-revoke. `/health`, `/ready`, `/devices/whoami`, and `/mcp` remain reachable; the latter two require a trusted bearer. With registration **actually closed before body handling**, an anonymous client has no durable state-creation path. A live deployment left in the roadmap’s `open` default still lets one create pending devices.