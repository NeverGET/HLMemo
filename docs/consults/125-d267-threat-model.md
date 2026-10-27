# D-267: brief resilience, cross-project clue hint, write-update reasons: threat model and severity rubric (before review)

Written 2026-10-27, before the change is reviewed. Trigger: the fourth round of test-drive notes (two project chats,
2026-10-24/25). Branch `d267-brief-resilience`, commits 4b04655..473b4f2 on top of main cbf71a0.

## The change
1. **Brief hook (client, `src/hlmemo/brief/`).**
   - `vocab.py` (new): the tiktoken o200k vocabulary lives in `~/.cache/hlm/tiktoken` (explicit `TIKTOKEN_CACHE_DIR`
     wins; seeded from tiktoken's temp cache); `load_offline()` never downloads (it swaps `tiktoken.load.read_file`
     for a raiser during `get_encoding`); without a local copy the brief estimates and `warm_in_background()` starts
     a detached `python -P -m hlmemo.brief.vocab` (at most once per process and per 10 minutes, marker file).
   - `hook.py`: the fetch deadline is capped by the watchdog's wall clock minus `ASSEMBLY_RESERVE_S` (1 s); with
     less than 0.5 s left the fetch is skipped (`timeout`/`start`). A full brief (status `injected`, not a dry run)
     is saved to `<state>/briefs/<slug>.json` (0600, atomic replace). On `timeout` without a partial brief,
     `error:network` or `error:server`, the saved brief (≤ 14 days old, same slug, tz-aware date not in the future)
     is injected behind a line that names the failure and the copy's age. Never on `error:auth`; `HLM_BRIEF_CACHE=off`
     disables saving and showing. The watchdog's armed fallback is the saved brief when one exists, and the
     watchdog logs a `watchdog` line before `os._exit`.
   - `assemble.py`: a skeleton card shows a one-line note in "Now"; the card shrink step uses the real token count
     (it used the room-penalising counter and looped forever on a card longer than the room); the pending-review
     line names the operator's queue.
2. **Server read path (`core/read_service.py`, `db/read_queries.py`).** `memory.drilldown` and `memory.raw`: when
   the clue/version is not visible in the requested project, `_readable_home` looks at the version's `project_ids`
   and, for each other project where the caller holds READ, re-runs the same row check (drilldown: `version_live`
   with the same scopes, `valid_at`, `known_at`, statuses; raw: `version_authz`). On the first hit the error is
   `E_NOT_FOUND` with message "... it is in project <slug>: pass project <slug>" and `details.project = <slug>`.
   Otherwise the uniform `E_NOT_FOUND` as before (G5).
3. **Write path (`core/write_updates.py`).** A new rejection reason `replacement_in_other_item` (E_INVALID_ARG) when
   a revise's replacement is missing from the carrier but present in another item of the same write; the
   `historical_kind` hint is reworded (scope). No other outcome changes.
4. **Text.** Protocol R6/R11/R17 and the §6 digest (37 lines, 3859 chars with a 64-char slug; the packaged copy is
   byte-identical), the `hlmemo` skill, `docs/brief/README.md`. tools/list and tool schemas are unchanged.

## What is protected
- **Read isolation (G5, PHASE0-SPEC §4.4 (a)).** A caller learns nothing about a version it could not read: not its
  existence, not its project, not its scope. Errors for unknown, foreign, out-of-scope and not-live rows are uniform.
- **Write contracts.** Every existing update outcome (applied, linked, rejected + reason) stays the same, except the
  one case that now names the sibling. The pessimistic ack bound still holds (no reason or hint longer in tokens
  than the longest before it).
- **The hook never blocks or breaks a session** (exit 0, ≤ 10 s registered timeout), never prints more than one
  stdout line, never writes outside its state/cache directories, never runs a project's code (`-P`).
- **Local privacy.** The saved brief holds memory text: owner-only file mode, only the owner's own state directory,
  shown only for the same slug, never after the server refused the device.
- **No stale-as-current confusion.** A cached brief is always labelled with its age and "may be stale"; a partial
  live brief is preferred over a cached one.

## Failure modes to look for
1. `_readable_home` naming a project for a row the caller could not read there: a scope, status, time or grant
   check that differs from the in-project path; `is_admin` behaviour; a version whose `project_ids` include the
   requested project but fails its scope there and passes elsewhere (is naming the other project a leak?); a timing
   or error-message difference that reveals existence when the hint is NOT given.
2. The hint path raising a different error or code than before (e.g. a DB error inside `_readable_home`), or
   changing the transaction state of the read.
3. The cached brief shown for the wrong slug, after an auth error, when the file is malformed or planted (symlink,
   other owner, huge file), or without its stale label; the cache written for partial or dry-run briefs; a race of
   two hooks writing the same file.
4. `vocab.py`: a download still possible inside the hook (any other tiktoken code path, a hash mismatch path, an
   encoding already cached in the registry); the background process inheriting something unsafe (cwd, env, stdin)
   or spawning repeatedly; `os.environ` mutation affecting other code in the same process (server side? CLI?).
5. The wall-clock cap making the fetch shorter than before in the normal case, or `MIN_FETCH_S` skipping fetches on a
   healthy start.
6. The watchdog log line: writing from the watchdog thread while the main thread also writes; the slug regex picking
   the wrong token from the fallback text.
7. `replacement_in_other_item`: a case that was `applied` or a different rejection before and now changes; the
   pessimistic ack bound.
8. Digest/skill/protocol text contradicting the code (part scope, reason names, the skeleton note).

## Severity rubric
- **HIGH:** any read-isolation leak (a project named, or an existence signal, for a row the caller could not read
  there); a hook path that can exceed the registered timeout, print twice, or crash a session; the cached brief shown
  for another slug, after an auth refusal, or without its stale label; a download inside the hook; a changed outcome
  for an existing write update other than the one documented.
- **MEDIUM:** a wrong reason or hint; the pessimistic ack bound exceeded; cache/warm-up files with wrong permissions or
  unbounded growth; docs contradicting the code; a test that does not prove what it claims.
- **LOW:** wording, comments, test gaps without a reachable failure.
