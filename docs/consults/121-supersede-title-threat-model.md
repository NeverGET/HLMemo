# Supersede by a title quote, precise span hints, card-size aim: threat model and severity rubric (before review)

Written 2026-10-09, before the change is reviewed. Trigger: the third test-drive notes (BACKLOG "Third test-drive
findings"). A writer superseded an item whose title held the outdated claim; quoting the title gave `span_not_found`
because `old_span` is matched only against the body (`core/write_updates.py` → `librarian/revise.py`). The protocol
says supersede is for "the whole memory or its title is outdated", so the refusal contradicted the rule it enforces.

## The change
1. **Supersede accepts a title quote.** With `mode: supersede`, a span not found in the body is matched against the
   target's title with the same rules (NFC, byte-exact, exactly one occurrence, no word cut). On a match the link is
   written as before: scope `whole`, `quote` = the span, plus `quote_in: "title"`. A span found in the body takes the
   body path unchanged.
2. **Revise names the title.** With `mode: revise`, a span found only in the title is refused with a new reason
   `span_in_title` and a hint to use supersede; `span_not_found` now says it searched the body.
3. **Card size.** `E_CARD_TOO_LARGE` names the protocol aim (≤ 420) next to the hard limit (512).
4. No tool description or schema change (tools/list stays at 2998/3000).

## What is protected
- **Append-only history and the supersede contract:** a supersede closes exactly the targeted item and records why;
  nothing else changes. Part-scope quotes must stay re-checkable against the body (`span_quoted_once`).
- **Grounding:** an update must cite text that really is in the target (no invented spans); the same NFC, uniqueness
  and word-boundary rules apply to title and body.
- **Visibility and authority:** the target must be readable and writable by the caller, in its project and device
  scope, as today (VISIBILITY_GUARDS). Historical (link-only) targets behave as today.
- **Replay and idempotency:** a replayed request returns the stored ack; the ack sizing still bounds every hint.
- **Revise semantics:** revise still changes only the body; titles are never rewritten by this release.

## Failure modes to look for
1. A title match that bypasses a guard the body path applies (visibility, closed or superseded target, historical
   target, cross-project target, device scope).
2. A title span accepted for revise, or a part-scope link created with a title quote (it would then fail the body
   re-check and could hide or mis-scope the link).
3. A span that occurs once in the title and once in the body counted as unique, or matched across the title/body
   boundary.
4. A change in the outcome of any request that passes or fails today for a body span.
5. The ack exceeding its pessimistic size bound with the new hint.

## Severity rubric
- **HIGH:** a supersede that closes an item the caller could not supersede today through a body span (visibility,
  project, scope, state); a part-scope link with a title quote; any change in the result of an existing body-span
  request; a revise that rewrites a title; a replay that returns a different ack.
- **MEDIUM:** a wrong reason or hint, an ack over its size bound, docs contradicting the code, a title match with
  rules looser than the body's.
- **LOW:** wording, comments, test gaps without a reachable failure.
