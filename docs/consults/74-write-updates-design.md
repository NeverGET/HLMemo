# 74 — Write-time supersession (D-118): design note

Branch `wf-write-updates` from ed8deda (wf-b-real). No migration, no flag: an absent `updates` field leaves every write, event and ack byte-identical. The observer librarian is unchanged (labels only); this is an OWNER action, so there is no question, TTL or role check.

## Schema: `items[].updates` (≤ 8 per item)
`{item, expected_version?, old_span, mode: revise|supersede, replacement?}`
- **Per item, not top-level:** "the new memory" is the item that carries the update, so "replacement ⊂ new body" stays well defined in a 50-item batch.
- **`item` is the clue the agent saw** (`v<version_id>`, or a chunk clue `v<vid>.<n>`). The server derives the logical id, and `expected_version` = vid. The spec-literal form (an integer logical id with `expected_version` required) is also accepted, for the hlm CLI. *Deviation to check:* memory.query and drilldown expose only clues, never logical ids, so the literal form would force a memory.raw round-trip.
- The wire schema carries types only. Pydantic holds the limits (old_span ≤ 2000, replacement ≤ 1000). A malformed shape is `E_INVALID_ARG` for the whole call (the existing contract). Every semantic rule is judged per update.

## Validation order (all inside the one write transaction)
- **Pre-lock (no disclosure):** clue → version row → §4.4(a) visibility for the home project. A hidden or unknown target is `not_found` and is never locked.
- **Locks:** request key → session → ONE sorted `lock_logical_ids` over the batch items ∪ pinned ids ∪ the visible update targets.
- The existing item checks run unchanged; they can still fail the whole call.
- **Then, per update, in order:**
  1. **Visibility recheck** on the current rows. The target is not the carrying item, not changed by another item or update of this batch, and not the project card.
  2. **Capability:** write on every project of the target (`correct(P)`). The touched set is the target's projects ∪ the carrying item's projects. More than one project makes it cross-project, which needs write on all of them, else `E_FORBIDDEN_PROJECT`.
  3. **D-083 policy:** the project rows of all touched sets are read `FOR SHARE` in one sorted call. `relation_allowed` is then checked per update, else `policy_excluded`.
  4. **Version:** the head must equal `expected_version` and be open and active, else `E_VERSION_CONFLICT {current_version_id}`.
  5. **Kinds (D-113):** `revise.revisable`. A historical target becomes link-only (see below).
  6. **Guards** (NFC, byte-exact, reusing `revise.check`; the two judged-statement guards are dropped because there is no relate quote):
     - *revise:* old_span occurs exactly once, on word boundaries, with ≥ 3 words outside it (else `span_whole`, hint: use supersede). The replacement must sit verbatim in the carrying item's BODY, differ from old_span, and meet the ratio (≤ 3× and ≤ 1000 characters). `replacement_visibility`: the target's projects ⊆ the item's projects, and the scope is `all` or the same.
     - *omitted replacement:* the whole trimmed body is used only if it is ≤ 1 statement (`revise._boundaries`) and passes the ratio, else `replacement_required`.
     - *supersede / link-only:* old_span occurs in the target. This grounds the id: a mis-copied clue fails here.
     - *either mode:* a carrying item with `close` or `valid_to` is refused as `item_closed`.
- **After the per-update checks:** `now`, then materialize, then `T = select_T(now, every superseded recorded_at, the updates' included)`, then the event id.

## Lock order (J/D-095)
Item locks (one sorted call) → policy (`FOR SHARE`) → version check → `now` → materialize (sequence ids and reads only, no waits) → event id. The event's FK key-share locks are compatible with our `FOR SHARE`, so nothing waits after `now`.

## Apply reuses B-real; event and replay records
- **revise:** `actor._revise_record` is split into guard and build. The build is reused as is:
  - the survivor covers `[vf, cut)`;
  - the new head covers `[cut, ∞)`, is byte-identical outside the span, and carries `body_sha256`;
  - chunks and meter come from the write path;
  - a pinned self-link with props `{by: writer, relation: revises, scope: part, quote, replacement, from_*}`.

  **Cut:** the carrying item's valid_from if `head.valid_from < it ≤ now`, else `now`.
- **supersede:** `actor._close_record` with the same cut (an effective date inside the validity, else `now`), plus `link_insert` of `supersedes` from the new item to the target (`dst_version_id` pinned, valid_from = cut, props `{by: writer, scope: whole, quote}`).
- **link-only:** `link_insert` of `supersedes` from the new item to the target, with scope `part` + quote for revise (read-side rule 3) and `whole` for supersede. It is skipped if the item's own `links` already hold that edge.
- **Event:** `resolved.updates[] = {index, update, status, mode, reason?, mutations[]}`. Every record is tagged `write_event_id` + `update: [i, k]`, and the revised versions' embed jobs join `resolved.jobs`.
  - Live: after the batch's versions and links, `actor.apply_mutations(records, event_id, T)`.
  - Replay: `_replay_write` does the same from `resolved.updates`, with no LLM and no re-chunking.
  - No librarian job is enqueued for a revised or closed target.
- **Reversal:** `ops librarian revert-update <event> --item i --update k --reason`. It writes ONE compensating `librarian` event (op `revert_write_update`, uuid5 request id) built from `actor.unrevise_records` / `actor.reopen_record` + `link_supersede`. The lock order is the same. It is refused with `E_VERSION_CONFLICT` while a later change depends on the update. The op joins `_REVERSAL_OPS`.

## Errors and partial success
- The new memory is ALWAYS written, and an update never fails the call.
- The ack gains `updates: [{index, update, status: applied|linked|rejected, version_id?, code?, reason, hint?, current_version_id?}]`, only when the request carried updates.
- `code` ∈ {E_VERSION_CONFLICT, E_NOT_FOUND, E_FORBIDDEN_PROJECT, E_INVALID_ARG}.
- Hints are fixed strings (e.g. "old_span is not in that memory: copy it verbatim"), so the pessimistic ack sizes them before any mutation.
- An idempotent retry returns the stored ack.

## Historical kinds (D-113)
If `revise.revisable` refuses the target, both modes become link-only: status `linked`, reason `historical_kind` or `decision_record`. The target's text and validity are never touched. A target is refused when:
- its kind is not in `HLM_LIBRARIAN_REVISE_KINDS` (default fact, lesson, doc_chunk); or
- it is a decision/ADR row.

## Tool description and G-SURF
Appended to the memory.write description: "When an item corrects or updates a memory you saw in memory.query results, add updates: item = that clue, old_span = the outdated sentence quoted verbatim, mode revise (replacement = the new sentence, verbatim from body) or supersede (the whole memory is obsolete). Each update is reported applied, linked or rejected with a hint."

Measured with the gate's own meter: **2807 → 2968 / 3000** (description +71, schema +88).

**For the check:**
1. clue form versus logical id;
2. cut = the item's valid_from, versus always `now`;
3. a supersede of an episode is link-only rather than a close.

## Disposition of the design check (74-astra-design-write-updates.md: GO-WITH-CHANGES)
Choices 1–2 and the cut rule accepted. Applied: (1) a clue fixes both ids; a contradicting `expected_version` is `expected_version_mismatch`. (2) The replacement is searched ONLY in the carrying item's body. (3) A carrier whose valid_from is in the future (≤ 5 min is still a legal write) gets `future_valid_from` for revise/supersede; the memory is written, a link-only update still links. (4) `revise.PROTECTED_KINDS` (episode, session_note) and decision/ADR rows are refused by `revise.revisable` whatever `HLM_LIBRARIAN_REVISE_KINDS` holds (both paths, write-time and B-real). (5) The conflict set counts every id the WHOLE batch mutates (item revisions + every update target); an id with >1 mutator rejects all its updates (`batch_conflict`), so A↔B chains fail order-independently. (6) The tool text is shortened; G-SURF **2956/3000** shipped. The client benchmark (`bench/results/20260925-write-updates.md`) chose the wording: the consult's literal text (2931) scored item recall .833 vs 1.000 for the shipped one; `expected_version` is accepted but no longer advertised (agents filled it with 0), and the wire `item` is a string.

## Disposition of dual review 76 (FIX-NEEDED)
(1) HIGH: the cut is chosen against the TARGETED open head only (`_revise_cut` for both modes, the close gets the concrete cut), and every row the mutation would affect is checked to be exactly that head and not historical. (2) HIGH + (4) MEDIUM: an update whose `supersedes` edge collides with one the item declares or already holds (any overlap) is rejected `duplicate_link`; an applied update now always owns its link. (3) MEDIUM: the target is the segment valid at the write clock (selected after the locks, re-checked at the write's clock); a finite valid-now segment is `not_open`. (5) MEDIUM: a link revert's T includes the link's recorded_at. Regressions: tests/integration/test_write_updates_r76.py (all 7 failed on 8b54800; live + replay).
