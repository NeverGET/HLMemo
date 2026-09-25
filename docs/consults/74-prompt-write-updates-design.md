# Consult 74 — routine design check (D-085, astra-low): write-time supersession (D-118)

Your cwd is a clean export of 93ebdff (branch wf-write-updates; it stacks on librarian v3 + B-real). Read docs/consults/74-write-updates-design.md (the design note) and the rows D-083, D-095, D-110, D-113 and **D-118** in docs/decisions/DECISIONS.md. Look at the existing code it reuses: the memory.write tool and core/write_service.py, the B-real revise path (librarian/revise.py, actor version_revise, reversal) and the G-SURF budget test. This is read-only and a design review only.

## The implementer's three choices; judge each
1. `item` is the clue id the agent saw (`v<version_id>`), which yields both the logical id and expected_version. The integer logical id + expected_version form is also accepted, because query and drilldown expose only clues.
2. `updates` lives per item (`items[].updates`), not at the top level, so that "replacement ⊂ this item's new body" is well defined in batch writes.
3. The cut is the carrying item's valid_from if that lies inside the target head's validity, else now. Historical kinds are link-only in BOTH modes, and an episode is never closed.
G-SURF goes 2807 → 2968 of 3000.

## Focus
- **Can a writer abuse `updates`?** Consider:
  - updating items outside its capability or project;
  - rewriting history via valid_from (a backdated cut that hides a value that was valid);
  - updating an item through a stale clue;
  - chaining updates within one batch (item A updates B, then B updates A);
  - an update whose replacement is quoted from a DIFFERENT item in the same batch.
- **Transaction semantics:** the new memory is always written and each update is independently applied or rejected. Any partial-failure state that replay would reproduce differently?
- **Lock order vs J/D-095**, and deadlocks between two concurrent writes that update each other's targets.
- Whether the cut rule (3) can produce overlapping or gapped validity, or a future cut.
- Whether 32 tokens of G-SURF headroom is a real risk. Suggest trims if cheap.

## Output (≤ 25 lines)
- `## Verdict (GO | GO-WITH-CHANGES | REDESIGN)`
- Positions on choices 1–3, one line each.
- Findings: severity | where | trigger | change. Real design defects only.
