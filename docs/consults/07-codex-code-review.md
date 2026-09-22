**1. SECURITY**

- **High — [core/read_service.py:392](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/core/read_service.py:392): survivor provenance leaks restricted corrections.** Survivors retain the original scope/body but reference the correcting event. `_payload_item()` returns that event’s replacement item. An `all` → `class:work` correction therefore exposes its secret body through readable survivors; project narrowing leaks similarly. **Fix:** resolve the survivor’s original content provenance and authorize it before serialization.

- **Medium — [core/read_service.py:453](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/core/read_service.py:453): alternate endpoint leak.** Although `raw_links()` filters inaccessible endpoints, `payload_item.links` returns their original logical/version IDs unfiltered, including on cursor pages. **Fix:** sanitize provenance links using the same endpoint authorization.

- **Medium — [core/write_service.py:425](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/core/write_service.py:425): hidden-target oracle.** Link resolution checks the home-project grant but ignores target `device_scope` and pinned-version project membership. Success versus validation errors reveals hidden logical/version relationships. **Fix:** authorize selected endpoints before existence/membership distinctions; return uniform `E_NOT_FOUND`.

- **Medium — [cli/preflight.py:77](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/cli/preflight.py:77): evidence-boundary spoofing.** JSON preserves literal `</hlmemo-preflight>` in stored text, permitting fabricated instructions outside the apparent block. **Fix:** Unicode-escape delimiter characters, place the untrusted-evidence instruction before data, and test malicious titles/previews. This mitigates boundary spoofing, not all prompt injection.

- **High — [compose.yaml:32](/Users/cemalkurt/Projects/HLMemo/compose.yaml:32): database bypass.** PostgreSQL publishes on all host interfaces with hardcoded `hlm/hlm` superuser credentials. Reachable peers bypass every application scope check. **Fix:** bind localhost or remove host publication.

No additional concrete SQL-injection, cursor-forgery, or admin device-scope bypass was established.

**2. CORRECTNESS**

- **High — [server/middleware.py:94](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/server/middleware.py:94): success precedes durability.** Responses are forwarded before the outer commit at line 121. MCP/write savepoints do not commit that transaction; commit failure can lose an acknowledged write. **Fix:** buffer finite responses and commit before sending success. A stubbed commit-failure probe confirmed this ordering.

- **High — [db/replay.py:162](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/db/replay.py:162): valid forward references break recovery.** Replay inserts each item’s links before subsequent items’ versions. An accepted item-0 `derived_from "$1"` violates the immediate target-version FK during rebuild. **Fix:** insert all versions/chunks before any links; cover forward and cyclic batch references.

- **High — [core/write_service.py:581](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/core/write_service.py:581): spanning edge corrections fail.** Overlapping links are stored by `(src,dst,rel)`, retaining only one segment. Correcting across two adjacent segments leaves one current, causing an exclusion violation. **Fix:** collect and supersede every overlapping link.

- **Medium — [db/read_queries.py:156](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/db/read_queries.py:156): copied scopes suppress legitimate results.** `PREFILTER_C AND AUTHZ_MV` lets stale restrictive chunk metadata hide an authorized version, violating the explicit “both directions” requirement. **Fix:** remove the restrictive copied-scope predicate or provide an authoritative fallback.

- **Medium — [core/write_service.py:192](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/core/write_service.py:192): idempotency hashes normalized arguments.** `model_dump()` inserts defaults and removes nulls before hashing; omitted versus explicit `device_scope:"all"` produces the same stored hash despite different verbatim requests. **Fix:** preserve/hash original arguments separately from validated/resolved values, including `call_the_day`.

- **Medium — [worker/main.py:217](/Users/cemalkurt/Projects/HLMemo/src/hlmemo/worker/main.py:217): copied vectors escape fencing.** Predecessor embeddings commit before the final lease check; losing the lease cannot roll them back. **Fix:** fence copied and newly inferred vectors within the same final transaction. Immutable inserts limit the immediate damage.

No additional RRF/dedupe or duplicate-wire-representation defect was established.

**3. OPERABILITY**

- [Dockerfile:20](/Users/cemalkurt/Projects/HLMemo/Dockerfile:20): required model assets remain absent. Current Compose now mounts host `./models`, but a fresh checkout lacks them; writes/queries fail while `/health` reports OK. **Fix:** provision and hash-check pinned assets, preload the tokenizer cache, and make readiness verify dependencies.
- [compose.yaml:88](/Users/cemalkurt/Projects/HLMemo/compose.yaml:88): the implemented worker still uses placeholder `restart: "no"`; crashes leave embedding jobs stranded. **Fix:** `unless-stopped` and worker-progress monitoring.
- [Dockerfile:11](/Users/cemalkurt/Projects/HLMemo/Dockerfile:11), [compose.yaml:99](/Users/cemalkurt/Projects/HLMemo/compose.yaml:99): the test service invokes pytest in an image installed with `--no-dev`. **Fix:** add a test target containing locked test dependencies.

`phase0@head` matches D-026. No concrete normal-path bearer logging leak was established.

**4. Top-3 fixes before completion**

1. **Raw provenance authorization — ≤2 hours:** correct survivor provenance and filter embedded endpoint references; add cross-device/project regressions.
2. **Commit before acknowledgement — ≤2 hours:** buffer responses and test commit failure/process interruption.
3. **Two-pass replay — ≤1 hour:** insert versions before links; test forward and cyclic references.

**5. GO / NO-GO after those fixes**

**NO-GO.** Remaining authorization, temporal-edge, and clean-Compose failures still prevent completion.

Read-only review; no integration tests ran. In-memory probes reproduced key defects. All 24 budget unit tests passed; the broader subset recorded 107 passes, with filesystem-dependent setup errors under the read-only sandbox.