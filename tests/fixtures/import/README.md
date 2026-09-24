# W1.5 importer fixture tree (gate G-I1)

A small, synthetic estate that exercises every importer rule deterministically. The golden
dry-run reports in `golden/` are produced by `hlm import <source> --offline --json --tz UTC` with a
fixed clock (see `tests/unit/test_importers.py::test_golden_dry_run`); they contain no mtimes and no
clock values, so they are stable across checkouts.

- `repo/` — `hlm import markdown repo/docs` and `hlm import context repo`:
  - `docs/decisions/DECISIONS.md`: preamble + three decision rows; D-003 is dated in 2099 and must
    be rejected per item (Sol #6: evidence more than 5 minutes in the future).
  - `docs/notes/changelog.md`: two dated headings → one item per entry, each with its own
    `valid_from`; the text before them is the file's main item.
  - `docs/notes/frontmatter-date.md`: frontmatter `date:` → `valid_from`; it mentions
    `src/app/main.py`, which exists → `describes`.
  - `docs/notes/plain.md`: no date evidence → `valid_from` omitted (the server's import time).
  - `docs/notes/archive/plain.md`: identical copy → skipped (`duplicate-of`), one content group.
  - `docs/other/plain.md`: same normalised name, other content → a `same_name` group, imported.
  - `docs/notes/empty.md`: whitespace only → skipped.
  - `docs/notes/stub.md`: `@plain.md` → a resolved stub, skipped.
  - `CLAUDE.md`, `AGENTS.md`, `sub/CLAUDE.md` (stub `@../AGENTS.md`), `.mcp.json`: context files.
- `automemory/` — a Claude auto-memory dir (`MEMORY.md` index, a top-level `type: feedback` lesson,
  a `type: project` fact with `date:` frontmatter) plus files in the CURRENT Claude Code shape, with
  `type` nested under `metadata:` (e2e 2026-09-24 finding #1; the layout copies real files, the
  text is synthetic): `feedback_deploy_footguns.md` (a lead line, three rules, a `**Why:**`
  paragraph, a second list with its own intro; one rule labelled `**Mistake:**`/`**Fix:**`) → one
  lesson per rule; `feedback_review_style.md` (one rule with `**Why:**`/`**How to apply:**`) → one
  lesson, text unchanged; `user_profile.md` (`user`) and `reference_dashboards.md` (`reference`)
  → facts.
- `serena/memories/` — a serena memories directory (numeric and topical names, a dated H1; its
  two-bullet `lessons_learned.md` becomes two lessons). It is not under `.serena/` because the
  repository ignores that path; `hlm import serena` accepts the memories directory itself or a
  project root containing `.serena/memories`.
