---
name: hlm-migrate
description: "Walks a project's own chat through migrating its legacy memory (Claude auto-memory, serena, CLAUDE.md, NotebookLM, repo notes, earlier projects' lessons) into HLMemo under the migration PLAYBOOK: owner decisions, inventory, curation, the secret gate, a local rehearsal, the review package, the sealed truth set, the production batches and the hand-off to the operator. Use it when the owner says \"migrate memory\", \"HLMemo migration\", \"/hlm-migrate\", \"move our memory to HLMemo\" or \"hafızayı HLMemo'ya taşı\"."
---

# HLMemo migration for a project chat

This skill turns a project's legacy agent memory into curated, dated HLMemo items in the project's own slug. The
source of truth is the migration playbook, `docs/migration/PLAYBOOK.md` in the HLMemo repository (a copy is in
`references/PLAYBOOK.md`; if the two differ, follow the repository and tell the owner). The writer rules of the
`hlmemo` skill (protocol R1–R21) apply throughout.

The reason for the care: memory is append-only, later chats act on it, and "learning halfway is worse than not
knowing". An old store copied in as it is gives stale answers stated as current; a curated, dated, blind-checked
import passed the blind-check bar with no stale answer stated as current in the first migrations (D-216, D-251).

## 1. What this skill does, and what it leaves to others

- **It works in this project's own slug only** (R3). The slug is the one the owner confirms in step 0.
- **It does not create the project.** The library operator creates the slug, grants the importing device `write`
  and takes a pre-import database dump, at F5, after the owner's OK on the review package. An early grant would let
  any chat on the machine write into the slug before review.
- **Production writes wait for the owner's OK given in this chat.** If a permission prompt or the auto-mode
  classifier refuses a production write, that is the system working: stop and tell the owner what you tried and
  why. Do not reach production another way (no other tool, no SSH, no other slug, no new token).
- **The operator side** (queue check, decision-row links, the blind check, the librarian review, the `capture.toml`
  mapping) needs operator tools (R20) and an agent that did not prepare the import. You prepare it and hand over.
- **Private material stays private.** Everything about the project goes under `docs/private/migration/<slug>/` in the
  HLMemo repository (directories 0700, files 0600). That folder is gitignored; check with `git check-ignore -v`. The
  HLMemo repository is public, so nothing about the project goes anywhere else in it.

## 2. Step 0: decide with the owner

Ask these before any real work, because they decide everything after them (PLAYBOOK §2, §3):

1. **Slug.** Lowercase kebab-case, the product or repository name, no machine or owner prefix. One slug per
   independently resumable subject; earlier attempts at the same product usually belong in it as lessons.
2. **Scope and sources.** Which stores exist (auto-memory, serena, `CLAUDE.md`/`AGENTS.md`, NotebookLM, repo docs,
   results, git history, earlier projects), and which are in. Repository documents are pointers by default.
3. **Tier.** Light: a few dozen agent-memory files, up to about 100 items, one curator pass and a checked sample.
   **Full** when any one of these holds: more than about 100 items, a NotebookLM notebook, research results the items
   must point to, earlier projects whose lessons carry over, or **layered legacy memory** (the knowledge was already
   migrated once, PLAYBOOK §5.9). Moving from Light to Full mid-way is cheap.
4. **Language** of the bodies and titles, and whether the truth set asks in two languages.
5. **Dated-note sources** and the **era boundary** (the model in use), if model-behaviour lessons exist (D-236).

Record the answers in `PLAN.md` as owner decisions.

## 3. The first 30 minutes

1. Read the project repository's state: uncommitted paths (commit or list them, so `file:line` pointers stay stable),
   the remote, the slug rules.
2. Step 0 with the owner (above).
3. In the HLMemo repository: create `docs/private/migration/<slug>/` (0700), run `git check-ignore -v` on it, copy
   `docs/migration/templates/*` into it, start `PLAN.md` (phases, gates, an owner-decision log, a "repo fixes made
   during the migration" list) and `migration.toml` (`slug`, `tz`, `[paths]` curated/private/repo, `[[sources]]`,
   `[tags].closed`, `[local]` and `[prod]` server URL and device).
4. Start the local stack: `bash tools/migrate/local_stack.sh up <slug>`, then
   `eval "$(bash tools/migrate/local_stack.sh env <slug>)"` in the shell that talks to it. Confirm the loopback URL.
5. Inventory: Light, read the sources yourself; Full, start read-only inventory agents (docs, results, auto-memory +
   `CLAUDE.md` + serena, the NotebookLM export, the git timeline, the rule catalog), each writing records
   (`templates/INVENTORY.md`) and a summary and nothing else.
6. Secret pre-scan of the sources (`gitleaks dir <path> --redact --no-banner` and a manual grep): file, line and rule
   id only, never the value.
7. Layered legacy memory: map the layers and the freeze date now (PLAYBOOK §5.9).
8. Write `QUESTIONS-FOR-OPERATOR.md` with only the open points (§6 below).

## 4. The phases

The `hlm` CLI is the one in the HLMemo repository's virtualenv (`.venv/bin/hlm`). Every `hlm migrate` command takes
`--spec <private dir>/migration.toml`, runs from a neutral working directory and never reads an `hlm.toml`: server and
device come from the spec, the token from `HLM_DEVICE_TOKEN` or the credential store. Exit codes: 0 ok; 1 lint errors,
a seal mismatch or recall below `--min-rate`; 2 a hard stop; 64 a usage or spec error; 65 refused before anything was
sent.

**F0 Inventory** (§3 above; PLAYBOOK §5). One line per source: path, files, bytes, date span, type, the importer that
fits, secret hits by file and rule id. NotebookLM: export read-only (sources' raw content, notes with bodies, a
manifest with sha256), and set a freeze date. Gate: the owner confirms tier, slug and scope.

**F1 Architecture.** Light: a short note in `PLAN.md`. Full: `ARCHITECTURE.md` with the layers, the lines or topics
(they become title prefixes, because no tool filters by tag), the kind mapping, dates, language and the package plan.
Gate: the owner's OK.

**F2 Rules** (Full, or when the project has many agent rules). Catalog the rules of `CLAUDE.md`, handover prompts and
feedback memories (keep, reword, merge, retire, move to memory), and draft a calm `CLAUDE.md` with the reasons kept.
Memory keeps the reason and the evidence, never an imperative or standing authority ("you may deploy"): a later chat
would read it as permission. Gate: the owner reads the draft.

**F3 Curation** (PLAYBOOK §6, §7, §8, §9). Write the curation spec first (`templates/CURATION-SPEC.md`) and pilot it
on one package. The rules that carry the most weight:
- **Facts hold only the present; history goes into dated episodes and decision rows.** A `STATUS` or handover note
  never becomes a fact; the current state is verified in the repository.
- Layout: one fact per file under `status/<topic>/`, one lesson per file under `lessons/<topic>/` (Mistake / Fix /
  Context; a lasting rule, no current values), monthly episode logs `sessions/<topic>/<topic>-<YYYY-MM>.md` and a
  decision log `decisions/DECISIONS.md`. **Grouped files start with their first entry**: no frontmatter, no H1.
  File names are unique.
- Titles: the topic prefix plus the claim, with bare stems, identifiers and the English term an agent would search for.
- Dates: explicit evidence first; otherwise a marked estimate with the `date-estimated` tag (D-215); two items of one
  day need a time in `date:`.
- A decision that fully reverses an earlier one says so in the row (`supersedes D-012`, `D-012'yi geçersiz kılar`);
  a partial change uses different wording, so it does not hide the whole earlier row.
- Full tier: packages with curator ≠ checker, a consolidation pass, a sample recomputed by you from the raw data.

Gate: `hlm migrate lint --spec …` reports 0 errors, and the checkers' findings are applied.

**F4 Gates and review** (PLAYBOOK §10–§14).
- Secret gate, all three: `gitleaks dir <curated> --redact --no-banner` with 0 findings; the importer's dry run with
  `skipped 0`; a manual grep for credential-shaped assignments and plain hex tokens. A hit goes to a cleanup list and
  to the owner if a value needs rotating.
- Plan and rehearse: `hlm migrate plan --spec …` (one batch per file, oldest first), then
  `hlm migrate run --target local --spec …` (dry run), `hlm migrate run --target local --apply --spec …`, and a second
  run that comes back all `unchanged`.
- Truth set (`templates/TRUTHSET.md`): 10 questions (Light) or 15–20 (Full) with gold answers and verbatim quotes,
  including a superseded value and at least two negatives; across the freeze date for layered legacy memory. Seal it
  (`sha256sum`) and keep it private.
- Recall pre-check, with the local embed worker running: `hlm migrate recall --truthset <truthset.jsonl> --target local
  [--k 5] [--min-rate R] --spec …`; sharpen titles where the right file misses the top hits.
- Seal the reviewed tree: `hlm migrate seal --spec …` (add `--expect '<batch counts JSON>'` to bind the counts the
  review states); `hlm migrate seal --verify --spec …` checks it later. A content fix after sealing needs
  `--force`, a new seal and a note in `REVIEW.md`.
- `REVIEW.md` (`templates/REVIEW.md`), a title list and a card draft (present tense, ≤ 420 tokens).
- Full tier: a Codex consult (Astra low and Sol xhigh in parallel, at most two rounds, a triage file).

Gate: **the owner's OK on `REVIEW.md`, given in this chat.** Without it, stop.

**F5 Production import** (PLAYBOOK §15). Hand over to the operator for the slug, the grant and the dump (§6 below).
When the operator confirms the slug answers with its skeleton card and 0 hits:
1. Per batch, oldest first, in a shell without the local stack's exports:
   `HLM_MIGRATE_ALLOW_PROD=1 hlm migrate run --target prod --batch <B> --apply --spec …`. The tool classifies the
   batch once, refuses unless it is new/missing only, re-reads the open keys and writes exactly that checked plan; it
   stops on any `failed`, `rejected`, `changed`, `closed` or `skipped`, and a production run needs a seal that
   verifies. `--resume` finishes a batch an interrupted apply partly wrote.
2. `hlm migrate verify --target prod --spec …`: every batch must come back `unchanged`.
3. Close with `memory_call_the_day`: notes with counts and pointers; `card_update` with `expected_version_id` = the
   skeleton card's version (the number in `memory_query`'s `card.clue`; D-248).
4. Tell the owner the counts and the session-note clue, and that the operator's F6 steps are next.

**F6 Hand-over.** The operator runs the post-import steps (§6 below). Your remaining part: the new `CLAUDE.md` (from
F2, if the owner approved it), archiving the old auto-memory files and reducing `MEMORY.md` to a pointer, and freezing
the old notebook as a read-only archive.

## 5. Source adapters in one line each (PLAYBOOK §5)

- **Auto-memory:** curate into the markdown layout; `MEMORY.md` is usually an index, not memory.
- **Serena:** fine-grained and often dated; a frozen store is layered legacy memory.
- **`CLAUDE.md`, `AGENTS.md`, handover prompts:** rules, not memory; F2.
- **Repository documents:** pointers by default; a few living documents as doc chunks only on the owner's say.
- **Results and reports:** the human-written reports drive the episodes; numbers verbatim with their pointer and
  denominator.
- **NotebookLM:** notes dated from their titles (`SESSION` → episode, `LESSON` → lesson, `BUG` → episode plus a
  lesson); `STATUS` notes never become facts; sources that the repository already holds become pointers.
- **Git history:** eras and date evidence; commit uncommitted paths before pinning `file:line`.
- **Earlier projects:** spot-check their distilled lessons and add the missing ones as project-scoped lessons; a
  lesson seen in two or more projects goes under "Promotion candidates" for the owner.
- **Layered legacy memory:** originals before the freeze, the newer store's notes after it, bundles only as a
  cross-check; record each item's layer.

## 6. Talking to the operator, and the hand-off points

Questions go into `docs/private/migration/<slug>/QUESTIONS-FOR-OPERATOR.md`. The template lists the defaults already
decided (who creates the slug, the dump, who runs the batches, drains, format, lessons, the card, deep documents, the
local API, the item count, the mapping), so ask only what is open, with the project facts the operator needs. The
operator answers in `ANSWERS-FROM-OPERATOR.md`, tagging each answer **[code]**, **[tested]** or **[rec]**. A tool bug
reported with a reproduction gets fixed fastest (D-249 started that way). The owner carries the files between chats.

The sequence:
1. **[owner]** OK on `REVIEW.md` in this chat.
2. **[operator]** creates the project, grants the importing device `write`, takes the pre-import dump, and confirms
   the slug over MCP.
3. **This chat** runs the batches, the verify run, and the closing `call_the_day` with the card.
4. **[operator]** checks the queues; runs `hlm links explicit --dry-run` for the decision-row reversals.
5. **[owner]** OK on the links; **[operator]** applies them.
6. **[operator]** runs the blind check (≥ 0.80 correct, 0 superseded values stated as current, every negative
   abstains; a failure is a finding, not a retry), reviews the librarian's proposals, copies the pre-import dump to
   the non-rotated folder, and adds the `capture.toml` mapping.
7. **[owner and operator]** AUDIT sign-off (`templates/AUDIT.md`).

After the mapping, new chats in the project start in HLMemo mode with the brief; you become an ordinary writer under
the `hlmemo` skill.

## 7. When something goes wrong

- **A refusal** (a permission prompt, an auto-mode block, `E_FORBIDDEN*`, `E_AUTH`): stop, tell the owner what you
  tried and why. A refused production write is the system working.
- **A hard stop** in `hlm migrate run` (exit 2): read the run report under `runs/` in the private directory. An
  unexpected `changed` or `closed` means the tree or the server is not what the review assumed: find out why before
  any retry.
- **A seal mismatch** (exit 1 on `seal --verify`, exit 65 on a production run): the tree changed after the review.
  Either restore it or re-review and re-seal with `--force`, and note it in `REVIEW.md`.
- **A secret found late:** stop the import; tell the owner; the erase path is the pre-import dump (PLAYBOOK §17).
- **A gap in the kit:** write it into `QUESTIONS-FOR-OPERATOR.md` with a reproduction; the PLAYBOOK's pitfalls table
  (§18) lists the known ones and their fixes.
