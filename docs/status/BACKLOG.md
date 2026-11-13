# Backlog (Phase 1+) — items deliberately NOT blocking the first deploy (D-041)
Updated 2026-09-23. Source of each item in parentheses.

## Security / availability (authenticated or low impact)
- **Redactor: JSON-style and quoted multi-word secret assignments (2026-10-07, review 116, D-257).** `librarian/redact.py` stops an assignment value at whitespace and does not read JSON-style keys, so `"password": "…"` and a quoted multi-word passphrase pass unredacted into LLM prompts and into risk_check's redacted titles. Extend the assignment rule to quoted values (single, double, JSON) up to the closing quote, with tests for false positives on prose.
- A revoked device's in-flight upload keeps its body budget until the body deadline (≤ 8222 s); abort in-flight reads on revoke (consults/28).
- A trusted device can hold 64 MiB per request for ~2 h 17 min with a slow chunked upload (two requests → per-client 128 MiB; two client keys → global 256 MiB). Owner-only lever; consider per-device body budget and shorter total cap for chunked bodies (consults/28).
- Gate pool wait capped at 250 ms → 503 under >8 concurrent legitimate requests (previously queued up to 5 s); tune pool size / wait (consults/28).
- Self-revoke under pool saturation loses its longer wait at the gate (admin revoke unaffected) (consults/28).
- Gate lookup has no client-side timeout on a TCP-level DB stall → can hold a per-client slot (consults/28).
- Readiness: model loaded before hash check; a bad-but-loadable file is never rebuilt (consults/28).
- Risk judge timeouts (D-239 watch): `memory.risk_check` answered retrieval-only in the risk-check gate on both post-B3 deploys.
  The judge runs the default profile (openrouter-gpt6-luna), then its fallback (openrouter-qwen38-27b-fast), under ONE 4 s deadline
  (deploy/llm.env.example:41-44). Prod logs show `outcome=ok` and no judge error line, so the timeout is invisible in ops.
  Next: probe each judge profile's latency ($0.001 each); log the judge outcome (judge/timeout/fallback) in the api; consider
  making the fast profile the judge's primary.
  Probe 2026-10-03 (3 real `memory.risk_check` calls, prod routing confirmed): 2 judged ok and precise (the test-DSN warning
  cited the right 2 lessons), 1 timed out → retrieval-only. Intermittent, about 1 in 3, consistent with luna latency near the cap.
- IPv6 through docker userland proxy: all IPv6 clients share one limiter key; withhold AAAA or enable IPv6 on the compose network (consults/26, RUNBOOK).
  **RESOLVED 2026-10-03 (D-242):** the dual-stack `edge` network for Caddy is live; IPv6 clients keep their own address.
- Caddy keeps the client socket open until the next body write after the API's 408 (API budget is freed on time) (closing/recheck 4a).
- `::ffff:a.b.c.d` mapped addresses collapse to one /64 bucket (consults/26, Low).
- XFF junk entry makes the limiter fall back to the proxy IP (consults/20, Low).
- No uvicorn-level protection beyond limit_concurrency 512 against pure slowloris (consults/23, Low-Med).
- **OBSOLETE (D-266, Codex cancelled 2026-10-15): codex review sandbox reads the whole disk** (D-092 side finding): in codex 0.155.1 `-s read-only` lets the reviewer model read repo `.env` and `deploy/.local/` (and ~/.ssh). Run codex reviews/consults from a clean export (git archive of the reviewed commit, no secrets) or with a restrictive permission profile that allows only the export + system paths.

- **FIXED 2026-09-24 (owner reset it: DROP + CREATE, verified empty; `hlm` untouched at 0004)** — shared test DB `hlm_test` was stranded at 0009_language_pivot (found 2026-09-24 by L; unknown migrator, likely a pytest run without HLM_TEST_DSN on the slice-2 branch). Code at main head cannot migrate it. Nobody should use it (every agent uses its own DB), but a bare pytest will fail. Fix: `DROP DATABASE hlm_test; CREATE DATABASE hlm_test OWNER hlm;` (the orchestrator's attempt was blocked by auto mode, so the owner runs it or grants permission). Consider extending the conftest guard to refuse any DB whose alembic head is unknown to the code.
- **Slice 2 (renditions) blockers before HLM_RENDITIONS can ship (D-090):** (1) G-L3 at 100% rendition coverage is still >500 ms p95 even with lower budgets (486–529) or no rendition vector leg (464–534); query p95 405–421 vs 265–313 ms without renditions. (2) After a bulk rendition insert, the DF stats load (~0.3 s idle, 2 s budget) timed out and queries ran without term filtering for the 60 s retry window (G3 0.920 in one stress run). Translate reliability itself is fixed (prompt/schema v2: 34/34 and 33/34 accepted, 0/36 schema failures).

- **Recurring footgun: pytest without HLM_TEST_DSN** (3 agents on 2026-09-24 fell back to the shared `hlm_test`). Make tests/conftest.py REQUIRE an explicit HLM_TEST_DSN (fail fast with a clear message) instead of falling back to `hlm_test`.

- **OpenRouter credits exhausted 2026-09-25 ~02:32Z (HTTP 402; $200.21 used of $200 purchased).** The same key serves local agents AND production. Prod spend today is $0 and retrieval is unaffected; the prod librarian, risk judge and synthesis fall back (D-071: retrieval-only risk). **Spend reconciliation needed:** the account usage rose from $134.16 (2026-09-24 afternoon) to $200.21, about $66, while agents reported only about $18 in the same window. Check whether the HLMemo ledger undercounts real billed cost (reasoning tokens? `usage.cost` vs reservation price?), and use a separate key per environment (prod vs dev/agents) so dev work can never starve production.

## Correctness / ops
- Marker write failure after cutover leaves current-ref behind → later deploys refuse (consults/20, D5 Low).
- Card survivor-link load still grows with links overlapping the interval (N5 residual, consults/20).
- Writes accepted between the live pre-upgrade dump and `dc stop` are lost if that snapshot is restored (documented in RUNBOOK).
- bootstrap.sh re-run with a changed --admin-cidr may stop before 80/443 under set -e; provider "allow 22" rules make --admin-cidr ineffective (consults/23, Low).
- middleware.py docstring says register is "gated" (it is checked only post-body).
- Writer 503 follow-up (consults/93 #4, NOT in the unbilled-503 settlement change): an opt-in, measured single retry of the research writer after a fast, verified-unbilled 503 (per-profile flag; jitter; honour `Retry-After`; re-reserve its worst case; skip it unless the fallback keeps a protected time share); tests for deadline/cancellation and at most two attempts.
## Retrieval / embeddings (D-042)
- Embedder provider interface + async query embedding; gemini-embedding-2 / pplx-embed-v1-4b as opt-in profiles after RRF re-tuning and latency measured from the VPS.
- int8-quantized e5 ONNX to cut RAM/CPU.
- memory.raw paging instead of E_BUDGET_TOO_SMALL for oversized payload_item (D-026).
## Worker (consults/30)
- No lease renewal: >1 worker replica or a job >120 s → takeover ping-pong (compose runs one worker; jobs ≤ ~45 s today).
- Heartbeat written only between jobs → a job >180 s marks the worker unhealthy.
- FIFO queue: small writes wait behind a contract-max write (~37 min) for their vectors (still found via lexical/trigram) → fair ordering.
- OOM/segfault-killed job is re-leased forever (attempts grow, _mark_failed only on Python exceptions) → cap attempts.
- API stop grace 10 s < body timeouts → SIGKILL (exit 137) during an in-flight 64 MiB upload (nothing committed).
- Contract-max write embeds in ~38 min on 1 CPU (2 × 512-token texts per inference) → faster batching / int8 model.

- Worker cgroup memory.peak reaches its 1536 MiB limit through file cache after reboot (anon ~830 MiB, no OOM kill) — watch on the VPS; consider 1792m or dropping page cache pressure (D-045).
- first_deploy.sh `--host-fingerprint` pins only the ED25519 host key; an ECDSA/RSA fingerprint from a provider console is rejected as a mismatch. Accept any key type or name the expected type in the error (hit on the real deploy 2026-09-23).

## Process
- Test suite wall time regressed to ~52 min in the auth-gate round (under investigation in the closing verification).

## Retrieval (W-E findings, 2026-09-23)
- Drill budget vs D-055 "drill the top 5" guidance: on corpus B, 53 of 80 top-5 drilldowns hit the 4000-token drilldown budget and were truncated. Raise the default drill budget for multi-clue calls, or split the budget fairly per clue. Small, pre-librarian; measure on corpus A + B dev.
- W0a /ready detail gating trusts the raw socket peer (loopback = details). This is safe while Caddy reaches the API over the docker network, but a reverse proxy running inside the API's own network namespace would expose details to the public. If the topology ever changes, gate the details on a separate loopback-only listener or an ops token (verifier note, 2026-09-23).
- [FIXED ab4b790] W2e synthesis fallback is ineffective under the 6 s cap: the provider retries the primary with 1/2/4/8 s backoff before trying the fallback, so an outage or stall of the primary times out before the fallback runs (found by gpt-5.6-sol in the D-084 bake-off). Fix: a synthesis-specific attempt budget (one bounded primary attempt, then the fallback within the deadline) + a timeout/503 failover test. The same applies to risk_judge (4 s cap).

## Migration pilot findings (2026-10-06, D-251)
- **RESOLVED 2026-10-06 (D-252): FETCH_S 6.5 / WALL_S 8 / hook timeout 10.** Brief fetch budget was at the edge. `brief/config.py` FETCH_S 3.2 s (WALL_S 4, hook timeout 5): today's fetches took 2.3–3.2 s, and under librarian load the brief fell back to "unavailable" twice (digest still injected). Proposal: FETCH_S ~6.5, WALL_S ~8, hook timeout 10 (mapped projects only pay it).
- **Memory Map at budget for a ~500-item slug.** `map_tokens` was 5984 on every blind-check question (budget ~6k, `core/memory_map.py`): file lines get spread-truncated. Measure the effect before raising the budget or grouping lines.
- **`abstained` on a rejected false premise.** `memory.ask` correctly rejected a negative question's false premise with evidence but returned `abstained:false, confidence:high`; callers that key on the flag would miss it.

- **remote_gates `postgres-closed` depends on the client network (2026-10-07, D-255).** On a mobile carrier that accepts TCP to any port, the client-side probe reports OPEN although the server listens on nothing there. Probe a control port that is known closed: if it also connects, report INCONCLUSIVE and rely on the server-side listener check.

## Migration kit v1.2 candidates (2026-10-07, from the read-only NotebookLM scan)
- **nlm-check signal `rich-text-no-angle`.** In the calibration notebook every note cut on NotebookLM's write path was saved in its rich-text form (escaped markdown) and contains no `<`. This rule matched the known cut set exactly (22/22, 0 false flags); the shipped heuristics caught 21/22 with 1 false flag. Notes that end in a URL caused both the miss and the false flag.
- **URL-ending notes** must not count as `ends-mid-sentence`.
- **Export fallback.** `nlm note list` (nlm 0.7.7) returned NOT_FOUND for some notebooks; the notebooklm MCP `note list` worked. Document it in PLAYBOOK §5.6.
- The originals-based signals never fired in this scan, because notes are not copies of the files written alongside. Keep them; they help only when an agent wrote the same text into two stores.

## Migration kit v1.1 (2026-10-07): SHIPPED in D-261 (8003d1f); still open from it: a server-side warning when a stored body is shorter than the submitted one
- **blindcheck `ask` loses the hlm MCP server (HIGH).** `--restricted` drops the user-scope server: every question comes back `no_call` and the step exits 0. Fix (tested privately): an hlm-only `--mcp-config` (a 0600 temp file, bearer from the environment) plus `--strict-mcp-config`; `ask` exits non-zero when nothing came back `ok`; the README names the venv python.
- **Supersession markers in docs (generated from `explicit_supersession.MARKERS`, with a drift test):** PLAYBOOK §9 and the CURATION-SPEC template list every marker (replaces, superseded by, merged from, and instead of / rather than / yerine in decision rows), plus neutral wordings for partial changes.
- `hlm migrate roundtrip --target local` (verbatim compare) and `hlm migrate containment` (8-word shingles), both as kit commands.
- `tools/migrate/scan.py`: personal-data rules (mail, device ids, private IPs, account and tax ids, key paths) and a known-value compare with masked output; PLAYBOOK §10 covers personal data.
- Lint: a one-entry episode file titled by its file name; skip non-md files plus an `exclude` glob; size limits (fact/lesson 1500, episode 3000); `[tags].scopes`.
- PLAYBOOK: a resumed store (read originals at the freeze commit); pull every repo first and re-validate an earlier plan; team projects (second writer's device, grant, capture mapping, hooks on Windows); a reviewer-agnostic consult; `verify --target local` as the idempotence check; `hlm query --project`.

## First real test-drive findings (2026-10-07)
A project chat that did not build the memory used it for a full session; its notes and a reproduction gave these items (details private: docs/private/test-drive/).
- **DONE in code, pending deploy (D-257, consult 114): a judged result lists `dropped_by_judge`** (the retrieval matches above the threshold that the judge did not warn on). The verdict is unchanged, so G-LIVE-C still measures the same thing. Still open: the 1/3 judge timeouts. Original item: **risk_check: the judge filters out relevant lessons (safety, next release).** Retrieval ranked the right lesson first, yet with the judge on (`judged: true`) the verdict was `no_matching_evidence` (10 of 10 candidates dropped); with the judge timed out, retrieval-only returned it as the top warning. Change: the judge annotates, never removes. Every lesson above the retrieval threshold is listed with the judge's label (the D-246 rule: an LLM label is not trusted alone). The 1/3 judge timeouts (risk-judge probe) belong to the same item.
- **revise leaves a stale title (next release).** Titles state the claim (R6). When `old_span` occurs verbatim in the target's title, apply the replacement there as well (within 200 chars); otherwise return an ack warning. Interim protocol guidance: use `supersede` when the title states the outdated claim.
- **Import title suffix shows the import key, not a repo path.** For migrations the key is the curated file path. When the frontmatter has `source_path`, use it for the suffix; otherwise mark the suffix as an import path.
- **Several revises to one target in one write are all rejected (`batch_conflict`).** Document "one write per target" now; let the server apply non-overlapping revises in sequence once the need is measured.
- **Lessons should not carry current values** (a version string in a lesson went stale within a day). Add to the protocol (R15), the skill and the migration curation rules: the value goes into a fact, and the lesson points to it.

## Second test-drive findings (2026-10-08)
A migrated project's chat used the memory for a day of real work; reproduced on prod 8003d1f (details private: docs/private/test-drive/).
- **DONE (D-262, consults 118–120): risk_check: a retrieval-only result hides every candidate below TAU (safety, next release).** A short task text ranks the right lessons in the top 10 but below `TAU` (0.045, `core/risk_service.py:113,454`), so a judge timeout returns `no_matching_evidence` with 0 warnings; the same task judged warned on the right lesson. `dropped_by_judge` (D-257) lists only candidates above `TAU`, so it does not cover this. Change: when `judged:false`, list the top 3 unwarned candidates as `unjudged` (title, clue, deterministic why, reason). Additive like D-257: `verdict` and `warnings` keep their meaning, G-LIVE-C is unchanged. Two independent test drives saw this phrasing sensitivity.
- **DONE (D-262): Query previews start with the YAML frontmatter of imported items (next release).** `importers/build.py:170` keeps the whole file text, frontmatter included, as the body of an unsplit file, and the query-centred preview often lands on the frontmatter `title` line. Change: the preview skips a leading YAML block of chunk 0 (read side, no data change; drilldown stays verbatim).
- **Importer: bodies without frontmatter (migration kit v1.2, with the title-suffix item above).** Every frontmatter field already lands in a structured field (title, `date` → valid_from, tags; `source_path` → the title suffix). Measured share of frontmatter in curated files: 12.7 % and 6.2 % of o200k tokens in the two migrations. New imports only; a re-run of an earlier migration would then report every item `changed`, so the kit must say so.
- **risk judge `why`: say whether the task follows the lesson's fix or repeats its mistake (measure first).** A task that applied a lesson's fix was warned as "repeating the risk". Build a fixture from the case, then change the prompt; a schema field only after the measurement.
- **Open risks are invisible until someone reads the right fact (design, owner priority).** A fact stated that no uptime monitor existed; the API was down for weeks unnoticed, and the card's manual "Open risks" line did not carry it. Proposal: protocol convention (an `Open risk:` fact tagged `active`, superseded when closed), a deterministic "Open risks (N; oldest 3 with age)" block in the brief, and a count instead of a list on the card. Mind the brief budget.
- **Protocol lines (DONE in D-262):** R17: a long pause that may end the session closes it; the continuation is a new session with a fresh `session_id` (no checkpoint tool: tools/list is at 2998/3000). Skill: `memory.ask` answers "known open risks and lessons for <area>, with clues" when writing subagent briefs.
- **Kit v1.2: `VERIFIED <date>` says what was checked** (`(repo)` vs runtime); checker and lint flag VERIFIED on production or network claims, which PLAYBOOK §6 already keeps unverified. A migrated topology fact carried a repo-only VERIFIED stamp after the topology had changed.
- **Shared infrastructure across projects (owner decision).** Infrastructure facts recorded in one project went stale in another. Option: a shared infrastructure project with read grants for the projects that use it; risk_check already reads every granted project.

## Third test-drive findings (2026-10-09)
A long project session (and its 10-08 notes) gave these items; reproduced where possible (details private: docs/private/test-drive/).
- **DONE (D-263): the SessionStart brief times out on slow networks and calls it "the memory read failed".** Local log: since the 6.5 s budget, 3 of 8 brief fetches failed, each at exactly the budget; successful ones took 3.7-6.7 s. The outer `wait_for` cancels the fetch, `MemoryClient.session()` turns the cancellation into `ToolCallError`, so the hook reports a read failure. The fetch makes about 25 round trips (initialize, 3 queries, a sequential card read, up to 22 raw reads). Fix (client only): one deadline, a named cause, a partial brief on timeout, the card read in parallel.
- **DONE (D-263, consults 121-122): supersede by a title quote.** `old_span` is matched only against the body, so superseding an item whose title holds the outdated claim failed with `span_not_found`. Supersede now also matches the title (scope whole); revise gets a `span_in_title` hint; `E_CARD_TOO_LARGE` names the 420 aim.
- **DONE (protocol R17, f2dce00):** a card line the owner asked for may be added; a second close in the same chat starts its note with the earlier note's clue.
- **A rare exact term loses to its parts (measure first).** A hyphenated rare term from a session note did not reach the top 10; alone it matched one list (score 0.016) while an unrelated lesson matching a stem of one part ranked first. Candidate: a lexical boost for rare exact tokens and phrase matches, measured on the G3 world and real queries before shipping.
- **Optional correction reason on updates.** Writers want "why this changed" visible; today it lives in the body. A wire field costs tools/list tokens (2998/3000); first try a body convention ("Correction: …" first line) and show it in drilldown.
- **DECIDED (D-264): external source pointers (`<source>:<id>:s<page>`).** Search by pointer already works (identifier/trigram legs). Resolution belongs to the client: the server cannot reach a local library, so the project's own MCP tool opens the pointer. A structured pointer list in drilldown is a later idea.
- **Vision items (owner):** salience/importance signals for ranking and recall (surprise, cost, recurrence, the owner's reaction), spontaneous recall outside risk_check, source-project context on cross-project hits, procedural ("muscle") memory with trigger, action, reason, scope and last verification.

## Fourth test-drive findings (2026-10-27)
Two project chats (2026-10-24/25); details private (docs/private/test-drive/).
- **D-267 (branch): the brief "timed out after 6.5 s" was a tokenizer download.** macOS purged tiktoken's temp cache; the hook downloaded the vocabulary during assembly, outside the fetch deadline, and the watchdog ended it at 8 s (8 of 8 field failures 8.05-8.09 s; no log line). Fixed: persistent offline vocabulary + background warm-up, wall-clock-capped fetch, the last good brief on a failed read, a watchdog log line, a skeleton-card note.
- **D-267 (branch): text and small server changes.** Digest/protocol: part-scope supersession, the `updates` fields, closing before a compaction, an optional `Day:` line, setting-scoped claims. `E_NOT_FOUND` names the project of a clue the caller can read elsewhere; `replacement_in_other_item`; the `historical_kind` hint names its scope.
- **Recall at the moment of action: measured, a naive hook is not worth it.** Ceiling 2026-10-27 (40 real agent-team calls, a mechanical lesson query per call, known_at = the call's time): a relevant lesson at rank 1 in 20 %, in the top 3 in 35 %; rank 1 wrong 80 % of the time. Next candidates, each with its own ceiling first: a tag-based pinned list in the brief, or a reranker. Original note: a relevant lesson existed but nothing pulled it before a routine coordination step. Options: a PreToolUse hook that shows 1-2 tagged lessons before agent-team messages or spawns; a brief section of pinned workflow lessons. Same family as "file-anchored recall" below and the "spontaneous recall" vision item.
- **"Similar lesson exists" hint on `register_lesson`** (two writers, one incident). Deterministic nearest-lesson lookup in the ack; measure false positives first.
- **Fresh lessons in `dropped_by_judge`/`unjudged`:** mark lessons recorded in the last 24 h (needs `recorded_at` on risk candidates).
- **Librarian block on every query** is noise for project chats; today labelled as the operator's queue. Dropping it from `memory.query` is a contract change (the brief reads it there).
- **Migration kit: the hand-off requires the first card** (a migrated project kept its skeleton card for weeks).
- **Drilldown of imported items shows the frontmatter lines** (previews skip them since D-262); a compact one-line provenance in drilldown, low priority.
- **Declined for now: a server-generated `request_id`** (it would make transport retries write duplicates); first measure which protocol steps cost writers the most.

## First guest onboarding dry run (2026-11-01)
A fresh clone on macOS, followed as a guest would (details private).
- **Fixed: the user `hlm.toml` ignored `HLM_CONFIG_DIR`.** `hlm init --user` wrote into the override, while the settings loader read `~/.config/hlm` (`tests/unit/test_user_config_dir.py`).
- **`local_stack.sh up` starts on an incomplete model download.** Every query then fails with "read dependencies are not initialized". `up` should verify the model hashes and fail loudly.
- **`make models` exits 69 when the server is unreachable, even after `models OK`.** `doctor --models` also checks the server; on a fresh machine this reads as a failed download.
- **Installability (W3) input.** The guest guide needed: an alias, not PATH (the venv's python would shadow `python3`); `claude mcp add --scope user`; a settings.json merge snippet for the hook; a hand-written `capture.toml`. A `hlm setup` command could do all four.
- **Per-account model later.** `devices.user_id` is always `owner`. A guest's "global" layer is a plain project granted only to their devices (`<name>-global`), and isolation rests on grants.

## Second owner device, Linux (2026-11-11, D-278)
The private kit for the owner's work machine (preflight, install, verify) ran end to end in Fedora containers.
- **Productise it (W3 input, with the guest dry run above).** `hlm setup` should do what the kit's `install.sh`
  does (the `~/.local/bin/hlm` wrapper, user `hlm.toml`, skills, the hook merge with a backup, a starter
  `capture.toml`), and `hlm doctor` should grow the kit's two other checks: the network preflight (install hosts,
  IPv4 and IPv6, the certificate issuer) and the per-mapping brief test.
- **`hlm device login --file-store`.** On Linux the kit forces the credentials file with
  `PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring`, because the hook cannot rely on an unlocked desktop
  keyring. A flag would say this plainly.
- **Notes between the machines: solved without code (2026-11-13).** A shared project, kept out of cross-project
  librarian work, holds them: one item per note, open until a later item supersedes it, read at session start
  in the other place. Still wanted: the brief could show open notes for the calling device's class, and the
  migration kit's `QUESTIONS-FOR-OPERATOR.md` step should say this instead of a file.
- **Not verified on Fedora:** the migration kit's local rehearsal (`podman` is the default there).
- **Test trap:** GNU tar 1.35 cannot extract any archive in an emulated amd64 Fedora container on Apple silicon
  ("Function not implemented"). Test tarballs in a native container.

## The owner account (2026-11-11, D-279)
- **No ops command clears `owner_created`** (review 130 C3). An owner project named `<name>-...` blocks a later
  account `<name>`; today only a hand edit of the policy resolves it. An `ops account release <slug>` verb would.
- **A unique violation in `hlm.project_create` returns the generic database error** (review 130 C7, both paths):
  when `ops project create` commits the same slug between the tool's existence check and its insert.
- **`hlm project create` prints "needs membership of the account its slug names (<account>-...)"** for an owner
  device that lacks the owner grant. The message must stay the one refusal, but the CLI could add a hint.

## First migrations from the second device (2026-11-12): kit, importer and reader feedback
Two migrations were run on the work-class device with no operator in reach: a Light one (58 items, finished) and a
Full one in progress (about 1,700 items expected). Details and the chats' question files are private.
- **Fixed the same day (review 131):** a changed or re-mapped re-import widened a narrowed `device_scope` back
  to `all`; `tags: a, b` passed the lint and was dropped by the import; `local_stack.sh` needed a host `psql`;
  `hlm project list` answered `E_NOT_FOUND` on a production server (it now shows the device's grants).
- **A source cannot state its `device_scope`.** Everything is imported as `all`; machine-only knowledge then needs
  an operator re-scope (export, add `device_scope` to the frontmatter, import). Wanted: a spec default and a
  frontmatter key. Two traps for the design: a device outside the class cannot see the narrowed items, so its
  next import classifies the sources as `new` and duplicates them (guard on `whoami`'s scope); and frontmatter in
  a multi-entry file lands in the preamble item.
- **No per-entry tags in a multi-entry file.** Tags are computed once per file; today's workaround is one file per
  entry. A `Tags:` line under a dated heading would do.
- **Item bodies keep the raw frontmatter** (`build.py` passes the whole text): readers see `title:`/`tags:` lines.
- **`ops project policy set` cannot switch a project's librarian off** (only `librarian_cross_project` is
  settable), although the privacy gate honours `policy.librarian = off`. An ops verb is missing.
- **Import cost is stale in the playbook.** It gives 0.0013–0.003 USD per item for the old pipeline; the D-277
  pipeline with the shadow measured 0.0023 on average (p90 0.0036) over 428 reviewed writes. F5 should include
  a provider-balance check: a large import can empty the balance that `memory.ask` shares.
- **Kit suggestions from the Full migration (candidates for the playbook):**
  - curators read a masked copy of the sources, in which extracted secret values and internal addresses are
    swapped by code for variable tokens with line numbers kept (`hlm migrate mask`);
  - read every commit's diff: about a fifth of the episodes written from commit messages alone were wrong;
  - a "not carried out" verdict is checked against the whole later history and every repository in scope;
  - every security episode states when the condition ended;
  - provisional ids and chain keys for a decision log that parallel packages write, with a consolidation step;
  - §5.9 for legacy stores written in parallel (one primary store per period, the others enrich; a coverage
    manifest checked by code, `hlm migrate coverage`).
- **Scan:** `scan.py` reads only the curated tree, so the pre-scan of a source directory relies on gitleaks
  alone (which found 0 where readers found 17 clear-text credential lines); the key-path rule flags
  `~/.ssh/config` and `known_hosts`.
- **Linux notes for the kit:** gitleaks and Docker are not in the device guide's prerequisites; in zsh a variable
  named `path` is tied to `PATH`.
- **Reader feedback on `memory.query`:**
  - a stack term returns generic lessons with `evidence: "matched"` and no sign of which terms matched;
  - chunks of one item take several slots; resolved and historical lessons rank beside active ones;
  - previews spend the budget on provenance lines instead of the rule; import paths cost title characters;
  - large `omitted` counts with no titles-only mode or cursor; the card eats a small budget;
  - a lesson's version distance (`next@16` against a reader on 15.5) is not shown;
  - a cross-project lesson search by topic or scope tag, without phrasing a task, is missing.
- **`memory.risk_check` after a judge timeout:** the verdict reads `no_matching_evidence` while `unjudged` holds
  applicable lessons just under the threshold (0.044 and 0.042 against 0.045). A verdict that names unjudged
  candidates would be harder to misread. (Adds to "risk judge timeouts" above.)
- **`memory.ask`:** the claims list repeats the answer and its headings; a compact mode would halve the reader's
  cost.
- **Wording.** The kit and the digest say "the owner" throughout and agents copy it; he prefers his name.

## Vision: one session for every project (Cemal, 2026-11-13), and what stands before it
**The aim.** One chat receives the day's list for several projects, plans the order (what runs in parallel, what in
sequence, by the nature of each job), writes the plan into memory and carries it out, instead of one chat per
project that Cemal feeds by hand. He judges it **not ready yet**: memory has to remind by itself first.

**His example.** A session upgrades a framework in one product because of a CVE; memory should say, unasked,
that another product runs a vulnerable version too, and ask what to do about it.

**Measured the same evening.** `memory.risk_check` for that task, asked from the first product, did return the
second product's security lesson about that framework as its top warning. What it did not do: say that the second
product is affected (no fact joins "which project runs which version"), speak without being asked, or keep the
follow-up open until someone works in the second product.

**His refinement (the same evening).** A reminder has three parts: what to do, where it can be done, and when
to raise it. At the work machine a product that lives on the home machine cannot be touched; the right move is
to leave a note for the place where it can be ("at home, update the framework in that product"), and for that
note to come up by itself when a session starts there.

**Proposed order (a proposal, not decided):**
1. The brief shows the open notes for this place and this project at session start. Deterministic and small;
   the notes project between the two machines already stores them.
2. Memory knows where a project can be worked on (each machine's folder mapping, reported to the server).
3. A stack inventory per project: current "runs X at version Y" facts, kept current and searchable.
4. Cross-project recall on request: for a described task, the other projects on the same stack with their
   version distance (the reader feedback of 2026-11-12 asked for the same). Ceiling first: take real past cases
   and see whether a model that reads all memory finds the sibling project; if not, the gap is data (step 3).
5. Push: the recall fires by itself at the right moments (session start, before a dependency or security change),
   and when the sibling cannot be worked on here, the session leaves the note for the place where it can.
6. The librarian above observer, so memory stays true without an operator pass (needs the promotion criterion).
7. The one-session pilot: count how often Cemal still has to carry something to another chat.

Promoting the librarian and proactive recall are two different things: the first curates what is stored, the
second speaks during a session. The example needs the second.

**Research (2026-11-13):** `docs/research/08-human-memory-reminder-layers.md` reads the request against human
prospective memory and the brain's memory systems. It proposes six reminder layers (standing rules, must-remember,
place-bound notes, to-dos, topic-cued reminders, lessons), eight rules for them (store the cue with the intention,
prefer cue-driven surfacing, check at natural breaks, close with a stop tag, do not over-remind, ...), and lists
what else the comparison says is missing: use-time correction, context as the cue, offline consolidation,
demotion of resolved items, salience capture. Nothing in it is decided.

## The first Full import from the second device (2026-11-13): findings on the server
A 1,754-item import ran in ten minutes from the work-class device; verify, the blind check (24 of 25, 0 superseded
values stated as current, 5 of 5 negatives abstained) and the first project created through the owner account all
passed. Private details stay with the migration.
- **A background import starved the interactive tasks: fixed by D-280** (a background share of each cap). The
  import's reviews had used the whole day cap by 09:50 UTC; until midnight UTC `memory.ask` and the risk judge
  refused with `budget` for every user.
- **Cost per reviewed write was 0.0035 USD on average (p90 0.0046)** in this import, against 0.0023 measured the
  day before on small writes: the estimate given to the chat was low by half.
- **One `hlm.export` page failed with 503 under the import's load:** PostgreSQL closed the request's connection
  with 25P03 (`idle_in_transaction_session_timeout`, 5 s) after a 15-second gap in the API log. Something held
  the event loop while a request transaction was open. The client now retries such a read (review 132); the
  stall itself is not diagnosed.
- **A deferred run pays its earlier calls again on the next run** (review 133 C1): only where the room under the
  bound is smaller than the dearest call's worst case. A headroom check before a job's first call would avoid it.
- **A `ProviderUnavailable` hand-back still keeps its call-ceiling slots** (review 133 R2): bounded per outage
  round and it stops at an open breaker, but a long half-open outage could fail a job with `E_CALL_CAP`.
- **No sanity floor for the background share** (review 133 C6): a share whose bound is below one call's worst
  case is accepted, and those jobs wait for ever. A startup warning would do.
- **Deferred attempts fill the ledger:** a job that meets the cap is retried every 60 seconds and each attempt
  writes a `budget_deferred` row (1,157 for one task in a day).
- **The blind check's second grader was `codex exec`,** retired since D-266. It is a second `claude -p` grader on
  another model now (review 132); two models of one vendor are less independent than two vendors.

## Ideas from claude-mem (2026-10-15): parked, evaluate later
Comparison and evidence: docs/research/07-claude-mem-comparison-2026-10.md. Evaluate one at a time, measure first.
- **File-anchored recall:** inject memories tied to a file when the agent reads it (PreToolUse on Read); the cheapest form of "spontaneous recall". Items already carry `describes` → `code_refs`. Measure how often a read file has code_refs and whether the injected items help.
- **Measured fit of the session-start block:** our brief already drops sections until it fits its token budget (`brief/assemble.py:232`); compare the drop order and whether the 9500-character room matches the host's real limit.
- **Write-and-exit spool for capture:** the hook writes the event to a file and exits; a repeated event dedups by file name. Compare with our capture path for hook reliability.

## Ideas from Rool (2026-11-03): parked, theoretical
Rool (rool.dev, SDK github.com/rool-dev/rool-js) gives each user a private cloud VM with an embedded agent, hosted in the EU. Its "Object Memory" is files plus schema-checked JSON records under `/space/<collection>/`, with keyword search only: no AI call, no embeddings, no stored index. It has no temporal model or corrections. It is not an HLMemo alternative. Two ideas, each to be measured before any work:
- **`hlm` as a Rool agent's memory:** a Rool machine accepts a custom remote HTTPS MCP server with header or OAuth auth. Check whether a Rool agent can drive `memory.query`/`write` under the protocol, and what token isolation it needs.
- **EU self-hosted model as a librarian provider:** Rool promises EU-hosted models and no training on user content. That matters for steps that see private items. A public OpenAI-compatible API was not seen (models are chosen per in-machine agent via `getModels()`), so this stays parked until one exists. Then it gets one decisive probe (D-017 profile).

## R4.3 candidates (2026-10-03)
- memory.query: when a hit is flagged `superseded`, pull its superseder into the hit list (as memory.ask's `superseders_pulled` does). On 10-03 a "production budget caps" query returned 4/4 superseded hits, while the current values were only in the card.
- memory.ask answer quality: a same-DB comparison vs main with ≥ 3 samples per arm; extractive (verbatim-only) main-sentence repair; the round-2 findings of consult 99 (URL non-path parts, the alternation bracket guard, the dangling-drop word lists, the preview filter).
- The literal check's pre-existing weaknesses (hyphen parts, nested brackets, digit-less URLs): tighten with a measured before/after.
- Lessons harness: the status-evidence rule let a "resolved" lesson through without a verification quote (caught by a reader in E4C).

## R4.4 candidates
- Superseder pull-in v2 (D-241). Pull a superseder only when the visible flagged chunk contains the link's stale span. Show the
  superseder chunk that holds the link's declaration, and follow the supersedes chain to its newest head. Ceiling first: regrade fresh
  packets with 2 blind readers, bar 80% under the stricter rule. The $0 estimate on the D-241 packets is about 79-84%.
- Risk judge: make the fast profile primary, or raise the 4 s cap after a G-LIVE-C re-check, and log judge outcomes (see above).
- Registration limiter: key IPv6 clients by /64 like the body admission already does (middleware.py:174-182). Today it keys by
  single address (devices.py:97). Dormant while registration is closed; do it before registration opens.
- **Librarian pending-queue hazard (2026-10-03):** 298 owner-accepted proposals are `accepted_pending`; independent verification found
  2 real contradictions, 139 supersessions and 141 no-conflicts. `role set assistant` would mass-apply ALL of them as `contradicts`/`refines`
  (every eligible batch, every project, no preview or subset). Needed BEFORE any promotion: an evented `ops librarian withdraw|resolve`
  (per question: reject, or mark superseded by a curated link), and a promotion pre-check that refuses while unverified accepted_pending
  rows exist. `hlm review` should show the librarian's own doubts (verifier kind and direction flags) next to each proposal.
  Built on branch `r4.4-librarian-queue` (awaiting dual review, then deploy + the RUNBOOK withdraw run): `ops librarian withdraw`
  (status `withdrawn`, migration 0011, `--resolved-by-link` as metadata), the `role set` guard (`--release-pending N`, `--dry-run`)
  and the review card's `verifier`/`flags` lines.
- **remote_gates drill default (2026-10-03):** `remote_gates.sh` runs the backup/restore drill by DEFAULT, and the drill restores over
  LIVE data. On 10-03 it ran on prod after every release; the audit of all 8 safety dumps found only the drill's own marker
  and its librarian note in each window, so 0 real data was lost (D-245). Fix: make the drill opt-in (`--drill`, refused unless the
  state dir or URL is marked non-prod or `--maintenance-window` is given) and keep `--no-drill` accepted as a no-op.


## Backups (2026-11-01, D-276)
- **Own Google OAuth client id: done 2026-11-01.** The remote uses the owner's own OAuth client (Desktop app,
  published "In production"; a forced token refresh was tested). Note: with the `drive.file` scope a client sees
  only the files it created, so the dumps the shared client wrote are invisible to the new one and are never
  pruned. They sit in a second `HLMemo-backups` folder (the one with `pre-upgrade/`), which the owner deletes by
  hand.
- **A gate for the schedule.** `remote_gates.sh` should check `systemctl is-active hlmemo-backup.timer` and
  the newest daily dump's age (lesson v4219).

## Librarian after the shadow week (2026-11-05, D-277)
- **Owner decision: the promotion criterion.** "≥ 150 labelled proposals" is out of reach. Organic writes give
  about 11 shadow proposals a week; replay density is 2.7% of pairs, which needs about 5000 pairs per 150.
  Options:
  - a smaller labelled bar with the same precision bar;
  - accumulate the weekly live-only and shadow-only labels;
  - a larger replay after a balance top-up.
- **Owner: OpenRouter balance.** $6.5 on 2026-11-05, shared by prod and dev. At the new pipeline's rate (about
  $1.5 to 2 a week for the librarian) it lasts about 3 weeks.
- **Recall is the open lever** (replay: about .2 to .45 behind the verifier). The most common miss is a decision
  record's plan step that a later record marks done. Measure a relate prompt change on the labelled replay set
  (private PLAN-3) before building.
- **Watch: the shadow baseline's `relate_schema_fail`** (luna relate/v2 at 2000 output tokens now sees older
  items of up to 4000 characters; review 129 F-M2). If it is above 0, raise `("relate", 2)` and re-record the
  G-LIVE-B cassettes.
- **`check_librarian.py` does not probe the relation review's profiles** (review 129 L6). A bad name stops the
  librarian at startup instead.
- **Weekly:** label the shadow report's live-only and shadow-only supersessions (two blind labellers, the brief in
  the private shadow folder), so the labelled count grows toward the owner's bar.
