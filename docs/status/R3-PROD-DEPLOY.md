# R3 production deploy — 2026-09-25 21:37–21:45Z (attended; the owner present)

**Result: R3 is LIVE.** Production `https://mcp.hlmemo.com` (VPS 153.92.1.166 = Hostinger VM 2002259, host `srv2002259`) runs
**805f4cd2955b83c258678cf901a32a605ea88f9c** with the **R3 env** (`HLM_ENV_RELEASE=r3`, D-094 mapping, the owner's prod key,
caps HOUR 1 / DAY 2 / MONTH 10, rewrite and per-source cap absent). Every step PASSED; no rollback was needed.
Procedure = the rehearsed D-108/D-119 order (docs/status/R3-REHEARSAL.md), with the owner's waiver of the VM G-L3 item (D-127) and
the prod preconditions of D-128: the new key and caps are live, Docker Hub/ghcr/HF are reachable, Hostinger snapshot **377499** of
VM 2002259 was taken at 21:34:59Z (it restores in ~30 min and expires in 24 h), and there is no HLMemo backup timer.
No other VPS was touched. Evidence: `docs/bakeoff/production-r3/` (logs; 40-hex SHAs abbreviated to 12). No secret is printed there
(key provenance by sha256 prefix only).

| Step | Result | Numbers |
|---|---|---|
| 1 Pre-check (read-only) | PASS | release-state current 6902f91 (previous ee6ce9c); `/ready` 200; librarian observer, heartbeat fresh, backlog 0; R2 check PASS; llm.env unlabelled, fallback `openrouter`, caps 1/2/10, guard on, key sha256 prefix `32fe76f56a2b` = `/etc/hlmemo/openrouter-prod.key`. Baseline: BASE_EVENT 1178; versions 407 (hlmemo-e2e 381, hlmemo 2, gates* 24), links 0, superseded/closed 0/0, version_signals 384, batches 13 (0 applied), open questions 310; lifetime spend $0.5095. Disk 85 GB free. |
| 2 `deploy.sh hlm-deploy 805f4cd…` | PASS | rc 0, ~166 s end to end (fetch, build incl. the model bake on the VPS, dumps, cutover). "llm.env of 6902f91 recorded for rollback"; in-deploy routes PASS (loopback + public); **interim cutover check: `RESULT librarian PASS llm.env=present release=r2-env (D-108 interim …)`**, manifest line `env_release=- (code r3, check mode r2-env interim)`, rewrite/cap `-`; "Deployment ready: 805f4cd… (mcp.hlmemo.com)". **Public downtime 36.1 s** (upper bound 36.8 s; /ready every 0.25 s with a 2 s timeout). release-state: current 805f4cd, previous 6902f91, `previous_llm_env=/etc/hlmemo/llm.env.release-6902f91…`, previous_image hlmemo:6902f91…, no open journal. |
| 3 `install_llm_env.sh --state …` (R3 tooling, under the deploy lock; no `--reset-operator-values`) | PASS | "**kept the operator's OPENROUTER_API_KEY**" (sha256 prefix still `32fe76f56a2b` = the prod key file); caps kept 1/2/10, `HLM_LLM_BUDGET_DISABLED=false`; backup `llm.env.bak-20260925T214050Z`; librarian + api recreated together, healthy; **`RESULT librarian PASS llm.env=present release=r3 manifest=r3`** (per-task fallbacks risk_judge=openrouter-qwen38-27b-fast, synthesis=openrouter, query_rewrite=openrouter; librarian fallback glm53-flash; all 4 profiles key set + reachable); "switch complete"; independent `check_librarian evaluate --llm-env-file … --release r3` PASS; both services: observer, concurrency 3, caps 1/2/10, no rewrite/cap field in the code; env_switch cleared, pending_cleanup []. |
| 4a Remote gates (11, drill ON: attended maintenance window) | PASS 11/11 | health-ready; unknown-path-404; tls-issuer (Let's Encrypt YE2); postgres-closed; probe; routes; hlm-cli; **wan-latency p50 132 / p95 145 / max 195 ms**; risk-check (warn, judged=true, judge=ok); **librarian (1 job in 10.0 s, observer, signal_upsert=1, links/closes 0, applied 0)**; **backup-restore (restore 21 s, marker A survives, B gone)**. Total 121 s. |
| 4b Light e2e (device `r3-e2e-214410`, ci, gates-probe:write, 30 min, revoked afterwards) | PASS | MCP initialize + tools/list: 8 tools (memory.answer, call_the_day, drilldown, query, raw, register_lesson, risk_check, write); memory.write → v411 (826 ms); memory.query finds the marker (355 ms); memory.risk_check verdict=warn judged=true judge=ok (1503 ms); `ops status` ready, migration 0008, worker/librarian ready 0, breaker closed. (Attempt 1 had two harness bugs of mine and made no call; its device was revoked at once.) |
| 5 Observer vs step 1 | PASS | versions 407 → 411 (+4 = gate/e2e markers: gates-probe +3, gates-cli +1; hlmemo-e2e 381 and hlmemo 2 unchanged); **links 0 → 0**; **superseded/closed 0/0 → 0/0**; batches applied 0/13 → 0/13; 4 librarian events since 1178, all observer, ops only signal_upsert (2) / none (2); links_by_librarian 0, versions_by_librarian 0; audit applied 0 (gates-probe, hlmemo-e2e 310 open, hlmemo). |

**Prod LLM spend during the deploy: $0.0014** (place 4, relate 3, risk_judge 2 calls, all luna ok); lifetime $0.5108.

**Final prod state:** release 805f4cd (image `hlmemo:805f4cd…`), env release marker `HLM_ENV_RELEASE=r3`, librarian ON observer with
concurrency 3, rollback pair recorded (previous 6902f91 + `llm.env.release-6902f91…` + quiesced pre-upgrade dump + image id), so
`deploy.sh --rollback hlm-deploy` restores R2 AND its env (rehearsed: docs/status/R3-REHEARSAL.md drill a). The ultimate net is
Hostinger snapshot 377499 (the owner's decision).

**Follow-ups (not blocking):**
- `deploy.sh --accept-release hlm-deploy` stays a separate owner OK (it deletes the retired pre-W0 backup `api.env.pre-w0-20260923T191722Z`).
- The owner may delete `/etc/hlmemo/llm.env.bak-20260925T213343Z` (it holds the old dev key, D-128) and `llm.env.bak-20260925T214050Z` (the R2-env backup with the prod key) once R3 is accepted.
- The e2e re-run per D-099 ("prod remote gates plus an e2e re-run") beyond this light check is the orchestrator's next step.
- Snapshot 377499 expires in 24 h (Hostinger).
