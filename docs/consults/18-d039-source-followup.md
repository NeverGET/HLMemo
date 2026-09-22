# D-039 source follow-up — fix/d039 (PROPOSED)

Scope: only `/Users/cemalkurt/Projects/HLMemo-bake/fix-d039`, based on 50546f8.
No commits; no edits to deploy/, Makefile, tests/deploy/. Review17 was read in full from
`/Users/cemalkurt/Projects/HLMemo/docs/consults/17-opus-review-d037.md`.

## Coarchitect exchanges (native session-model workers, D-002)

- Readiness worker proposed a shared shielded probe, completed-result TTL including failures,
  and a semaphore held through DB connection close. Root accepted: this prevents both request
  cancellation churn and failed-DB reconnect storms without borrowing either traffic pool.
  `readiness_cache_ttl_s=1`; invalid strict CIDR lists expose a readiness check error.
- Middleware worker proposed 256 MiB process / 128 MiB client body budgets, declared-length
  reservation before receipt, incremental unknown-length reservation, and retaining accounting
  through downstream completion. Temporary body conversion is charged, with 128 KiB/s average
  transfer rate after 64 KiB initial grace. Root accepted and required real SDK 38 MB coverage.
  Reserved capacity is admin-token authenticated; self-revoke stays in the normal pool with
  longer bounded acquisition wait, preserving ordinary queue timeout isolation.
- Card worker proposed SQL interval overlap filtering instead of deleting historical link
  survivors. Root accepted because old provenance and backdated corrections must stay exact;
  normal forward revisions only materialize the latest affected sources. The existing GiST
  exclusion expression matches the new range condition. Replay/event format stays unchanged.

## Independent source reviews

- Card worker (not readiness author) reviewed readiness/app/config/spec: no blocker; confirmed
  atomic task publication, shielding, TTL for errors and successes, semaphore through close,
  and invalid CIDR diagnostics. Suggested explicit timeout recovery coverage and corrected a
  pre-existing sample survivor id greater than the replacement head (now survivor902/head903).
- Readiness worker (not card author) reviewed N5: SQL predicate equals previous Python overlap,
  sole production caller updated, historical read/replay maintained; tests assert 30 closes
  load `[0]+[1]*29`, spanning backdated intervals, and identical replay.
- Readiness reviewer identified retained final ASGI message/chunk references and the admin
  `/ready` routing regression in middleware draft. Root also required no alias bypass via
  `/devices/revoke` and preserved existing self-revoke behavior. Author fixed all findings;
  reviewer re-read the final code and found no remaining blocker. Both self-revoke spellings
  now inspect the caller identity then resolve authoritatively with exclusive auth only for
  self-targets; cross-device denials cannot queue exclusive locks on the attacker.

## Verification — complete

Interpreter: `/Users/cemalkurt/Projects/HLMemo/.venv/bin/python` with `PYTHONPATH=src:.`;
this is essential because its editable install otherwise resolves the MAIN checkout.
Every pytest invocation has explicit HLM_TEST_DSN and
HLM_MODELS_DIR=/Users/cemalkurt/Projects/HLMemo/models.
Only hlm_d039 for ordinary suites; hlm_r1judge for R1; read-only hlm_retr for final G3/G4 once.

Focused readiness + card tests: `12 passed in 2.08s` on hlm_d039, including 500 public probes
per window through actual psycopg connections with both traffic pools occupied.

## Exact deploy handoff (files intentionally untouched)

Pin `networks.frontend.ipam.config[0].subnet: 172.30.39.0/24` in production compose;
set API environment `HLM_TRUSTED_PROXY_IPS=172.30.39.0/24`. Only Caddy and API should
participate in that frontend network. Application default is empty (no implicit trust).
Set Caddy `request_body { max_size 64MiB }` (67,108,864 bytes), matching default
`HLM_REQUEST_MAX_BODY_BYTES=67108864`; the old 2MB edge limit breaks contract maxima.

Focused middleware/lifetime run: 46 passed, one new test failed because it assumed the admin
pool starts at its maximum size (actual min_size=1). Fixed test to observe request checkout
counts. Final targeted rerun: `25 passed in 8.49s` (`/tmp/d039-focused.log`), covering both
cross-device/junk attack route spellings (10/10 admin revocations each), concurrent self-revoke
serialization, existing D-037 pool pressure, and the real MCP SDK contract-maximum writes.

R1: `17 passed in 5.42s` (`/tmp/d039-r1.log`). Explicit `HLM_TRUSTED_PROXY_IPS=172.18.0.0/16`
provided only for this suite: its historical F05 harness simulates Caddy on that subnet;
production/default trust remains empty. Ruff: all checks passed;105 files already formatted.

Full source acceptance selected tests/unit + tests/fixtures + tests/integration. Only G3/G4
are excluded for the required final single read-only run; G2 budget tests are INCLUDED
(unlike D-037). Normal opt-in live CLI/worker-restart tests remain skipped without their env
flags. A /tmp-only collection plugin places G2 last in run1 and first in run2 so hlm_d039's
own freshly generated embedding fixture is reused instead of inferred twice. No fixture
was copied from any other database. Independent card worker executes both runs sequentially.

Broader precheck found two stale G1 tests that counted eight model-directory resolutions to
synchronize eight HTTP readiness callers. With single-flight the directory is correctly
resolved once. Updated only the test observer to count public readiness entrances, retaining
all eight waiter assertions, cancelled-leader behavior, and exactly-one hash/inference/meter
assertions. Focused G1: `2 passed, 3 deselected in 1.05s`. Precheck log `/tmp/d039-precheck.log`:
2 failed,431 passed,4 skipped, interrupted before expensive G2 completion; not an acceptance pass.
Two clean acceptance passes restarted after this fixture adjustment.

Independent readiness reviewer checked the G1 observer change: test intent retained (eight
callers, hash barrier, cancelled leader, pending followers, one hash/inference/meter load).
Also corrected §2 token wording to distinguish hash-based DB identity resolution from the
constant-time configured-admin-token comparison used only for pool admission.

Clean full source suite run1: `437 passed, 4 skipped in 756.11s (0:12:36)`;
log `/tmp/d039-full-1.log`. G2 setup generated11,574 embeddings in hlm_d039 (600.05s),
then all1,000 random budgets/continuations and project-isolation checks passed.
The4 skips are exactly3 live G7 client tests (no HLM_DEVICE_TOKEN) and1 opt-in O2 worker
crash/restart test (no HLM_O2_RESTART); no source test was deselected besides final G3/G4.

Clean full source suite run2: `437 passed, 4 skipped in 156.16s (0:02:36)`;
log `/tmp/d039-full-2.log`. Root independently checked final summaries and asserted sorted
collection equality: the same441 nodeids in both runs; exactly4 G2 tests included. Logs of
selected nodeids: `/tmp/d039-full-{1,2}-collection.txt`.

Final-source R1 rerun: `17 passed in 5.35s` (`/tmp/d039-r1-final.log`), same explicit proxy
CIDR environment as above. Final Ruff: `All checks passed!`; `105 files already formatted`.

Reproduction commands (run from this worktree; main venv is only the interpreter):

```sh
export PYTHONPATH=src:.
export HLM_MODELS_DIR=/Users/cemalkurt/Projects/HLMemo/models
export HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_d039
/Users/cemalkurt/Projects/HLMemo/.venv/bin/python -m pytest tests/unit tests/fixtures tests/integration \
  --ignore=tests/integration/test_g3_recall.py --ignore=tests/integration/test_g4_latency.py \
  -v -ra --tb=short --durations=15 -p no:cacheprovider
```

The actual two acceptance processes invoke pytest.main with the above selection and a
`pytest_collection_modifyitems` hook whose stable sort key is
`int(item.path.name == "test_g2_budget.py")` for run1, its inverse for run2. This changes only
order and avoids a second600s fixture drain. Commands explicitly unset HLM_DEVICE_TOKEN and
HLM_O2_RESTART so opt-in tests cannot use any unrelated live stack.

## Final single read-only G3/G4 gate

Before invoking pytest, a connection with `PGOPTIONS='-c default_transaction_read_only=on'`
asserted `SHOW transaction_read_only = on` and the cached `_state(...,2400)` fixture with
11,574 chunks/embeddings. No reload was permitted. Then exactly one invocation:

```sh
PYTHONPATH=src:. PGOPTIONS='-c default_transaction_read_only=on' \
HLM_MODELS_DIR=/Users/cemalkurt/Projects/HLMemo/models \
HLM_TEST_DSN=postgresql://hlm:hlm@127.0.0.1:5432/hlm_retr \
/Users/cemalkurt/Projects/HLMemo/.venv/bin/python -m pytest \
  tests/integration/test_g3_recall.py tests/integration/test_g4_latency.py -q -s -p no:cacheprovider
```

```text
[G3] overall: Recall@5 = 0.930 (93/100)
[G4] 300 queries / 3 callers: p50=189.2 ms p95=274.3 ms p99=464.9 ms
4 passed in 45.84s
```

Log `/tmp/d039-g3-g4.log`; original HARDWARE `/tmp/d039-HARDWARE-before.md`; measured copy
`/tmp/d039-HARDWARE-measured.md`. A try/finally restored HARDWARE.md byte-for-byte and an
assertion verified equality. This was the only G3/G4 invocation.

Final scope: HEAD remains50546f8 on fix/d039; no commits, deploy or main-checkout writes.
No diff in deploy/, Makefile, tests/deploy/, HARDWARE.md. `git diff --check` clean.
D-037 and D-039 remain PROPOSED. This native review and local validation do not substitute
for the orchestrator's D-036 ratification/Opus review.

Suggested commit message for the orchestrator: `fix: bound readiness and request resource usage`.
