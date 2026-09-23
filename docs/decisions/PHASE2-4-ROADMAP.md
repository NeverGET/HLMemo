# HLMemo — Phase 2–4 Roadmap (planning spec, D-051)

Status: REVISION 2, 2026-09-23. Rev 1 was reviewed by gpt-6-sol (`docs/consults/32-sol-review-roadmap.md`, verdict "No", 10 defects) and by the orchestrator (`docs/consults/31-opus-position-roadmap.md`, 5 points). Every point is resolved here; the index is §8. Next steps: owner answers the §7 questions, then each workstream gets its own frozen contract section before coding starts (D-025 pattern).
Binding inputs: `docs/research/00-deep-research-report.md` (the report), D-001..D-053, `PHASE0-SPEC.md` (the spec).
Conventions: an ideal-day (id) is one focused implementer-day including tests. It excludes review rounds, which add about 30–50% (D-036 neutral verifier + adversarial review). File anchors refer to `main` at 49fe389.

---

## Phase numbering reconciliation

| Report (Aşamalı Yol Haritası) | Spec / D-series | Where it goes now |
|---|---|---|
| Faz 0: PG+pgvector, MCP, query/write/call_the_day, 3 CLIs | Phase 0 | DONE (D-044), live in production (D-047/D-048). |
| Faz 1: Graphiti L1 bi-temporal, RAPTOR L2, clue ids + drilldown | "Phase 1" (never named in the spec) | L1 bi-temporal and clues/drilldown moved into Phase 0 on our own schema, not Graphiti (D-006/D-007). **RAPTOR L2 is not built.** It needs an LLM, so it moves to **Phase 3 (W3a)**, next to consolidation, which regenerates summaries (report D.5). The real-data regression the spec owes (§9.10) becomes **W-E**. |
| — | Phase 1.5: importers (D-020) | **W1.5** (import + export). Runs right after W0 because dogfooding needs it (Opus 3). |
| Faz 2: librarian, placement, contradiction, cross-project, risk_check | Phase 2 (D-016/17/19) | **Phase 2 (W2a–W2f)**. The librarian runs on OpenRouter `deepseek/deepseek-v4.1-flash` (D-019), not the report's self-hosted model. |
| — | Phase 2.5: reconstruction (D-022) | Only the **HLMemo self-project** is in D-051 scope (§6). The legacy campaign stays out of scope (Q5). |
| Faz 3: consolidation (score, silent drop, experience promotion), PDF/DOCX ingestion | Phase 3 (D-012) | **Phase 3 (W3a–W3d)**. The report's silent DROP becomes a reversible ARCHIVE (D-012). |
| Faz 4: L4 experience, event-log multi-computer sync, packed share | Phase 4 | **Phase 4 (W4a–W4c)**, reduced to a minimal, demonstrated scope (§4). Deferred items and reasons are in §4. |

---

## §0 Scope ledger: everything promised and not yet built

| # | Capability | Source | WS | Exists partially? (file:line) |
|---|---|---|---|---|
| 1 | Server-side device minting; public registration off, fail-closed | D-052 | W0a | `server/devices.py:91-105` (secret header + 5/min limiter); `db/auth_queries.py:74,131,249` reusable |
| 2 | Pasted-token login in `hlm` | D-052 | W0a | `cli/credentials.py:133 store_token`; `hlm --token` is only a per-call override (`cli/hlm.py:101`) |
| 3 | Extra VPS security layer | D-053 | W0b | ufw + fail2ban only (`deploy/bootstrap.sh`) |
| 4 | Real-data evaluation (spec §9.10 owes one) + agent-level A/B | spec §9.10, report Değerlendirme Planı, Opus 1/2 | **W-E** | `eval/realdata/import_corpus.py` (mechanical corpus importer, in progress) |
| 5 | L2 topic summaries (RAPTOR-style) with clue ids | report D.1 L2, B; Faz 1 | W3a | none; `kind` CHECK `alembic/versions/0001_phase0.py:133-134` |
| 6 | Skeleton project card at project creation | D-015 (dropped, F10 note in D-033) | W2a | `server/admin.py:22` creates project + events, no card |
| 7 | Librarian provider: OpenAI-compatible, schema-validated, retry once, 429/5xx backoff, model+prompt version per job | D-017, D-019 | W2a | parsed but unused: `config.py:150-156`; `profiles/*.toml` (5) |
| 8 | Secret/PII filter before every LLM call; redacted logs; `data_collection=deny` | D-016 | W2a | `profiles/openrouter.toml` |
| 9 | Async librarian jobs on the outbox | report D.4, D-010 | W2a | `jobs` `0001_phase0.py:250-266`, kind CHECK :252; `worker/main.py:69 JOB_KINDS` |
| 10 | Write placement (importance, stability, topic, cross-links) | report D.4(a), D.5 | W2b | columns `0001_phase0.py:139-141`, defaults at `write_service.py:178-180` |
| 11 | Contradiction detection + resolution (invalidate, never delete; ask on real conflict) | report D.4, risk list | W2b | rels `0001_phase0.py:221`; correction path spec §1.1 |
| 12 | Question channel to the project LLM + answer + librarian memory | report D.4 "bloke etmeme" | W2c | none |
| 13 | Cross-project similarity check | report D.4(a) | W2b | `project_ids[]` |
| 14 | `memory.risk_check` (proactive flashback) | report D.3 #7, D.4(b), Tavsiye 4 | W2d | query preflight only (`cli/preflight.py`) |
| 15 | `memory.register_lesson` | report D.3 #6 | W2d | lessons via write/call_the_day (`write_service.py:374-402`) |
| 16 | Low-confidence slow-path synthesis | report D.4(c) | **W2e (in scope)** | none |
| 17 | Librarian working memory, compaction, fresh-wake | report D.4 | W2a/W2c | none |
| 18 | `hlm bench` user tool | D-017, spec §5 | W2f | `bench/run.py`, `bench/tasks/t1..t4` |
| 19 | Hard cost caps, latency/privacy controls | report risk "maliyet kaçağı" | W2a | none |
| 20 | Importers + provenance + idempotent re-import + dry-run diff + `describes` | D-020, D-022 (1.5 part) | W1.5 | `eval/realdata/import_corpus.py` (eval-only mapping, see W1.5) |
| 21 | `hlm export` (cold-start fallback) | D-021 bootstrap rule | **W1.5** | none |
| 22 | NotebookLM + codex SQLite importers | D-020 | deferred (Q5) | none |
| 23 | Reconstruction campaign | D-022 | §6 (HLMemo only) | none |
| 24 | Consolidation "sleep" | report D.5 | W3b | none |
| 25 | Score formula in ranking | report D.5, D-024(10), spec §9.12 | W3b | signals only: `last_access_at` `0001_phase0.py:149`, `db/read_queries.py:580-597` |
| 26 | Decay + reversible archive, shadow first, restore | D-012 | W3c | status `0001_phase0.py:135`, events :107-108, job kind :252, `core/read_service.py:110` |
| 27 | Experience promotion + L4 maturation | report D.5, D.1 L4 | W3b + W4a | `experience` kind :134 |
| 28 | PDF/DOCX ingestion + `memory.ingest_document` | report D.3 #8, D.6, risk "prompt injection" | W3d | `doc_chunk` kind :134 |
| 29 | Event-log multi-computer sync | report D.7 | W4b (narrowed) | authoritative `events` + `db/replay.py` |
| 30 | Packed memory share | report D.8 | W4c (minimal) | none |
| 31 | `memory.raw` payload paging | D-026, BACKLOG | W2a | chunks/links paged (D-037); `payload_item` not |
| 32 | Worker lease renewal, attempt cap, fair queue | BACKLOG consults/30 | W2a | `worker/main.py:105` |
| 33 | Embedder provider interface (gemini opt-in) | D-040/D-042 | OUT of D-051 | `core/embedder.py:71` |
| 34 | `memory.compact` tool | report D.3 #5 | not a tool (deviation, to be logged): `call_the_day` + `hlm.ops consolidate` | — |
| 35 | OAuth 2.1 | report C | OUT (bearer + D-052 minting) | — |
| 36 | HNSW index | spec §1 | only above 200k rows | `alembic/versions/0002_hnsw.py` |

---

## Cross-cutting contracts (frozen in W-C before fan-out)

**CC-1 Migration chain.** Existing: `0001_phase0` (label `phase0`) and `0002_hnsw` (label `hnsw`, D-026). New revisions extend phase0 linearly: `0003_w0_access` → `0004_librarian` → `0005_import` → `0006_consolidation` → `0007_ingest` → `0008_experience_share`. `0003` adds `branch_labels=("main",)`. Migrate commands (`compose.yaml:56`, `deploy/compose.prod.yaml:70`) move to `alembic upgrade main@head`. Agents never pick `down_revision`; the orchestrator links revisions at merge. Each agent tests on its own DB `hlm_test_<ws>`. Migrations only add tables/columns, extend CHECK lists, and insert reserved rows. **No migration rewrites existing event rows.** Any future change to event rows (e.g. a hash chain, §4) needs its own contract and decision.

**CC-2 Event kinds (0004+).** `events.kind` CHECK gains: `librarian`, `question`, `answer`, `import`, `ingest`, `consolidation`, `pack_import`, `device_minted`. Replay handlers ship in the same PR. G6 `test_rebuild_projections_from_events_identical` covers every new kind.

**CC-3 System actor and internal capabilities (Sol #3).** `0004` adds `devices.is_system boolean DEFAULT false` and inserts device `librarian` (class `server`, `trusted`, `token_sha256='reserved:librarian'`: no usable bearer; `is_admin` false). The librarian never acts with "all grants". Each job carries a **capability set**, computed at enqueue and **rechecked at apply** inside the apply transaction. The recheck re-resolves the triggering device with `FOR SHARE` (same ordering rule as spec §2), reloads its current grants, and checks every project a mutation touches:

| Capability | Allows | Scope rule |
|---|---|---|
| `annotate(P)` | `version_signals` rows; links `relates_to/contradicts/supersedes/member_of` | every endpoint's `project_ids ⊆ P`, P = projects where the triggering device **currently** holds `write`; endpoints pass authz (a) for that device |
| `correct(P)` | bi-temporal correction (valid_to close) of an item | item home ∈ P and all of the item's `project_ids ⊆ P`; triggering device holds write on each; only under the W2b auto-resolve rule |
| `question(P)` | open a `librarian_questions` row | all subject clues readable by the triggering device |
| `librarian_memory` | write `kind=fact, tag librarian-rule` into reserved project `hlm-librarian` only | content limited to rule text + clue references; never copies item bodies |
| `global_experience` | write `kind=experience` into reserved project `hlm-global` only | only via an **accepted `promote` answer** (never autonomous, Q2); answering device must hold `write` on `hlm-global` and `read` on every source lesson's projects |

If the recheck fails (device revoked or downgraded, a grant removed), the job applies nothing. It records a `librarian` event with `resolved.outcome="authority_lost"` so replay stays identical. **Answers** (`memory.answer`) are authorized against the **answering** device. It must hold `write` on every project in the union of the question's subject `project_ids` and the proposed action's targets, else `E_FORBIDDEN_PROJECT`. A question whose subjects the device cannot fully read is `E_NOT_FOUND`. Accepting a question never grants authority over another project.

**CC-4 Tool-surface budget.** 5 tools today (D-013). After Phase 4 there are 9: `query`, `drilldown`, `raw`, `write`, `call_the_day`, `risk_check`, `register_lesson`, `answer`, `ingest_document`. Gate G-SURF: `tools/list` ≤ 3,000 o200k tokens (measure today's value first). `memory.query` output changes are additive optional fields under a `contract_version` bump (`query/2`).

**CC-5 LLM audit payload, determinism, live gates (Sol #8).**
- *Audit payload.* Phase 0 defines `payload.request` as the client's verbatim arguments. For **system-actor events** (`librarian`, `consolidation`, `question`), `payload.request` is defined as the actor's arguments: the versioned audit record `{"audit":"llm/1","task","job_id","capabilities","profile","model_id","prompt_version","schema_version","redaction_version","input_digest":sha256(redacted prompt),"output":<schema-validated JSON AFTER redaction>}`. Free-text fields (`reason`, `summary`, `why`) pass through the same redactor as the prompt. The raw provider response is **never** stored. `payload.resolved` holds only the applied mutations. Replay reads `resolved` only and never calls an LLM. `events.schema_version = 2` marks these rows. PHASE0-SPEC §1.1 gets an addendum (a contract change, logged as a decision).
- *Cassettes (CI).* Provider mode `HLM_LLM_MODE ∈ {live, record, replay, off}`. Key = `sha256(model_id ‖ prompt_version ‖ schema_version ‖ canonical(redacted messages) ‖ canonical(params))`. Files: `tests/cassettes/<ws>/*.jsonl`. `replay` is strict (a miss fails; no network). Cassettes prove parsing, application, idempotency and replay. **They do not prove model quality.**
- *Live gate (release-blocking).* `make gate-live PROFILE=… REPS=3 MAX_USD=2` runs every LLM task fixture against the real provider, and runs **again for the fallback profile**. It is required before R2, R3, R4 and after any change of model, profile or prompt version. Fixture files are pinned by sha256 and the rubric lives in `eval/live/RUBRIC.md`. Scored outputs, error counts, cost and latency are kept in `eval/live/<date>-<profile>/` (redacted, committed). Pass rule: the minimum across 3 reps ≥ threshold, and the mean is reported. A cap breach aborts the run as a FAIL, never a skip.

---

## W-E: Real-data evaluation (new; Opus 1+2; runs early)

Purpose: every quality gate so far (G3) is tuned on our synthetic fixture. W-E makes real data the acceptance basis for the librarian and Phase 3 features. Owned paths: `eval/**`, `docs/private/**` (private and hold-out; public files carry only sha256).
- **Corpus A (a private owner project).** Imported by `eval/realdata/import_corpus.py` (mechanical, no LLM). The 100-question private set lives in `docs/private/realdata-yt/`. **Hold-out rule:** no implementer agent reads the questions. Only the grader script and the orchestrator's eval runner open them. Public artifacts get only scores and the set's sha256.
- **Corpus B (HLMemo held-out).** Built by a separate agent **before** dogfood import (D1). It is frozen at a pinned commit and stored in `docs/private/realdata-hlmemo/` (sha256 published). Rows: `{question, category, gold_facts:[atomic statements], evidence_spans:[path:Lstart-Lend@commit], temporal_status:current|superseded(valid dates), negative:bool}`. Categories: architecture, decision rationale, gotcha/lesson, temporal ("what did X use before Y"), identifier/config, negative (no answer exists). There are ≥ 80 questions, of which ≥ 15 temporal and ≥ 10 negative. The final D-051 acceptance uses a **disjoint** 50-question subset kept sealed until §6.
- **Metrics.** Per category: evidence Recall@5 (a returned chunk overlaps a gold span by ≥ 50% of the span's characters), drilldown-to-fact (the gold fact is reachable within one drilldown from the top-5 clues), stale-claim error (a superseded value returned as current), negative false-answer rate, tokens/query (≤ 7k, report metric). Answer-level accuracy uses an LLM judge with a fixed rubric (3 reps, live, cost-capped). Deterministic identifier matching is used where possible.
- **Baseline.** Phase 0 fast path (no librarian) on both corpora, recorded with commit, fixture hashes and model/embedder ids in `eval/baselines/phase0.json`.
- **Feature gate (applies to W2b, W2e, W3a, W3b, W3c).** A feature is enabled by default only if on corpus A ∪ B: overall primary metric (evidence Recall@5 for retrieval-shaping features; answer accuracy for W2e) improves by **≥ X = +3 points** (W3c: non-inferior, ≥ −1 point), and **no category regresses by more than Y = 3 points**, and the stale-claim error does not rise. McNemar p is reported (not a gate at this n). A feature that misses X still ships, but disabled by config, with a logged decision (Opus 4: each phase must justify itself on the evaluation). X and Y are Q6.
- **Agent-level A/B.** 10 fixed HLMemo tasks under `eval/ab/tasks/`, each with a deterministic check: a test passes, a specific file/line changes, or a command output matches. Examples: "add a config key following the conventions", "which gate proves revocation ordering", "prepare the deploy command for a new release" (must avoid `bash -s` piping). Each task runs headless (`hlm claude --headless`) **with memory** and **without memory** (`--no-preflight` and the MCP server removed from the child config), 3 reps each, and records success, turns and tokens. Gate at R2 and at the final acceptance: with-memory success ≥ without-memory success + 2 tasks (of 10), and median tokens no more than 20% higher.
- Size: **4 id** (harness integration, corpus-B builder, grader, A/B runner).

---

## §1 W0: Access hardening (D-052, D-053)

### W0a Server-side device minting; public registration closed (3.5 id)

**Settings (fail-closed, Sol #1)**
- `registration_mode ∈ {open, secret, closed}` defaults to **`closed`**. Only local `compose.yaml` and the test fixtures set `open` explicitly. `admin_http ∈ {enabled, disabled}` also defaults to **`disabled`**; local compose and tests set `enabled`.
- Production does not rely on env files: `deploy/compose.prod.yaml` sets `HLM_REGISTRATION_MODE: closed` and `HLM_ADMIN_HTTP: disabled` in the tracked `environment:` block, so the settings ship with the release. `HLM_DEPLOYMENT: production` is also set there. With `production`, **startup refuses** (lifespan error, `/ready` 503 "unsafe config") if registration is not `closed` or admin HTTP is not `disabled`.
- **Explicit live-env migration.** `deploy/scripts/remote-deploy.sh` gets a step `migrate_env_w0` that runs before cutover. It backs up `api.env`, removes `HLM_REGISTRATION_SECRET` and `HLM_ADMIN_TOKEN` from the host env files (device 1 becomes disabled, spec §2 (ii)), and records the step in the release log. The step is idempotent. The step runs before cutover because the old code still requires the secret file.
- **Post-deploy route verification.** `deploy/scripts/check_edge.py --routes` runs from `remote-deploy.sh` after cutover (against the loopback Caddy site and the public URL) and from `remote_gates.sh` (RG-routes). A failure fails the deploy, and the existing rollback logic applies. Expected public results:

| Route | Anonymous | Trusted device bearer |
|---|---|---|
| `GET /health`, `GET /ready` | 200 | 200 |
| `GET /devices/whoami` | 401 `E_AUTH` | 200 |
| `/mcp` | 401 | per grants |
| `POST /devices/revoke` | 401 | **self-only**: body `id` = caller → revoked; any other id → 404 |
| `POST /devices/register`, `POST /devices/approve`, `POST`/`DELETE /devices/grant`, `GET /devices/list`, all `/admin/*` | **404**, before any body byte is read | **404** |

  This is Sol's answer (b). The 404 for closed routes comes from the middleware route filter (applies to any listener). Caddy's `@rest` matcher shrinks to `/health /ready /devices/whoami /devices/revoke` as defense in depth.
- **Self-revoke kept (Sol #2).** `POST /devices/revoke` stays publicly reachable in self-only mode, preserving spec §2 step 3 for a device revoking itself (lost-laptop case run from another machine is an operator action, below). Contract change, logged as a decision: with `admin_http=disabled`, revoking *another* device is available only via `hlm.ops`.

**Operator path: ops only (Sol #2).** There is no private HTTP listener, so no tunnel has to reach one. All admin actions go through `python -m hlmemo.ops` inside the api container, reached over SSH:
- `src/hlmemo/ops/__main__.py` uses the app DSN and the query layer (`auth_queries.py`) in one transaction. Events carry `device_id=1`, `client='hlm-ops/<ver>'`, and the command does not depend on the admin token being bound. Commands: `device mint --name N --class C [--grant slug:role …] [--expires 2h] [--notes …]` prints **only the token on stdout** (metadata on stderr; events `device_minted` + `grant_added`). The others: `device list|revoke|rotate|grant|ungrant`, `project create|list`, `status [--json]` (jobs, heartbeats, librarian ledger), plus later W2/W3 subcommands.
- `0003_w0_access` adds `devices.expires_at timestamptz NULL`. Expired is treated exactly like revoked, both in the pre-body gate (`middleware.py:252-291`) and in the in-transaction resolve.
- The local wrapper `deploy/scripts/hlm_ops.sh <args…>` runs `ssh -F deploy/.local/<host>/ssh_config hlm-deploy "cd /srv/hlmemo && docker compose … exec -T api python -m hlmemo.ops <printf %q args>" </dev/null` (the D-035 stdin lesson).
- Token delivery: `hlm_ops.sh device mint … | hlm device login --name N --token-stdin --server https://mcp.hlmemo.com`. `hlm device login` reads stdin (or `getpass` for a pasted token), requires `/health` to report `trusted`, then calls `credentials.store_token` (keychain, else a 0600 file). The token never appears in argv, history or logs. `hlm device register` handles a 404 with a hint to ask the operator. `hlm device revoke --self` uses the public self-revoke route.

**Script changes.** `deploy/scripts/probe.py:117-155` (smoke/remote gates) gets its probe device from `hlm_ops.sh device mint --class ci --grant gates-probe:write --expires 30m` and cleans up with `hlm_ops.sh device revoke`. `remote_gates.sh:200-203,288-290,312-314` replaces register/approve/admin-revoke with ops mint/login/revoke and adds **RG-routes** (the table above, with anonymous, trusted, and junk bearers). G7 mints `g7-<host>` once (`--class personal --grant gates-g7:write`) and rotates via `hlm_ops.sh device rotate` + `hlm device login` + `hlm mcp add …`. `first_deploy.sh:134-163` stops generating the registration secret and the admin token. RUNBOOK and USAGE get "Adding a device (operator + owner over SSH)".

**Files.** `src/hlmemo/ops/**`, `server/middleware.py`, `server/devices.py`, `server/app.py` (unsafe-config check), `config.py`, `cli/hlm.py` (`device login`, `revoke --self`), `alembic/versions/0003_w0_access.py`, `deploy/{compose.prod.yaml,Caddyfile,app.env.example,api.env.example,RUNBOOK.md}`, `deploy/scripts/{hlm_ops.sh,remote-deploy.sh,check_edge.py,probe.py,remote_gates.sh,first_deploy.sh}`, `tests/gates/test_g5_isolation.py` (route table).

**Gates.**
- G-W0-1 `test_closed_routes_never_read_body`: each closed route is called through an ASGI harness whose `receive()` **raises if awaited**. Expect 404, no exception, no `devices` row, no event.
- G-W0-2 `test_route_table_production_mode`: parametrized over every `build_routes()` entry (`app.py:283-302`) × {anonymous, trusted, junk, admin-token-shaped bearer} must equal the table above. With `admin_http=enabled` (dev), the Phase-0 G5 cases pass unchanged.
- G-W0-3 `test_production_refuses_unsafe_config`: `HLM_DEPLOYMENT=production` combined with `registration_mode=open` (or `secret`, or admin HTTP enabled) makes lifespan fail and `/ready` return 503.
- G-W0-4 `test_ops_mint_roundtrip`: mint → trusted → granted project OK; other project `E_FORBIDDEN_PROJECT`; revoke → 401. Events recorded; replay identical.
- G-W0-5 `test_expired_device_rejected`: rejected at the gate and in-transaction. A cursor issued before expiry fails after it with `E_INVALID_CURSOR`.
- G-W0-6 `test_self_revoke_only`: the caller can revoke only itself. Another device's id gets 404, with no difference between existing and non-existing ids.
- G-W0-7 `test_env_migration_idempotent`: run `migrate_env_w0` twice under the fake ssh/docker judge harness (D-037b). Env files are correct, a backup exists, and no secret appears in stdout or logs.
- G-W0-8 `test_token_never_in_argv_or_logs` (fake ssh/docker harness).
- G-W0-9 remote: `remote_gates.sh` 8/8 + RG-routes pass with **no admin token** in the local environment.

### W0b Extra VPS security layer (D-053) (1.5 id)

| Option | What | Pros | Cons |
|---|---|---|---|
| **A. Hostinger VPS firewall via API** | Per-VM firewall (`VPS_createNewFirewallV1` / rules / `VPS_activateFirewallV1`, no panel needed): allow 80/tcp, 443/tcp, 443/udp, 22/tcp | Filters traffic before it reaches the VM. It is the only guard against Docker-published ports, which **bypass ufw**. Reversible from the Mac via API. | 22 stays open to the world (dynamic home IP). IPv6 and stateful behavior are unverified. Lockout recovery depends on the browser console, which must be verified. |
| B. Tailscale / WireGuard for SSH | sshd reachable only on the tailnet; public 22 closed | SSH is invisible to scanners; the dynamic IP does not matter | A third party (or hand-run WG); a new daemon on Ubuntu 26.04 (sudo-rs/uutils); a lockout path adds recovery work |
| C. In-place hardening | `AllowUsers`, `MaxAuthTries 2`, CrowdSec | No new dependency | Marginal gain; does not cover Docker bypassing ufw |

**Recommendation: A only, now. B is re-evaluated after the final acceptance** (Sol: Tailscale adds recovery work). The W0a ops-only path already removes the public admin surface. Gate G-W0b: from an external host, an IPv4 + IPv6 + UDP smoke scan (`nmap -Pn`, operational smoke, not a formal proof) shows only 22/80/443 (+443/udp), and a `docker run -p 5555:80` canary is unreachable from outside. Rollback: `deactivateFirewall` via API; RUNBOOK entry.

---

## W1.5 Import + export (Phase 1.5; 5 id)

Client-side, because the files live on the owner's machines (D-022). It is needed for dogfooding (D1) and for the final test.
- `hlm import <source> --project SLUG [--dry-run] [--json]` with `source ∈ {markdown <paths>, automemory <dir>, serena <dir>, context <files>}`. Parsers are in `src/hlmemo/importers/*.py` and emit `ImportRecord(system, path, sha256, mtime, commit?, title, body, kind_guess, tags, describes[], evidenced_valid_from?)`. The rules from `docs/research/03-legacy-memory-inventory-public.md` §1–10 apply: derived titles, prefix/separator-normalized dedupe keys, content-hash dedupe of monorepo copies, stub resolution, empty-dir skip.
- **Temporal rule (Sol #6).** `recorded_at` stays server time (spec §1.1 invariant). `mtime` and git commit date are **provenance only** (`source.mtime`, `source.commit_date`). `valid_from` is taken only from **explicit evidence**: a frontmatter `valid_from`/`date`, a dated decision-log row (e.g. `D-047 | 2026-09-23`), or a dated heading. Otherwise it is the server's import time (the write's `occurred_at` default). Evidence dates in the future beyond the 5-minute tolerance are rejected per item, with the reason in the dry-run report. The rule is logged as a decision:
  > **D-0xx (proposed)** | ACCEPTED | D-020 deviation: imported memories keep server-assigned `recorded_at` (system time, spec §1.1). The original file mtime and commit date are stored only as provenance in `source`; they do not establish when a fact became true. `valid_from` comes from explicit evidence in the content (frontmatter date, dated decision row, dated heading), else import time. | Filesystem mtime reflects copies and edits, not factual validity, and using it as `recorded_at` breaks the monotone system-time invariant. Reviewed in consults/32. | Temporal queries on imported history are exact only where the source dates its facts; `source.mtime` stays queryable via `memory.raw`.
- `eval/realdata/import_corpus.py` currently sets `valid_from` from the git author date or mtime. That is acceptable **for eval corpora only**. Production imports use `hlm import`, and the eval harness adopts the same rule before its output is reused as production memory.
- **Contract (Item).** New optional fields: `source:{system, path ≤ 512, sha256, mtime?, commit?, commit_date?}` and `describes:[path ≤ 256](≤ 16)`. `0005_import` adds `memory_versions.source jsonb NULL`, a generated `source_key` column + index, and a projection table `code_refs(version_id, path, commit NULL)`.
- **Idempotency.** `request_id = uuid5(NS_IMPORT, project ‖ source_key ‖ sha256)`. An unchanged file is a replay. A changed file with a known `source_key` becomes a revision (`expected_version_id` = head). The `--dry-run` report lists new/changed/unchanged/skipped, duplicate groups, rejected evidence dates, and a token estimate.
- **`hlm export` (Sol #9, D-021).** `hlm export --project P --out DIR [--kinds …] [--as-of T]` writes Markdown with frontmatter (`clue`, `logical_id`, `version_id`, `kind`, `valid_from/to`, `source`, `tags`, `links`) to one file per current item, plus `CARD.md` and `INDEX.md`. The export can be re-imported (`hlm import markdown` recognizes its own frontmatter and maps it back by `logical_id`).
- **Gates.**
  - G-I1: fixture tree `tests/fixtures/import/` → the dry-run report is golden-equal. A second run makes 0 writes. An edited file produces exactly 1 revision. An mtime-only change produces 0 writes and no valid-time change.
  - G-I2: `memory.raw` shows `source` + `code_refs`.
  - G-I3: export → import into a fresh project → export again gives byte-identical files (ids excluded).
  - G-I4 (D-020 sampled recall): for ≥ 30 sampled source files, a question answerable from the file finds its evidence span in the top-5 (≥ 0.90).

---

## §2 Phase 2: Librarian LLM active

### Architecture (all W2)
- A separate `librarian` service (same image; `python -m hlmemo.librarian.worker`). It loads no ONNX model. Limits are in §6.
- Lease renewal every 30 s, `attempts ≤ 5` then `failed`, ordering by `(priority, run_after)` (fixes BACKLOG consults/30). The shared code moves to `src/hlmemo/worker/lease.py`.
- The core never waits for an LLM: query/write/drilldown/raw do not await one, and librarian health is **not** part of `/ready`.
- Prompts are versioned files `src/hlmemo/librarian/prompts/<task>/v<N>.md` + `.schema.json`, with the static prefix first (D-019 cache economics). Model quirks live only in `profiles/<name>.toml [prompt_overrides]` (D-017).

### W2a Librarian foundation (6 id)
- **Provider** `librarian/provider.py`: OpenAI-compatible client built from `config.py:150-156` profiles plus the fallback profile. `response_format=json_object`, or `json_schema` if the profile declares support. Schema validation, retry once on a schema failure, backoff 1/2/4/8 s ×5 on 429/5xx/timeouts (D-019), fallback profile once, circuit breaker (5 failures → open for 60 s, doubling up to 15 min). **Every request sets `max_tokens`** from the task's bound (placement 200, contradiction 600, summary 700, risk 500, synthesis 700).
- **Redaction** `librarian/redact.py`: the gitleaks rule set from `.githooks/pre-commit` plus `hlm_` tokens, private keys, JWTs, DSNs with passwords, IBAN, and optionally email/phone. Replacement format `⟦REDACTED:<type>:<sha8>⟧`. The mapping stays in memory only. It is applied to prompts **and** to audit-payload free text (CC-5).
- **Atomic hard caps (Sol #7).** Table `llm_budget(period_kind ∈ {day, month}, period_start date, cap_usd, reserved_usd, spent_usd, PRIMARY KEY(period_kind, period_start))` plus `llm_reservations(call_id PK, day_start, month_start, worst_usd, expires_at)`. Before each call: `worst = ceil(input_tokens × 1.10) × price_in + max_tokens × price_out`. Prices come from the profile (`price_in_per_m`, `price_out_per_m`). A profile without prices refuses live calls unless `HLM_LLM_BUDGET_DISABLED=true`. One short transaction locks the day row then the month row (fixed order) and does `UPDATE … SET reserved_usd = reserved_usd + :worst WHERE spent_usd + reserved_usd + :worst <= cap_usd` on both. If either affects 0 rows → no call, outcome `budget_deferred`. After the call, settle: `reserved -= worst, spent += actual` (actual comes from `usage`; if absent, charge worst). A sweeper settles reservations older than `expires_at` (10 min) **as worst-case spent** (conservative). Caps: `HLM_LLM_BUDGET_DAY_USD`, `HLM_LLM_BUDGET_MONTH_USD` (Q4).
- **Ledger** `llm_calls` (no content): `call_id, job_id, task, profile, model_id, prompt_version, schema_version, request_sha256, response_sha256, input_tokens, cached_input_tokens, output_tokens, reserved_usd, cost_usd, latency_ms, outcome ∈ {ok, schema_retry_ok, schema_fail, http_error, timeout, budget_deferred, breaker_open}, created_at`.
- **Cassette + live-gate runner** (CC-5): `librarian/cassette.py`, `eval/live/run.py`, `eval/live/RUBRIC.md`.
- **Privacy controls.** `HLM_LIBRARIAN_ENABLED` (default false until R2). Per-project `projects.policy.librarian ∈ {on, off}` (column `0001_phase0.py:79`, unused so far). This is a small deviation from D-016 and gets logged. `device:*`-scoped items are never sent to an LLM (`HLM_LIBRARIAN_SEND_DEVICE_SCOPED=false`).
- **Worker + actor.** `librarian/worker.py` implements CC-3 capabilities with apply-time recheck, plus the CC-2 kinds. Heartbeat fields: `ready`, `in_flight`, `oldest_ready_age_s`, `failed_24h`, `spend_today_usd`, `reserved_usd`, `breaker_state`.
- **Working memory** `librarian/memory.py`: reserved project `hlm-librarian` (created by `0004`). Each job loads the top-8 `librarian-rule` facts (≤ 1,500 tokens): fresh-wake. Compaction happens in W3b once the project exceeds 200 items or 40k tokens.
- **D-015 skeleton card restored.** `POST /admin/projects` and `hlm.ops project create` write a deterministic skeleton card in the same transaction. A migration step backfills a card as a write event for each existing project without one.
- **`memory.raw` payload paging** (D-026).
- **Migrations `0004_librarian`.** `jobs.kind` += `librarian_write, topic_summary, consolidate, ingest_extract, import_postprocess, pack_review, experience_review`. Also: `jobs.priority smallint DEFAULT 5`, `devices.is_system`, the librarian device row, `llm_calls`, `llm_budget`, `llm_reservations`, the CC-2 event kinds, and the reserved projects `hlm-librarian` and `hlm-global`. Both reserved projects get **no default grants**: `hlm-global` grants are issued explicitly by ops (Q3).
- **Files.** `src/hlmemo/librarian/**`, `src/hlmemo/worker/lease.py`, `worker/main.py`, `server/admin.py`, `ops/`, `core/read_service.py`, `deploy/compose.prod.yaml` + `compose.yaml` (librarian service), `deploy/llm.env.example`, `eval/live/**`.
- **Gates.**
  - G-L1 provider contract against a scripted stub (invalid JSON, 429×3, 5xx×6 → fallback, schema-invalid×2 → `schema_fail`); fake clock asserts the backoff; ledger rows exact.
  - G-L2 redaction corpus: 60 seeded secrets (TR/DE/EN contexts) appear in 0 outgoing requests and 0 audit payloads. False-positive rate ≤ 1% on 200 clean identifier-rich chunks.
  - G-L3 LLM down: the stub returns 503 or stalls 30 s. G4 with the librarian enabled keeps p95 ≤ 500 ms; 100 writes acked. After recovery all jobs complete, with 0 lost and 0 duplicate `librarian` events.
  - G-L4 **concurrent cap**: 50 concurrent calls with the cap set to 3 × worst → exactly 3 HTTP calls reach the stub; ledger spend ≤ cap; a crash between reserve and settle is swept as worst-case.
  - G-L5 replay never calls an LLM (`HLM_LLM_MODE=off`, network denied) → projections identical.
  - G-L6 lease renewal (a 300 s job is not re-leased) + SIGKILL → re-lease, exactly one applied event.
  - G-L7 `authority_lost`: revoke the triggering device between enqueue and apply → nothing applied, event recorded, replay identical.
  - **G-LIVE-A** (release-blocking): bench tasks t1–t4 on the default and fallback profiles, per CC-5.

### W2b Placement, contradiction, cross-project check (6 id)
- **Trigger.** Every committed `write`/`call_the_day`/`import`/`ingest` event enqueues `librarian_write:<event_id>` in the same transaction (priority 3; imports priority 6). Card and session-note items get placement only.
- **Pipeline per new version V.**
  1. **Candidates.** Hybrid retrieval with the triggering device's current scope and grants: top-8 current same-project items of compatible kinds, plus top-5 cross-project `lesson/experience/fact` items where the device holds ≥ read. Pairs with cosine < 0.80 and no lexical hit are dropped. The candidate set is recorded in the audit payload.
  2. **Placement** (T1 schema): `{importance, stability, topic_hint, tags_add ≤ 5}`, applied only to fields the client left unset. Stored in the projection **`version_signals`** (`version_id PK, importance, importance_src, stability_suggested, topic_logical_id, usage_count, last_scored_at, score`). Content is **not** re-versioned, which keeps `expected_version_id` stable.
  3. **Contradiction** (T2+): per candidate `{relation ∈ {none, duplicate, refines, contradicts}, supersedes, confidence ∈ {low, med, high}, reason ≤ 200}`, batched ≤ 8 pairs per call.
  4. **Resolution.**
     - `duplicate` gets `relates_to{dup:true}` (the merge is deferred to W3b).
     - `refines` gets `relates_to`.
     - **Auto-invalidate** only when: `contradicts ∧ supersedes ∧ high`, same home project, identical `device_scope`, C not pinned, C.kind ≠ experience, `V.valid_from ≥ C.valid_from`, the `correct(P)` capability passes the recheck, **and** `librarian.auto_resolve=true`. It is a bi-temporal correction of C (`valid_to = V.valid_from`) plus `supersedes` and `contradicts` links; nothing is deleted.
     - Everything else, including every case while in **shadow** mode (the default), becomes a `question` with `proposed_action` plus a `contradicts` link.
     - A cross-project duplicate or refinement produces a `widen_scope` question only (Q3).
  5. One `librarian` event per job, with `request_id = uuid5(NS, "librarian_write:<event_id>")`.
- **Contract.** `memory.query` gains an optional `librarian:{pending_questions, notices[≤3]}`, budgeted and packed after hits (`query/2`).
- **Shadow audit (Sol Gates).** During dogfooding (D1 onward) and R2, every proposed invalidation and merge is logged via `hlm.ops librarian audit --since … --json`. The owner or orchestrator labels ≥ 100 real proposals (correct/incorrect/near-miss). Auto-resolve may be enabled (Q2) only when the labeled false-invalidation rate is ≤ 0.02 with ≥ 100 labeled proposals, and a 95% upper bound ≤ 0.05 is reported.
- **Files.** `librarian/tasks/{placement,contradiction}.py`, prompts, `db/librarian_queries.py`, shared hunks for `write_service.py` (enqueue), `retrieval.py` (scope-parameterized candidates), `replay.py`, `read_service.py` (notices).
- **Gates.**
  - G-P1 CI replay: golden `payload.resolved` over ≥ 60 placement and ≥ 150 contradiction pairs (the 80 in rev 1 were too few). Includes 40 adversarial near-misses and 20 cross-project cases, TR/DE/EN. Client importance is never overwritten; shadow mode → 0 invalidations.
  - **G-LIVE-B** (release-blocking): placement ≥ 0.90; contradiction exact ≥ 0.90; false-supersede ≤ 0.02 on the minimum across 3 reps.
  - G-P3 capability isolation: 1,000 randomized probes → 0 candidates from ungranted projects; `device:*` items in 0 requests.
  - **G-E-W2b**: the W-E feature gate (≥ +X, no category worse than −Y, stale-claim error not higher) with notices and links enabled.

### W2c Questions + `memory.answer` (2.5 id)
- Projection `librarian_questions(question_id, project_id, subject_project_ids[], device_scope, kind ∈ {contradiction, widen_scope, merge, promote, card_refresh, quarantine, pack_accept}, subject_clues[], proposed_action jsonb, status ∈ {open, answered, expired, superseded, authority_lost}, created_at, answered_at, answer jsonb)`.
- Tool **`memory.answer`** `{project, request_id, question_id, decision ∈ {accept, reject, custom}, note? ≤ 2000, token_budget?}` → Ack. Authorization per CC-3 (the answering device needs write on the union of projects). `accept` applies `proposed_action` through the write service, and **every affected item is rechecked for staleness**: if a subject version is no longer head, the question becomes `superseded` and nothing is applied. `custom` triggers a re-plan job. Every answer is also stored as a `librarian-rule` fact.
- The preflight shows up to 3 open questions. Questions expire after 30 days.
- **Gates.**
  - G-Q1: lifecycle + replay.
  - G-Q2: authorization matrix. A device with write on only one of two subject projects gets `E_FORBIDDEN_PROJECT`; an unreadable subject gets `E_NOT_FOUND`; accepting never widens grants.
  - G-Q3: stale subject → `superseded`, no mutation.

### W2d `memory.risk_check` + `memory.register_lesson` (3 id)
- **`memory.risk_check`** `{project, task (1..4000), token_budget, mode?: auto|deterministic}` → `{verdict ∈ {warn, no_matching_evidence}, judged, warnings:[{clue, title, why ≤ 300, source_project}], candidates_considered, budget}`. Per D-014 it never says "no risk".
  - Deterministic stage: lessons and experiences in the project, plus cross-project ones and `hlm-global` (if granted); top-10; ≤ 300 ms.
  - LLM judge (T4): in the api process, 4 s timeout, subject to the same caps and redaction. On timeout, breaker or budget exhaustion it falls back to deterministic mode (`judged:false`, warn iff RRF ≥ τ calibrated on the fixture).
- **Preflight.** With `--task`, the wrapper calls query and risk_check in parallel. A risk_check failure only degrades to a note.
- **`memory.register_lesson`** `{project, request_id, mistake, fix, context?, tags?, device_scope?}` → a `lesson` item (`## Mistake/## Fix/## Context`), cross-project check at priority 2.
- **Gates.**
  - G-R1 CI replay on ≥ 40 seeded past mistakes (from `docs/decisions`) + 40 negatives.
  - **G-LIVE-C**: catch rate ≥ 0.85, false-warn ≤ 0.10.
  - G-R2 deterministic: catch rate ≥ 0.70, p95 ≤ 300 ms.
  - G-R3 stub stalled → p95 ≤ 4.5 s, `judged:false`.
  - G-R4 G2 budget.

### W2e Low-confidence synthesis (in scope, Sol #10; 3 id)
- `memory.query` gains `synthesize?: bool` (default false). The wrapper sets it only for preflight questions ending in "?" or when `--ask` is given.
- When the flag is set **and** the fast path is weak (top RRF < τ_s, or `evidence:"none"` with near misses), the api runs the synthesis prompt over the top-10 chunks (6 s timeout, capped, redacted) and returns `synthesis:{text ≤ 400 tokens, clues[]}`.
- A validator drops every sentence that does not cite ≥ 1 returned clue. Without the flag, latency is unchanged. With an LLM failure, the response omits `synthesis` and adds `synthesis_unavailable:true`.
- **Gates.**
  - CI: cited clues ⊆ hits in 100%; p95 without the flag unchanged.
  - **G-LIVE-D**: answer accuracy on the W-E weak-evidence subset beats fast-path-only by ≥ X.
  - The W-E feature gate decides the default (on/off) for the wrapper's `--ask`.

### W2f `hlm bench` (2 id)
`src/hlmemo/bench/` (ported from `bench/run.py`; tasks move to `src/hlmemo/bench/tasks/`). Command: `hlm bench [--profile|--model] [--tasks] [--runs N] [--limit K] [--max-usd X]`. It uses the production prompts, provider, redaction and the atomic cap. Its report has accuracy, JSON-failure rate, latency p50/p95, $/task, a projected $/month from `llm_calls`, and `--compare A B` (McNemar). `eval/live/run.py` reuses it. Gates: G-B1 replay report byte-identical; G-B2 `--max-usd` is honored via reservation.

---

## §3 Phase 3: Consolidation, decay/archive, ingestion

### W3a L2 topic summaries, bounded RAPTOR-lite (4.5 id)
- New kind `topic` and rel `member_of` (`0006`). The topic body is an LLM summary ≤ 300 tokens (T3: summary + ≥ 3 cited clues) with `derived_from` links pinned to member versions, reusing the D-015 staleness machinery.
- **Bounded clustering (Sol #4).** Average linkage over 20k items needs about 1.6 GiB for distances alone, so it is replaced by:
  1. **Stage 1: mini-batch k-means** (numpy, `k = ceil(n/25)`, seed = `project_id`, batch 1,024, 30 iterations). Vectors are streamed from the DB in pages of 2,048 (keyset). Memory is O(n·d + k·d): 20k × 384 × 4 B ≈ 30 MiB + centroids.
  2. **Stage 2: exact average linkage inside each stage-1 cluster**, only when its size ≤ 400 (distance matrix ≤ 400² × 4 B = 0.6 MiB). Larger clusters are split by recursive 2-means until they fit.
  3. Topics are clusters with ≥ 3 members. A second level (topics of topics) is created when there are more than 12 topics, max depth 2. A topic is re-summarized only when stale or when membership changes by more than 20%.
  4. Ties are ordered by `logical_id`, so the result is byte-deterministic for a given seed.
- Retrieval: topics are ordinary hits; `drilldown(v<topic>)` gives the summary plus member clues.
- **Gates.**
  - G-T1: ≥ 90% of G3 items are in topics; summary clues ⊆ members 100%; deterministic across 3 runs.
  - G-T2 (Sol Gates): on 30 overview questions, the top-5 contains a topic whose **one-hop drilldown reaches the correct cited gold fact** (≥ 0.85). The earlier "member or topic" credit is dropped. G3 stays ≥ 0.930.
  - **G-T4 capacity**: a synthetic 20k-item project on the 2 vCPU / 8 GiB lima VM (the D-045 rehearsal class), and once more on production at a quiet hour. Librarian peak RSS ≤ 300 MiB (cgroup `memory.peak`), clustering + summarizing wall time ≤ 20 min at `cpus: 0.5`, and G4 query p95 ≤ 500 ms with 3 callers **during** the run.
  - **G-E-W3a**: the W-E feature gate.
  - **G-LIVE-E**: summary faithfulness (every sentence supported by a cited member, judged by rubric) ≥ 0.95.

### W3b Consolidation "sleep" (5 id)
- **Trigger** (report D.5): a project with new events and at least 24 h idle, a nightly window at 03:30 Europe/Vilnius, or `hlm.ops consolidate <slug>`. Dedupe key `consolidate:<project>:<date>`.
- **Steps** (each one is an event, so the run can resume):
  1. **Merge duplicates**: `dup` pairs plus cosine ≥ 0.95 → LLM confirm. Auto-merge only within the same device class and under `annotate`+`correct` capabilities; otherwise a `merge` question. A merge creates a surviving revision plus `supersedes` links; losers are closed with `valid_to`.
  2. **Re-summarize** stale topics. Card refreshes are offered as a `card_refresh` question (the card stays client-authored, D-015, unless `policy.card_author="librarian"`).
  3. **Score**: `w_r·r + w_i·importance/10 + w_u·u`, with `r = 2^(-idle_days/14)` (the D-012 function), `u = min(1, log2(1+usage)/5)`, weights 1/3 each. Experience and stable items use `r = 1`.
  4. **Ranking**: `S'' = S'·(1 + λ(score − 0.5))`, λ ∈ [0, 0.5] tuned on G3 plus `g3c_temporal` plus **W-E corpora (tuned on corpus A, confirmed on corpus B)**. λ = 0 if no value passes every gate, and that outcome is logged.
  5. **Promotion candidates**: ≥ 3 independent confirmations, ≥ 14 days old, no open contradiction → a `promote` question (never autonomous; see Q2).
  6. **Bury**: that is W3c.
- **Gates.**
  - G-C1: golden replay of a 60-day synthetic history.
  - G-C2 (A-MEM regression): G3/g3b/g3c do not drop. Gold evidence may be **correctly** superseded (a bi-temporal correction the fixture labels as expected) but must remain reachable at its historical `valid_at`/`known_at`. Any unlabeled invalidation of gold fails.
  - **G-LIVE-F**: dedupe precision ≥ 0.95, recall ≥ 0.80.
  - G-C4: idempotent re-run.
  - **G-E-W3b**: the W-E feature gate on the full consolidation run over the dogfood HLMemo project and corpus A.

### W3c Decay + reversible archive (D-012) (3 id; LLM-free)
- Daily `archive_cycle` job on the **embed worker**, per project. Candidate iff all hold: `r < 0.1` (about 47 idle days), `volatile`, not pinned, `kind ∉ {lesson, experience, project_card, topic, document}`, no live incoming `derived_from/depends_on/member_of` link, not cited by an open question.
- Counter table `retention_state(logical_id PK, below_count, last_cycle_date)` (rebuildable from events). Archive happens at `below_count = 3`.
- **Shadow** by default: archive events carry `resolved.shadow=true` and change nothing. `hlm.ops archive report`. **Live** mode writes a new version with status `archived`.
- **Restore**: automatic on drilldown/raw of an archived clue, or via `hlm.ops restore`.
- **Gates.**
  - G-A1: simulated 120 days → golden archived set. Property test over 1,000 seeded histories: exempt items are never archived.
  - G-A2 (Sol Gates): the gold set must **not** be pre-accessed. Gold items follow the same synthetic access log as everything else. Criterion: no G3 Recall@5 drop **with `include_archived=false`**, and every archived gold item comes back within one query using `include_archived=true` (or touch-to-restore). The miss count is reported.
  - G-A3: restore is byte-identical and the embeddings are copied.
  - G-A4: replay identical.
  - **G-E-W3c**: non-inferiority (≥ −1 point overall, no category worse than −Y) on the W-E corpora after 120 simulated days.

### W3d PDF/DOCX ingestion (4 id)
- **Versioned document item (Sol #5).** New kind `document` (`0007`). One logical item per document, title = filename. Its body is a deterministic metadata card: filename, mime, pages, extraction status, outline headings. It is not the full text. A re-upload of the same `(project, filename)` with a new sha256 becomes a **revision**.
  - Table `documents(version_id PK REFERENCES memory_versions, sha256, mime, size, bytes bytea, extract_status ∈ {pending, extracted, failed}, extract_report jsonb)` keyed by the document's **version**.
  - `doc_chunk` items link `derived_from` **pinned to the document version**, so the D-015 staleness machinery works when a document is revised.
  - Per-project quota `HLM_DOC_QUOTA_MB` (default 1024).
- **Tool** `memory.ingest_document` `{project, request_id, filename, mime ∈ {pdf, docx, markdown, plain}, content_b64 ≤ 32 MiB decoded, title?, tags?, device_scope?, token_budget?}` → Ack + `document_clue`. CLI: `hlm ingest <file>`.
- **Quarantine as status.** The `memory_versions.status` CHECK gains `quarantined`. The document item and all its chunks are written as `quarantined`:
  - they are excluded from default `memory.query` (the existing status filter admits only `active`);
  - they are **never** in the preflight, risk_check or synthesis;
  - they are reachable only by `query(include_quarantined=true)` or by explicit clue via drilldown/raw, and the response carries `untrusted:true`;
  - approval (`memory.answer` on the auto-created `quarantine` question, or `hlm.ops doc approve`) writes active versions;
  - the librarian never derives lessons or experiences from `doc_chunk`.
- **Parser bounds.** Extraction runs in a **subprocess** of the librarian service with `RLIMIT_AS` 384 MiB, CPU time 120 s, wall time 180 s (killed after that), ≤ 500 pages, ≤ 5,000,000 extracted characters, and an expansion ratio (extracted chars / file bytes) ≤ 50. For DOCX (zip): total uncompressed size ≤ 100 MiB, entries ≤ 2,000, and each entry's compression ratio ≤ 100. Libraries: `pypdf` (BSD) and `python-docx` (MIT); **not PyMuPDF** (AGPL). A bound violation marks the document `failed` with a reason, and nothing partial is activated.
- **Gates.**
  - G-D1 (Sol Gates): **labeled document facts**. The fixtures (TR/DE/EN, a two-column PDF, a DOCX with tables, a scanned page) carry ≥ 40 facts with page/section labels. Evidence Recall@5 ≥ 0.90 after approval, and reading order is checked by ≥ 10 facts that span a column break.
  - G-D2: the quarantine exclusion matrix (query, preflight, risk_check, synthesis) → 0 appearances before approval.
  - **G-LIVE-G** (adversarial, release-blocking): ≥ 10 injection documents (direct, indirect, multilingual, hidden text). After approval, a run of the librarian tasks and of W2e synthesis over them yields 0 librarian-authored lessons from docs, 0 tool-call or instruction compliance per the rubric, and 0 secrets echoed.
  - G-D3: zip bomb, 10,000-page PDF and a pathological PDF each end `failed` within bounds; librarian cgroup peak ≤ 512 MiB.
  - G-D4: bad mime or oversize input → `E_INVALID_ARG` before storage.

---

## §4 Phase 4: Minimal, demonstrated scope (Sol #10, Opus 4)

### W4a L4 experience maturation (4 id)
- Promotion happens only through an accepted `promote` question (capability `global_experience`, CC-3). The librarian drafts the experience (`## When/## Do/## Avoid/## Evidence`, ≤ 300 tokens, `stable`) with `derived_from` links pinned to the confirming lessons. `project_ids = [hlm-global] ∪ source projects` (the answering device must hold write on all of them).
- Retrieval: query and risk_check merge up to 3 `hlm-global` hits for devices with read on `hlm-global` (Q3 default: personal devices only, via explicit ops grants).
- Maturation: only new evidence raises confidence. A contradicting lesson produces a question, never an auto-invalidation. Decay-exempt.
- **Gates.**
  - G-X1: golden promotions from a 90-day history; 0 promotions from fewer than 3 confirmations or from docs.
  - G-X2: risk_check catch rate after 90 days ≥ before (the report's special test); false-warn ≤ 0.10.
  - G-X3: 1,000 probes from a work-class device without the grant → 0 `hlm-global` items.
  - W-E category "gotcha/lesson" does not regress.

### W4b Multi-computer write convergence, narrowed (2.5 id)
- **What is demonstrated.** Every computer writes to one authoritative server. Online concurrency is already solved (idempotent `request_id`, `expected_version_id`, one `events` table). The one real gap is that `hlm close`, `hlm import` and `register_lesson` calls made while the server is unreachable are **lost**; D-047 recorded a client session dropping when the Mac slept.
- **Scope.** An offline **write** outbox in the client: `~/.config/hlm/outbox/<server>/<ulid>.json` (0600). It stores the exact tool call with its `request_id` and `occurred_at`. It is drained by `hlm sync` and after every successful preflight, in `occurred_at` order (idempotent). An `E_VERSION_CONFLICT` on a queued card update becomes a `question`, never a drop.
- **Gate G-S1.** Two simulated devices each go offline and queue 20 writes plus a call_the_day, one with a conflicting card update. Reconnecting in either order gives exactly-once effects, one question, and identical projections.
- **Deferred, with reasons.**
  - (1) Event hash chain: it would need a serialized insert path and a backfill that rewrites event rows. That violates CC-1, and nothing today needs tamper evidence.
  - (2) Standby replica / signed event export: this is the off-host copy mechanism, which D-051 explicitly defers to the owner.
  - (3) Offline read cache: it would weaken the D-014 "block on preflight failure" contract, and no need has been demonstrated.

  Each item needs its own contract and decision if a need appears.

### W4c Packed memory share, minimal (3 id)
- **Format `hlmpack/1`**: a zip with `manifest.json` (`format, pack_id, created_at, source_instance, author{device_name, class}, title, license, item_count, content_sha256`), `items.jsonl` (lesson/experience/fact/topic items with `device_scope=all` only, links within the pack), and `signature.ed25519`. The server key is created with `hlm.ops pack keygen`.
- **Export**: `hlm.ops pack export --project P --clues …|--tag T [--dry-run]`, with mandatory redaction. Refused for `device:*` items and quarantined content.
- **Import**: `hlm.ops pack import F --project P` verifies the signature against `pack_trusted_keys`. The items land in quarantine (`status=quarantined`, same exclusion rules as W3d). A `pack_review` librarian job checks for injection, contradictions and duplicates and opens a `pack_accept` question. On accept, items are activated with `source.system='pack'`, importance ≤ 6, and they are never counted as local confirmations for promotion.
- COGX adapter: out of scope (R6).
- **Gates.**
  - G-K1: round trip; tampering or an unknown key → rejected.
  - G-K2: secrets and `device:*` items excluded.
  - G-K3: an injection pack stays quarantined and yields 0 lessons.
  - G-K4: replay identical.

---

## Workstream summary

| WS | Phase | id | Depends on | Parallel? |
|---|---|---|---|---|
| W-C contract freeze (CC-1..5, 0003/0004 skeletons, schemas, config) | prep | 2 | — | no (orchestrator) |
| W0a minting, fail-closed routes, env migration | W0 | 3.5 | W-C | yes |
| W0b Hostinger outer firewall | W0 | 1.5 | Q1 | yes (ops) |
| D1 dogfood import of HLMemo into production | 1.5 | 1 | R1, W1.5 (markdown/automemory) | no |
| W1.5 import + export | 1.5 | 5 | W-C (Item.source) | yes |
| W-E real-data eval + A/B + baselines | eval | 4 | W1.5 (corpus B import) | yes |
| W2a librarian foundation + live-gate runner | 2 | 6 | W-C | yes |
| W2f hlm bench | 2 | 2 | W2a provider | yes |
| W2b placement / contradiction / cross-project | 2 | 6 | W2a, W-E baseline | partly |
| W2c questions + answer | 2 | 2.5 | W2b | no |
| W2d risk_check + register_lesson | 2 | 3 | W2a | yes |
| W2e synthesis | 2 | 3 | W2a, W-E | yes |
| W3a bounded topics | 3 | 4.5 | W2a | yes |
| W3d ingestion | 3 | 4 | W2a, W3a | yes |
| W3c decay/archive (shadow) | 3 | 3 | W-C | yes |
| W3b consolidation + λ | 3 | 5 | W2b, W3a, W3c | no |
| W4a experience | 4 | 4 | W3b | yes |
| W4b offline write outbox | 4 | 2.5 | W0a | yes |
| W4c minimal packs | 4 | 3 | W2b, W3d quarantine | yes |
| W-D deploys, mem gate, final acceptance | all | 5 | all | no |
| **Total** | | **≈ 70.5 id** (+30–50% review) | | |

---

## §5 Execution order (Sol's §Order merged with W-E and dogfooding)

1. **W0 closure + reachable operator path.** W-C → W0a (+ W0b step A) → **R1 deploy** → RG-routes verified on production.
2. **Import plumbing; freeze real-data truth and baselines.** W1.5 (import + export). A separate agent seals the W-E corpus-B questions **before** D1. Then **D1 dogfood**: `hlm import` of HLMemo's docs, auto-memory and CLAUDE.md into production project `hlmemo`, and every HLMemo dev session uses `hlm claude|codex` from here on (D-021; docs stay the cold-start fallback). The librarian can reprocess this history later (`hlm.ops librarian backfill --project hlmemo`), since events are authoritative. W-E baselines are recorded on corpus A + B.
3. **W2a + W2f**; live provider gate G-LIVE-A for default + fallback.
4. **W2b/W2c/W2d/W2e in shadow.** Proposed mutations are audited on the dogfood project (≥ 100 labeled). W-E gates (G-E-W2b, G-LIVE-B/C/D), A/B gate → **R2 deploy** (librarian on, shadow, caps).
5. **Bounded W3a, then W3d.** G-T4 capacity on the lima VM, then production.
6. **W3b consolidation + W3c archive shadow.** Real-data regression (G-E-W3a/b/c) → **R3 deploy**.
7. **Minimal Phase 4** (W4a, W4b narrowed, W4c minimal) → **R4 deploy**.
8. **Final acceptance** (§6): reconstruct HLMemo on production, then the fresh-chat test and the A/B.

**Parallel waves.** Disjoint owned paths; one DB `hlm_test_<ws>` and one compose project per agent; no agent commits; the orchestrator re-runs gates and commits.

| Wave | Agents (owned paths) |
|---|---|
| after W-C | **W0a** `src/hlmemo/ops/**`, `server/{middleware,devices}.py`, `cli/hlm.py` (device cmds), `deploy/**` · **W1.5** `src/hlmemo/importers/**`, `src/hlmemo/export/**`, `cli/{import_cmd,export_cmd}.py` · **W3c** `src/hlmemo/retention/**`, `worker/archive.py` |
| after R1 | **W-E** `eval/**`, `docs/private/**` (hold-out: no other agent reads it) · **W2a** `src/hlmemo/librarian/{provider,redact,budget,cassette,ledger,worker,memory,actor}.py`, `worker/lease.py`, `eval/live/**` |
| after W2a | **W2b+W2c** `librarian/tasks/{placement,contradiction,questions}.py`, `server/tools/answer.py`, `db/librarian_queries.py` · **W2d** `server/tools/{risk_check,register_lesson}.py`, `librarian/tasks/risk.py`, `cli/preflight.py` · **W2e** `librarian/tasks/synthesis.py` · **W2f** `src/hlmemo/bench/**` · **W3a** `librarian/tasks/topics.py`, `src/hlmemo/cluster.py` |
| after R2 | **W3d** `server/tools/ingest.py`, `src/hlmemo/extract/**`, `librarian/tasks/ingest.py` · then **W3b** (sequential; touches ranking) |
| after R3 | **W4a** `librarian/tasks/experience.py` · **W4b** `cli/outbox.py` · **W4c** `src/hlmemo/packs/**`, `ops/packs.py` |

Hot shared files (`config.py`, `server/tools/{__init__,schemas}.py`, `core/write_service.py`, `core/retrieval.py`, `core/read_service.py`, `db/replay.py`, `alembic/versions/*`) change only in W-C or by the orchestrator. Workstreams hand in their shared hunks as `docs/consults/<nn>-<ws>-shared-hunks.md`.

---

## §6 Deploy plan (Hostinger KVM 2: 2 vCPU / 8 GiB, about 7 GiB visible)

**Per-service limits (Opus 5).** These are hard `mem_limit` values and each is proven under load.

| Service | mem_limit (today → target) | cpus | Peak today | Expected after Phase 4 | Basis |
|---|---|---|---|---|---|
| db | 2g → **1792m** | 1.0 | not measured (est. 0.6–1.0 GiB) | ≤ 1.3 GiB | `shared_buffers=384MB`, `work_mem=16MB`, `maintenance_work_mem=128MB`, `max_connections` set to the actual pool sum; measure first |
| api | 2560m → **2560m** | 1.0 | 1.25 GiB anon (D-044) | ≤ 1.45 GiB + tmpfs | includes the 320m spool tmpfs + 64m /tmp (spec §5 sizing rule) |
| worker (embed + archive) | 1536m → **1536m** | 1.0 | 0.94 GiB anon; file cache reaches the limit (D-045) | ≤ 1.0 GiB anon | archive is SQL only |
| librarian (new) | — → **512m** | 0.5 | — | ≤ 0.30 GiB + parser subprocess ≤ 384 MiB (RLIMIT_AS) | G-T4, G-D3 |
| caddy | 256m → **192m** | 0.5 | ~0.05 GiB | ~0.05 GiB | |
| **Sum of limits** | **6.5 GiB** | | | sum of expected peaks ≈ 4.6 GiB | host keeps ≥ 0.5 GiB for the OS; `migrate` runs only with writers stopped (deploy.sh) |

**Gate G-MEM (load proof; lima 2 vCPU/8 GiB first, then production at a quiet hour).** For 30 minutes, run all of these concurrently:
- (a) a 38.4 MB write and its embedding;
- (b) G4 query load with 3 callers;
- (c) W3a clustering + summaries on 20k items;
- (d) ingestion of a 32 MiB PDF;
- (e) risk_check at 1 request/s against a stub LLM, plus a live-cap run.

Pass: 0 OOMKilled, RestartCount 0, every service's anon peak ≤ 90% of its limit (`memory.peak` and `memory.stat anon` recorded), host `MemAvailable` ≥ 512 MiB throughout, query p95 ≤ 500 ms. Evidence goes to `docs/bakeoff/mem-gate/`. This gate runs before R2 and before R3.

**Release train.** Each release goes through: local gates G1–G8 plus the new workstream gates, the release-blocking live gates (CC-5), a Sol review, the neutral verifier, `deploy.sh`, `remote_gates.sh` + RG-routes, and new remote gates. The backup timer stays uninstalled (D-051). Each deploy's pre-upgrade dump is the rollback point.
- **R1 (W0)**: `0003`, env migration, RG-routes. Then W0b option A.
- **D1** (dogfood import, §5 step 2).
- **R2 (Phase 2 + W1.5)**: `0004`, `0005`. Librarian enabled, shadow on, caps set (Q4). `llm.env` is mounted into api + librarian only. Remote checks: provider egress blocked (temporary ufw OUTPUT rule) → `/ready` 200, writes and queries OK, jobs back off, then drain; G-MEM.
- **R3 (Phase 3)**: `0006`, `0007`, archive shadow. Live archive only after 7 shadow days, a reviewed `hlm.ops archive report`, G-E-W3c, and a decision.
- **R4 (Phase 4)**: `0008`.

### Final acceptance: "HLMemo migrates itself" (D-021 + D-022 + D-051, Sol #9)
The script `deploy/scripts/self_migrate_acceptance.sh --url https://mcp.hlmemo.com` runs from the owner's Mac. Each step is machine-checked; the first failure exits non-zero. Truth is **frozen**: `docs/private/realdata-hlmemo/final/` (the sealed 50-question subset of W-E corpus B, pinned to commit `<C>`, sha256 published) plus `claims.jsonl` (≥ 30 current facts with evidence spans at `<C>`, ≥ 10 of them superseded facts with their current value).
1. **Device**: `hlm_ops.sh device mint --name <host>-final --class personal --grant hlmemo:write --grant hlm-global:read | hlm device login --token-stdin`; `whoami` = trusted.
2. **Idempotent re-import** of the D1 sources at `<C>`: `hlm import … --dry-run --json` then the real run. A second pass makes 0 writes.
3. **Drain**: `hlm_ops.sh status --json` reaches `ready=0`, `failed_24h=0` (timeout 30 min). Run spend ≤ $1.00 (from the ledger).
4. **Reconstruction session (D-022 steps 1–7)**: `hlm claude --headless --task "$(cat deploy/acceptance/reconstruct-hlmemo.md)"`. It diffs claims against the repo, fills gaps from code, registers lessons, and answers open questions. Checks: a session `call_the_day` exists, and the card is non-skeleton and ≤ 512 tokens. Counts are reported, **not graded**.
5. **Evidence retrieval**: for every sealed question, top-5 evidence overlaps a gold span by ≥ 50% of its characters. **Recall@5 ≥ 0.90**, tokens/query ≤ 7k.
6. **Factual answers**: a fresh headless agent answers each sealed question using only HLMemo (the wrapper preflight plus MCP tools; repo access removed by running from an empty temp dir). Grading uses deterministic identifier/number matching where the gold fact has one, and otherwise the fixed-rubric LLM judge (3 reps, live, capped). **Accuracy ≥ 0.85** (minimum over reps). Negative questions: false-answer rate ≤ 0.10.
7. **Stale claims**: for every superseded fact in `claims.jsonl`, the agent's answer and the top-3 hits give the **current** value. **Stale-claim error ≤ 0.05**. For every current fact, 0 contradictions with the repo-truth probe at `<C>`.
8. **risk_check flashback**: ≥ 10 tasks mirroring real past mistakes (`bash -s` stdin, umask 077 checkout, `git add -A` sweep, ORT telemetry abort, …) → `warn` citing the correct lesson in ≥ 8/10; unrelated tasks → `no_matching_evidence` in ≥ 4/5.
9. **Fresh chat after an MCP reload** (D-051): `hlm mcp add claude|codex|agy`, then a headless run with each CLI. The server trace shows the preflight `memory.query` before any other tool call, and the answer passes the step-6 rubric for three fixed questions (current phase, last production decision, the deploy footgun).
10. **Agent-level A/B** (W-E): with-memory success ≥ without-memory success + 2 of 10 tasks.
11. **Regression**: `remote_gates.sh --g7` and RG-routes pass; G-MEM passes.
12. **Bootstrap rule (D-021)**: `hlm export --project hlmemo --out docs/status/hlmemo-export/` succeeds, and re-importing the export into a scratch project re-passes step 5 at ≥ 0.85. `deploy.sh --check` works with HLMemo stopped.
13. On PASS: log a decision "self-hosting live" and switch CLAUDE.md's Project Memory section to HLMemo-first. Then, **after owner confirmation**, delete the local `hlmemo` compose project and the lima VMs (D-051).

---

## §7 Owner questions and research items

**Owner questions (defaults in italics)**
1. **Q1 VPS outer layer**: may I enable the Hostinger VPS firewall via API (80/443 tcp, 443/udp and 22 open; everything else closed), and leave Tailscale-only SSH until after the final acceptance? — *Default: yes to the firewall now; Tailscale re-evaluated later.*
2. **Q2 Librarian autonomy**: after the shadow audit shows a false-invalidation rate ≤ 0.02 on ≥ 100 labeled real proposals, may the librarian invalidate contradicted facts on its own (same project, same device scope, not pinned)? Experience promotion always asks. — *Default: yes, under exactly that evidence rule.*
3. **Q3 Global experience visibility**: should experiences live in `hlm-global`, readable by **personal** devices via explicit grants, with **work** devices excluded unless granted, and cross-project widening only proposed, never applied? — *Default: yes / yes / propose-only.*
4. **Q4 LLM spend caps** (hard, reserved atomically): — *Default: $1/day, $10/month, $2 per live-gate run. When exceeded, jobs wait until the next day and risk_check/synthesis fall back to deterministic mode.*
5. **Q5 Import scope for D-051**: HLMemo only, with the NotebookLM/codex-SQLite importers and the other projects handled as a separate later goal? — *Default: HLMemo only.*
6. **Q6 Real-data gate thresholds**: a librarian or Phase-3 feature is on by default only if it improves the real-data score by ≥ X and no category regresses by more than Y; otherwise it ships disabled. — *Default: X = +3 points, Y = 3 points (W3c: non-inferior, ≥ −1).*

**Research needed**
- R1 (W0b): the Hostinger firewall API: default policy, IPv6, statefulness, interplay with Docker, and browser-console/recovery availability for VPS 2002259. Try it on a firewall bound to no VM first.
- R2 (W2a): OpenRouter + `deepseek/deepseek-v4.1-flash` today: `json_schema` support, whether `usage` reports cached tokens, whether `data_collection=deny` shrinks routing, and the actual per-token prices to seed the reservation. Re-bench with the new prompts (monthly rule, D-019).
- R3 (W2b/W-E): the LongMemEval "knowledge update" subset as a supplementary contradiction/temporal set (CI runtime and licence); TR/DE NLI data for near-miss fixtures.
- R4 (W3a): mini-batch k-means + bounded linkage vs RAPTOR's GMM/UMAP quality on corpus A and B at ≤ 20k items.
- R5 (W3d): pypdf vs pdfminer.six on Turkish ligatures and two-column reading order; whether OCR is ever needed (out of scope unless it is).
- R6 (W2c/W4c): MCP elicitation support in claude/codex/agy as a push alternative to `memory.answer`; the COGX format's status.

---

## §8 Review resolution

| Defect | Resolution | Section |
|---|---|---|
| Sol #1 registration open by default | Default `closed` everywhere except local/test; production settings in the tracked `compose.prod.yaml` environment; startup refuses unsafe config; explicit idempotent `migrate_env_w0`; post-deploy route verification fails the deploy | §1 W0a (settings, G-W0-1/3/7/9) |
| Sol #2 tunnel cannot reach the in-container listener; self-revoke lost | Private listener dropped; ops-only operator path over SSH; public `POST /devices/revoke` kept self-only (contract change logged); Sol's answer (b) route list adopted | §1 W0a (table, operator path, G-W0-2/6) |
| Sol #3 librarian/global write authority | Narrow capabilities (`annotate`, `correct`, `question`, `librarian_memory`, `global_experience`); grants rechecked at apply (`authority_lost`); answers need write on the union of affected projects | CC-3, W2c, W4a |
| Sol #4 clustering memory (1.6 GiB) | Streamed mini-batch k-means + bounded (≤ 400) linkage; G-T4 20k-item peak/runtime/concurrent-p95 gate on 2 vCPU / 7 GiB | W3a, §6 G-MEM |
| Sol #5 document target + injection path | Versioned `document` item; `documents` keyed by version; chunks pinned to it; `quarantined` status excluded from default query, preflight, risk_check and synthesis; subprocess parser bounds | W3d |
| Sol #6 mtime as valid time | Server `recorded_at` kept; mtime/commit date are provenance only; valid time only from explicit evidence, else import time; ADR text drafted | W1.5 |
| Sol #7 caps not hard | Atomic worst-case reservation on day + month rows (bounded `max_tokens`), settle on completion, sweeper charges worst case | W2a, G-L4 |
| Sol #8 verbatim LLM output / cassettes not enough | Versioned redacted audit payload `llm/1` (event schema_version 2); cassettes for CI only; release-blocking live gates (3 reps, pinned fixtures, cost cap, fallback included) | CC-5, G-LIVE-A..G |
| Sol #9 acceptance proves counts, `hlm export` missing | `hlm export` added to W1.5 with a round-trip gate; the final test grades evidence spans, factual answers and stale claims against a sealed truth set frozen at a pinned commit | W1.5, §6 steps 5–7, 12 |
| Sol #10 W2e optional; W4b over-scoped | W2e in scope (3 id, W-E gated default); W4b narrowed to an offline write outbox; hash chain, standby export and offline read cache deferred with reasons | W2e, W4b |
| Sol Gates (tests) | ASGI never-read receive; nmap treated as a smoke check; drilldown-to-fact instead of "member or topic"; no pre-accessed gold in archive tests; correct supersession allowed with historical access; labeled document facts + live adversarial docs; ≥ 150 contradiction pairs + ≥ 100 labeled shadow proposals | G-W0-1, G-W0b, G-T2, G-A2, G-C2, G-D1, G-LIVE-G, W2b |
| Sol Order | Adopted as §5 steps 1–8, merged with W-E and D1 | §5 |
| Opus 1 real-data eval first-class | W-E workstream: corpus A (private YT set, hold-out) + corpus B (sealed HLMemo set); Phase 0 baselines; feature gate ≥ +X, no category worse than −Y for W2b/W2e/W3a/W3b/W3c | W-E, gates G-E-* |
| Opus 2 agent-level A/B | 10 fixed tasks with/without memory, 3 reps, deterministic checks; gate at R2 and final | W-E, §6 step 10 |
| Opus 3 dogfooding early | D1 right after R1 + W1.5; history reprocessed later by the librarian backfill; final D-051 test still last | §5 step 2 |
| Opus 4 scope risk | Each feature must pass the W-E gate or ship disabled; Phase 4 reduced to its minimal demonstrated scope; W2e/W4c are the first cut candidates if W-E shows no gain | W-E, §4 |
| Opus 5 RAM budget | Per-service hard limits (sum 6.5 GiB) + G-MEM 30-minute concurrent load-proof gate before R2/R3 | §6 |
