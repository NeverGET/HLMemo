# Per-project memory migration: the protocol (TEMPLATE)

Status: v1, 2026-09-30 (GOAL-PLAN Phase C, D-206). Derived from the Phase-5 protocol
(`docs/decisions/PHASE2-4-ROADMAP.md` §4b) and from the first real migration (D-131/D-132, HLMemo itself,
`docs/status/MIGRATION-HLMEMO.md`). Copy this file to `docs/migration/<slug>/AUDIT.md` and tick it per project.
Private material (curated copies, review packages, truth sets) lives ONLY in `docs/private/migration/<slug>/`
(gitignored, directories 0700, files 0600; check with `git check-ignore -v`). This public file and the
per-project `AUDIT.md` carry counts, hashes and pass/fail, never content.

## Guiding principle (owner, 2026-09-30)

"Bir seyi yarim ogrenmek, hic bilmemekten beterdir": learning something halfway is worse than not knowing it.
A migration may therefore never copy old memory in as-is. It must (1) keep provenance on every item,
(2) let newer facts supersede older ones without deleting anything, (3) make not-knowing explicit (mark stale
or uncertain statements instead of silently trusting them), and (4) trust nothing until it has been curated,
dry-run, scanned, owner-reviewed and blind-checked. When a step cannot be done honestly, the item is dropped or
flagged, not guessed.

## Hard rules

- No prod write without the owner's OK on the review package (step 5). Workers prepare; the owner decides.
- Workers never print secrets or personal data. Reports name files and rule ids, never values.
- No LLM API calls during preparation: the curating agent reads and decides; the librarian only runs on prod after step 6.
- Facts are never rewritten. A curator may drop, mark (`CURATOR NOTE`), verify against live state, or fix structure
  (move a paragraph under its own dated heading). A statement that cannot be verified stays as written and is flagged.
- Dates come only from explicit evidence (D-072): frontmatter `date`/`valid_from`, a decision-log row, a heading that
  starts with the date, or `SESSION <date>`. File mtime and git dates are provenance and may ORDER items, never date them.
- One project at a time. A failed audit stops the next project (fix-before-degrade, §4b step 6).

## Naming, scope and grants

- **Slug**: lowercase kebab-case, the product or repository name without a machine or owner prefix
  (`shop-app`, `game-tools`, `notes-app`). One slug per independently resumable subject. Split a source directory into two
  slugs when its topics have different readers, different secrets or different lifetimes; merge sources into one slug when they
  describe one product (auto-memory + serena + CLAUDE.md of the same repo always share a slug). Never reuse a
  slug for unrelated material: a wrongly scoped project is expensive to undo (see Rollback).
- **Cross-project knowledge** is NOT decided here. A generic lesson found during curation (for example a framework
  gotcha) is listed under "promotion candidates" in the review package and promoted to `hlm-global` later (W4a), after
  the owner's OK, by a `lesson` item that names its source project item. Project memories keep their own copy.
- **Grants**: production has no admin HTTP routes. The project is created and the importing device is granted on it over
  SSH by the operator: `bash deploy/scripts/hlm_ops.sh --state deploy/.local/<SERVER_IP> project create <slug> --name '<Name>' --exists-ok`
  then `... device grant <device> <slug> write` (check `hlm_ops.sh device grant --help`; the D-132 device was
  `cemals-mb-pro-3` with `hlmemo:write`). Grant the smallest role needed (`write` for the import, `read` for agents that only
  recall) and remove the import grant when the migration is closed if no agent needs to write there.

## The steps

### 1. Inventory
List every agent-memory source of the project with path, file count, bytes, date span and type: Claude auto-memory
(`~/.claude/projects/<dir>/memory`), serena (`.serena/memories`), context files (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`),
NotebookLM (needs an authenticated export first), codex/other (no importer yet). Record the importer that fits each
(`hlm import automemory|serena|context|markdown`). Repo docs that are not agent memory are listed as out of scope unless the owner
pulls them in. Record which files have secret-rule hits (names and rule ids only).

### 2. Curation (an agent reads every file)
Per file or section decide: **keep**, **drop** (duplicate, stale, trivial, secret, personal data, index-only) or **fix**.
- Write the curated copy under `docs/private/migration/<slug>/curated/`, preserving the original relative path, with a
  provenance frontmatter (`source_path`, `source_mtime`, `curated: <decision>`; add `date:` only from explicit text evidence and list every such case in the review).
- Mark perishable statements ("as of <date>, not re-verified") and conflicts between files (same fact, different value, or a
  later note that supersedes an earlier one: say so in both). Verify cheap claims against the live local tree (paths, commits,
  versions) and write `VERIFIED <date>: ...` lines; production and network facts stay "unverified".
- Drop near-duplicates across sources (keep the superset) and index files whose content is fully in the topic files.
- Decide the slug(s) and justify them.

### 3. Exclusion and secret scan
Only the curated set is ever imported. Excluded by construction: `docs/private`, `.env*`, `*.key`, credentials, raw outputs,
tests and source code. Run `gitleaks dir <curated> --redact --no-banner` on the exact file set: 0 findings required. Then the
importer's own filter (a dry run must report `skipped 0`; a `secret-pattern` skip means a real or look-alike secret remained).
The curator also greps the curated set by eye/regex for credential-shaped assignments (`*_KEY=`, `*_SECRET=`, `*_TOKEN=`, `PASSWORD=`, DSNs with inline passwords): the importer regex and gitleaks BOTH missed a `MINIO_SECRET_KEY=<value>` line in a pilot file (2026-09-30), so the two scanners are a floor, not proof. Files that still hit go to a cleanup list (redact the line, drop the file, or keep it outside HLMemo), never into the import.

### 4. Dry run on a LOCAL database, and the review package
Local stack only: create a scratch database (`CREATE DATABASE hlm_mig_<slug>`), `alembic upgrade main@head`, run the API on a
loopback port with its own admin token, register a scratch device with `write` on the slug. Always run the client with an explicit
local `HLM_SERVER_URL` and `HLM_CONFIG` so the prod `hlm.toml` is never read. Then `--dry-run`, then the real local import.
Write `docs/private/migration/<slug>/REVIEW.md` for the owner:
source -> item counts by kind; the item titles; dropped files and why; flagged uncertain/stale statements; conflicts;
`valid_from` coverage (items with explicit dates, items undated); the batch plan (next step); the proposed slug(s);
what prod would write (counts, token estimate); the exact prod command; the librarian cost (items x $0.0013); the
truth-set sha256; the promotion candidates.

### 4a. Chronological import (oldest first, never mixed)
- Establish each item's date from explicit evidence (see the hard rules). Bucket items by the month of `valid_from` read in the
  owner's time zone (`--tz`), oldest batch first. Items without explicit evidence form an **undated** batch that the owner
  reviews and that is imported LAST (or dated by the owner, or dropped). Note: an undated item gets `valid_from` = import
  time, so it looks newer than everything dated; the owner must know that when accepting the undated batch.
- Run the batches in order, one `hlm import` pass per batch, `--keep-missing` (a batch must never close the items of another).
  The CLI has no date filter yet (W5a/W5c follow-up): until it has one, use a thin driver on the importer API
  (`importers.cli.parse_source` + `import_async`, records filtered by the bucket, same keys as a whole-directory import), or
  stage one directory per batch. Between batches wait until the embedding and librarian queues drain
  (`hlm_ops.sh status`, `librarian audit --project <slug>`) so contradiction/supersession proposals see the history in order.
- The review package lists each batch with its date range and item count. The local import must be run batch by batch in the same order first.

### 5. Owner review and OK
The owner reads REVIEW.md (and any curated file they doubt), resolves the flagged items (keep, date, drop), accepts or rejects the
slug decision and the undated batch, and says OK in the conversation. The OK is recorded in a `D-` entry. Without it, stop.

### 6. Production import (the documented path)
D-131/D-132 path: from the owner's Mac, `hlm` configured by `hlm.toml` to `https://mcp.hlmemo.com/mcp`, a device holding
`<slug>:write`. Sequence: operator creates the project and grants (see Grants); `hlm import ... --project <slug> --dry-run`
per batch (classified against prod: expect only `new`); then the real run per batch with `--keep-missing`, in the batch order.
Record counts, the dry-run vs apply difference, the time window and the spend. Hard stops: any `failed`, `rejected` or unexpected
`changed`/`closed` count; the librarian hour cap pausing is expected, a provider error is not.
Automation note: an auto-mode agent may be refused prod writes; that is correct and is not routed around. The owner grants
the write to the main session, or runs the commands.

### 7. Sealed truth set and blind check
Before step 6, write 10-20 questions with gold answers and verbatim source quotes from the curated set (categories: fact, temporal
incl. a superseded value, procedure, at least one negative that must abstain). Seal it: `sha256sum truthset.jsonl` goes into the
review package; the file stays private. After step 6 a different agent, without access to the curated set, answers each question through
the production MCP (`memory.query` / `memory.ask`) and a grader scores correct / contradicts / abstains. Pass bar (proposed):
>= 0.80 correct, 0 answers that state a superseded value as current, every negative abstains. A failure is a finding, not a retry.

### 8. AUDIT.md sign-off
`docs/migration/<slug>/AUDIT.md` (public: counts, hashes, pass/fail, no content) with: inventory totals; curation counts (kept/dropped/fixed);
gitleaks result; dry-run vs apply counts; batch table; librarian proposals (labeled correct/incorrect/near-miss), spend and latency per item;
scope check (0 items visible to a device without a grant); a 10-fact stale-claim sample re-checked against live state; truth-set sha and blind-check
score; friction points for the tooling. Signed by the owner and the orchestrator; a `D-` entry records the scores.

## Rollback: retiring a wrongly imported project

Memory is bi-temporal and append-only; there is no "delete project". Retire in this order and stop at the first step that is enough:
1. **Stop the exposure**: `$OPS device ungrant <device> <slug>` for every device that can read or write it, so agents no longer see it;
   `$OPS project policy set <slug> librarian_cross_project exclude` so the librarian does not propose links across projects.
2. **Close the items** (validity ends now, history stays and remains queryable as-of): run a corrective import of the same source type
   from an empty directory with `--confirm-close` (the mass-close guard needs it). Repeat once per importer type used (automemory, serena, context, markdown).
   Verified locally 2026-09-30: an empty-directory automemory import closed all 3 items of a scratch project in one run.
3. **Correct single items**: re-import the fixed curated file (one revision, the old version stays as history).
4. **Erase** (a leaked secret or personal data): not possible through the API, because history is kept by design. It needs an
   owner decision and an operator procedure on the server: restore from the pre-import database dump (the deploy scripts take one per
   deploy, and an operator can take one before the import), or a manual database purge plus re-embedding. Plan the dump BEFORE step 6.
Afterwards record what was retired and why in a `D-` entry and fix the protocol step that let it through.

## Artifact map (per project)

| Artifact | Where | Public? |
|---|---|---|
| Inventory, curated copies, REVIEW.md, truth set, cleanup lists | `docs/private/migration/<slug>/` | no |
| AUDIT.md (counts, hashes, pass/fail) | `docs/migration/<slug>/AUDIT.md` | yes |
| Decision + scores | `docs/decisions/DECISIONS.md` (`D-` entry) | yes |
