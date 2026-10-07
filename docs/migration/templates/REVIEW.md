# REVIEW: <slug> migration (template, PLAYBOOK §14)

PRIVATE. For the owner's OK before any prod write. Copy to `docs/private/migration/<slug>/REVIEW.md`.

## 1. What will be written
| source / layer | facts | lessons | episodes | decision rows | total |
|---|---|---|---|---|---|
| <source> | | | | | |
- Tier: <Light/Full>. Slug: `<slug>` (new). Card draft: `CARD-DRAFT.md` (≤ 420 tokens).
- Title list: `REVIEW-TITLES.md`. Promotion candidates for `hlm-global`: <n, listed below>.

## 2. What was left out, and why
- Dropped files: <n> (duplicates <n>, stale <n>, index-only <n>, secret <n>, personal data <n>), list in `<file>`.
- Repo documents kept as pointers, not imported: <summary>.

## 3. What to look at
- Flagged uncertain or unverified statements: <list with clues/paths>.
- Conflicts and how they were resolved: <list>.
- Dates: explicit <n>, estimated <n> (`date-estimated`), same-day ordered by time <n>.
- Layer map (layered legacy memory): originals before <freeze date> <n>, notes after <n>, recovered details <n>.

## 4. Gates passed
- Secret gate: gitleaks <0>, importer `skipped 0`, manual grep <clean>, known-value compare <clean / not applicable>.
- `hlm migrate lint`: <0 errors>. Local rehearsal: dry → apply <n>/<n>, re-run all `unchanged`.
- Recall pre-check: <k of n questions with the right source in the top hits>.
- Consult (Full): rounds <n>, findings accepted <n>, rejected <n> (triage file `<path>`).

## 5. Seals
- Final set seal: `<tree digest>` (`final.seal.json`), per-batch counts below.
- Truth set: v<n> sha256 `<hash>` (earlier versions: `<hash>`, gold unchanged).

## 6. Batch plan and exact commands
| batch | files | items | date range |
|---|---|---|---|
- `HLM_MIGRATE_ALLOW_PROD=1 hlm migrate run --target prod --batch <B> --apply --spec migration.toml` per batch, then
  `hlm migrate verify --target prod --spec migration.toml`.
- Expected librarian cost: <items> × about $0.0013–0.003.

## 7. Decisions needed from the owner
1. <question, with the options and a recommendation>
