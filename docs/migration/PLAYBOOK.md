# Migrating a project's legacy memory into HLMemo: the playbook

Status: v1.1, 2026-10-07 (v1.1 adds the second migration's findings: the marker table, the round-trip, containment
and personal-data checks, resumed stores, team projects, a reviewer-agnostic consult). It replaces
`docs/migration/TEMPLATE.md` (v1, 2026-09-30), which is now a pointer here.
Grounded in the first migrations (D-131/D-132, D-215/D-216) and in the first migration run by a project's own chat
under the hybrid model (D-246; D-248..D-255). Product content only: per-project material stays in the gitignored
`docs/private/migration/<slug>/` (directories 0700, files 0600; check with `git check-ignore -v`).

**Who reads this.** The project chat that migrates its own memory (the `/hlm-migrate` skill follows this playbook),
the library operator who opens and closes the production side (`/hlm-library`), and the owner who decides the gates.
The writer rules R1–R21 of `docs/protocol/HLMEMO-PROTOCOL.md` apply throughout; this playbook adds what a migration
needs on top of them.

**The kit.** Templates are in `docs/migration/templates/`. The tools (K1) are driven by one spec file,
`docs/private/migration/<slug>/migration.toml` (template: `templates/migration.toml`):

| tool | what it does |
|---|---|
| `tools/migrate/local_stack.sh up\|env\|status\|down <slug>` | a scratch database, the API on loopback and a scratch device with `write` on the slug; no prod access, no LLM calls |
| `hlm migrate plan --spec migration.toml` | parses the curated set with the real importers and prints the batch table (one batch per file, oldest first) |
| `hlm migrate lint --spec …` | the static rules of the curation spec plus the real importer parse (kinds, dates, skipped files, `describes` pointers) |
| `hlm migrate seal [--verify] [--expect JSON] [--force] --spec …` | per-file sha256 of exactly the files the importer reads, a tree digest and the per-batch counts (`seal.json` in the private dir); `--verify` compares the tree against the seal; an existing seal is replaced only with `--force` |
| `hlm migrate run --target local\|prod [--batch B] [--apply] [--resume] --spec …` | a dry run by default; `--apply` classifies the batch once, checks it is new/missing only, re-reads the open keys and writes exactly that checked plan (a key that appeared in between stops the run before any write); `--resume` finishes a partly written batch; reports go to `runs/` in the private dir |
| `hlm migrate verify --target prod --spec …` | read-only: every batch must come back `unchanged` |
| `hlm migrate recall --truthset <jsonl> --target local [--k 5] [--min-rate R] --spec …` | a cheap pre-check: does the right source file reach the top hits for each sealed question; `--min-rate` makes it a gate |
| `hlm migrate roundtrip --spec … --target local` | parses the final set, exports what the local stack stored and compares every body verbatim (§11) |
| `hlm migrate containment --export DIR --originals PATH… [--shingle 8] [--json]` | how much of an exported store (a NotebookLM export) the originals already contain, by 8-word shingles (§5.6, §5.9) |
| `hlm migrate markers [--format md]` | prints the supersession marker table of §9, generated from the code |
| `hlm migrate nlm-check --export DIR [--originals PATH…]` | flags NotebookLM notes that were cut when they were saved (§5.6) |
| `hlm migrate card --file CARD-DRAFT.md` | counts the card draft in the server's tokenizer (o200k) and fails above 420 (§14) |
| `tools/migrate/scan.py --spec … [--known-values FILE]` | the credential and personal-data scan with masked output, plus a compare against known leaked values (§10) |
| `tools/migrate/blindcheck/` | extract → ask → packets → graders → score: the blind check of §13 (`ask` uses an hlm-only MCP configuration and fails loudly when nothing was asked) |
| `tools/migrate/withdraw.sh <slug> <ids-file> <expect> --reason '<why>' [--owner NAME] [--status open|accepted_pending]` | operator only: withdraws reviewed librarian proposals in one event (§16) |

Exit codes: 0 ok; 1 lint errors, seal mismatch or recall below `--min-rate`; 2 a hard stop; 64 a usage or spec
error; 65 refused before anything was sent. The spec's optional `estimated_marker` (default "Date estimated") is the
line `lint` requires in an item tagged `date-estimated`. Diagnostics never print a value that matches a secret rule.

`local_stack.sh env` also exports `HLM_PROJECT`, and `hlm query --project <slug>` queries a slug directly;
`local_stack.sh up <slug> --embed` also starts the embed worker, which the recall pre-check needs (§11).

A prod write needs `HLM_MIGRATE_ALLOW_PROD=1` and a seal that verifies, so it cannot happen by accident or from a
tree that changed after the owner's review. The read-only `hlm migrate verify --target prod` runs without the flag.

## 1. Principles

- **Learning halfway is worse than not knowing it** (owner, 2026-09-30). Old memory is not copied in as it is: every
  item keeps its provenance, newer facts replace older ones without deleting anything, uncertainty is made explicit,
  and nothing is trusted until it has been curated, scanned, rehearsed, reviewed by the owner and blind-checked. When
  a step cannot be done honestly, the item is dropped or flagged, not guessed.
- **Facts hold only the present; history lives in dated episodes and decision rows.** A fact that was true once and
  is not any more is the one way a curated import produces a stale answer stated as current (D-184). If a threshold
  changed three times, import one current fact, and record the earlier values as dated episodes or decision rows.
  The blind-check bar "no superseded value stated as current" then holds by construction (D-251).
- **Memory is an index into the repository, not a copy of it.** Items point to `file:line` and to result paths; the
  deep material stays in the repo. Fewer, sharper items rank better than many loose ones, because noise takes the
  slots of real hits within the token budget (R5).
- **Memory holds knowledge, not instructions.** Imperatives, handover prompts and standing authority ("you may
  deploy", "always run X") do not become items: a later chat would read them as permission. Rules the project still
  wants live in its `CLAUDE.md`; memory records the reason and the evidence.
- **The producer is not the checker.** Whoever curates a package does not check it; whoever prepared the import does
  not run the blind check. Every gate that matters has a second pair of eyes and, where possible, a machine check.
- **Workers do not print secrets or personal data.** Reports name the file, the line and the rule id, never the value.

## 2. Slug, scope and roles

**Slug.** Lowercase kebab-case, the product or repository name without a machine or owner prefix. One slug per
independently resumable subject. Split a source into two slugs when its parts have different readers, secrets or
lifetimes; merge sources that describe one product (its auto-memory, serena and `CLAUDE.md` share a slug). A
wrongly scoped project is expensive to undo (§17), so the owner confirms the slug in F0. Earlier attempts at the same
product (prior projects, §5.8) usually belong in the same slug as distilled lessons, not in slugs of their own.

**Roles and gates (D-246).** A project chat may migrate its own memory into its own slug. The library operator (the
orchestrator, in sessions the owner starts) opens and closes the production side. The owner decides every one-way door.

| step | who | when | why |
|---|---|---|---|
| tier, slug, scope, language, sources in or out | owner, asked by the chat | F0 | these decide everything after them |
| review package (`REVIEW.md`) | the chat writes, the owner approves | end of F4 | no prod write without the owner's OK on what will be written |
| project, `write` grant for the importing device | operator (`hlm_ops.sh project create`, `device grant`) | F5, right after the OK | an early grant would let any chat on that machine write into the slug before review |
| pre-import database dump | operator (`backup.sh --pre-upgrade <hex>`) | F5, right before the first batch | the only way to erase a leaked secret afterwards (§17) |
| copy of that dump to `/var/backups/hlmemo/migration/` | operator | right after the import | deploys prune `pre-upgrade/` to the newest 5 dumps (D-255) |
| prod batches | the project chat, under the owner's OK given in that chat | F5 | the chat knows its curated set; an auto-mode refusal of a prod write is correct and is not routed around |
| card | the project chat, in its closing `call_the_day` | end of F5 | `expected_version_id` = the skeleton card's version (D-248) |
| queue check, decision-row links (dry run, owner OK, apply), blind check, librarian proposals, `capture.toml` mapping | operator | F6 | they need operator tools (R20) and an agent that did not prepare the import |
| AUDIT sign-off | owner and operator | end of F6 | closes the migration; a `D-` entry records it |

The project chat and the operator talk through two files in the private directory, `QUESTIONS-FOR-OPERATOR.md` and
`ANSWERS-FROM-OPERATOR.md` (template: `templates/QUESTIONS-FOR-OPERATOR.md`, which lists the defaults already decided,
so only open points are asked). Answers are tagged **[code]** (verified against the code, with `file:line`),
**[tested]** (run on a local stack) or **[rec]** (a recommendation); that turns an ambiguous procedure into verified
answers and makes a tool bug reproducible (D-249 came from such a report).

**Team projects.** The kit assumes one owner on one machine. A project written by two people from two machines needs
four decisions in F0:
- **Device and grant.** The second writer's machine gets its own device (`hlm_ops.sh device mint`) and a `write` grant
  on the slug. Devices are not shared, so one can be revoked alone.
- **Mapping and hooks.** `~/.config/hlm/capture.toml` is per machine: the second machine needs its own `[projects]`
  entry and its own SessionStart hook install (on Windows, with Windows paths to the venv python and the folder).
- **Their local memory.** The second writer's auto-memory lives on their machine: decide whether it is a source of
  this migration (they export it) or not (say so in `REVIEW.md`).
- **Closing sessions.** Both write under the protocol, and each chat closes its own sessions with `call_the_day`.

## 3. Two tiers and how to choose

| | Light | Full |
|---|---|---|
| typical sources | Claude auto-memory, serena, `CLAUDE.md`; a few dozen files | large doc/result corpora, NotebookLM, a long research history, earlier attempts at the same product, layered legacy memory (§5.9) |
| expected items | up to about 100 | hundreds |
| inventory | the chat reads the sources itself | parallel read-only inventory agents, one record per document (`templates/INVENTORY.md`) |
| curation | one curator pass, a second agent checks a sample | packages with curator ≠ checker, a consolidation pass, a sample recomputed by the main thread (§6) |
| seal and manifests | `hlm migrate seal` | the same, plus per-package manifests |
| consult | optional, one independent reviewer | two independent reviewers, at most 2 rounds, a triage file (§14) |
| truth set | 10 questions | 15–20 questions, both languages if the project writes in two |

**Planning the Full tier.** One measured run (a project of about 500 items, layered legacy memory, 2026-10-07): about
5 hours of wall clock from the first pull to the AUDIT, about 25 subagents and about 9 million subagent tokens
(inventory about 1.6 M, curators about 3.8 M including fix rounds, checkers about 2.6 M, drafting and review agents
about 0.6 M), and about one checker fix per six items. It is a single data point, not a budget.

**Choosing.** Start from the inventory: count the files, estimate the items (about 2.4 items per memory file in the
first migrations), and note which sources exist. Choose Full when any of these holds: more than about 100 items, a
NotebookLM notebook, research results that the items must point to, earlier projects whose lessons carry over, or
**layered legacy memory** (the same knowledge already migrated once, §5.9). Otherwise choose Light. The owner
confirms the tier in F0. Moving from Light to Full mid-way is cheap (add packages and a consult); the reverse is not
needed.

## 4. Phases F0–F6

| phase | Light | Full | artifacts | gate |
|---|---|---|---|---|
| **F0 Inventory** | read every source, list secret hits by rule id | parallel inventory agents; git timeline; secret pre-scan; NotebookLM export | `PLAN.md`, `inventory/`, exports | the owner confirms tier, slug and scope |
| **F1 Architecture** | a short note in `PLAN.md` | `ARCHITECTURE.md`: layers, lines/topics, kind mapping, dates, language, package plan; questions to the operator | answers file | owner OK on the architecture |
| **F2 Rules** | skip unless the project has many agent rules | rule catalog of `CLAUDE.md`, handover prompts and feedback memories → principles with reasons in a new `CLAUDE.md` | rule catalog, draft `CLAUDE.md` | the owner reads it |
| **F3 Curation** | one pass + a checked sample | spec pilot on one package, then all packages; checkers; consolidation | `curated/`, `final/`, manifests, logs | `hlm migrate lint` 0 errors; checker findings applied |
| **F4 Gates and review** | secret and personal-data gate, local rehearsal with `roundtrip`, truth set, `REVIEW.md` | the same, plus recall pre-check, title review, card draft, seal, consult | `REVIEW.md`, `truthset.jsonl` + sha256, seal | the owner's OK on `REVIEW.md`, recorded as a `D-` entry |
| **F5 Prod import** | operator opens; chat runs batches; card | the same | run manifests | dry run before every apply; hard stops (§15) |
| **F6 Hand-over** | queue check, blind check, mapping, AUDIT | the same, plus decision-row links and the librarian review | `AUDIT.md`, blind-check result | the owner's AUDIT sign-off |

F2 exists because agent rules written for older models are migration input too: their reasons become memory, and
their wording is rewritten for the current model (a rule of the 2026-10-04 calm rewrite).

## 5. Source adapters

Each source gets a line in the inventory: path, file count, bytes, date span, type, the importer that fits, and secret
hits by file and rule id. Repository documents that are not agent memory are out of scope unless the owner pulls them in.

### 5.1 Claude auto-memory (`~/.claude/projects/<dir>/memory`)
Frontmatter `type` maps to kinds when imported with `hlm import automemory` (`feedback`/`lesson` → lesson; the rest →
fact). In practice the files are curated into the markdown layout of §7, because `MEMORY.md` is usually an index
that duplicates the topic files and typed files mix present state with history. After the migration, archive the
files out of the live directory and reduce `MEMORY.md` to a pointer; otherwise the project has two truths.

### 5.2 Serena (`.serena/memories`), including frozen stores
Serena files are fine-grained and often carry explicit dates. A store marked frozen (a `_FROZEN.md` or similar) was
bundled into another system at its freeze date: treat it with §5.9. An app sub-folder with its own store is part of
the same slug unless it has a different audience.

### 5.3 `CLAUDE.md`, `AGENTS.md`, handover prompts
These are rules and instructions, not memory. Mine them into a rule catalog (rule, category, keep / reword / merge /
retire / move-to-memory, reason) and rewrite the project's `CLAUDE.md` in F2. What survives as memory is the reason
behind a rule and its evidence, never the imperative itself. Handover prompts are dated documents: their present tense
("next step", "you may") is history the day after they were written.

### 5.4 Repository documents
The default is pointers: items cite `docs/…:line`, and `describes` turns them into code references when the curated
set is parsed with the repo path in `migration.toml`. A small set of living documents (current specs, a benchmark) may
be imported as doc chunks from the real repository with `--base` set to the repo root, so later doc syncs stay
idempotent; they get `valid_from` = import day, so only documents that describe the present qualify. Importing a whole
corpus mechanically is supported but measured worse (D-054, D-080: recall below the curated layer, one long document
flooding the top hits), and its undated chunks look newer than every dated item.

### 5.5 Results and reports
Reduce machine output to the human-written reports (an agent scan), rank them (core / supporting / low) and let the
core ones drive the episode list. Numbers are copied verbatim with their pointer; a JSON or table value beats a prose
summary of it, and the checker verifies the denominator ("N of M").

### 5.6 NotebookLM
Export read-only with the `nlm` CLI or the NotebookLM MCP tools (the owner authenticates; nothing is written to the
notebook): notebook facts, the source list, each source's raw content (`nlm source content <id>`), and the notes with
their bodies, plus a manifest (sha256, bytes, export time, tool version). Do not use AI-generated descriptions as
content. Tested (2026-10-07): `nlm note list <notebook> --json` returns the full note bodies, so no other export path
is needed. `nlm source content` returns raw text but reflows markdown, so a line-by-line comparison with the
originals under-reports the overlap; `hlm migrate containment` compares by 8-word shingles instead. Then:
- **notes** carry no timestamps: take the date from the note title (`SESSION <date>`) and say so in the review;
  `SESSION` → episode, `LESSON` → lesson, `BUG` → a dated episode (plus a lesson if a rule came out of it);
- **`STATUS` notes never become facts.** Their present tense is stale; the current state comes from the repo and is
  written as a fact verified there. A `STATUS` note may survive as a dated episode ("state on <date>: …") when it
  records something found nowhere else;
- **sources** are usually pasted repository text: compare them with the repo (shingle overlap). A source that the repo
  already holds becomes a pointer, not an item; only content found nowhere else is curated;
- **notes may already be cut.** NotebookLM's write path can drop everything after a note's first `<` (`<tag>`,
  `Type<T>`) at the moment the note is saved, with no warning; reading it back returns the cut text faithfully. So
  every project that used NotebookLM as its memory probably has cut notes, unnoticed for months. Run
  `hlm migrate nlm-check --export <dir> [--originals <paths…>]` on the export: it flags notes that end mid-sentence
  or right before a `<`, and, with originals, notes shorter than what the originals hold. Recover a cut note from the
  stores written alongside it (serena or auto-memory files, git history, session files) and mark it as recovered.
  Check each NotebookLM-first project this way before its migration starts. `hlm migrate roundtrip` then shows that
  HLMemo keeps every body verbatim;
- set a **freeze date**: no new notes after the export, and the notebook stays as a read-only archive.

### 5.7 Git history
Commits give a timeline of eras and date evidence, with two caveats: a batch commit dates earlier work too late (take
the earliest explicit date in the describing text), and uncommitted paths have no commit at all (commit them before
the curated set pins `file:line` pointers). Commit trailers can show which model wrote what, the evidence for a
lesson's era (D-236).

### 5.8 Earlier projects on the same problem
When the project is a later attempt at a problem that earlier projects tried, their lessons are part of its memory.
If the repository already holds distilled reports of them, spot-check them (about five lessons per earlier project,
each found and correctly worded) instead of importing the old stores, and add what is missing as lessons that name
their source project. They stay project-scoped lessons in this slug; a lesson that held in two or more projects is
listed under "Promotion candidates" for `hlm-global`, which the owner decides (D-222).

### 5.9 Layered legacy memory
A project whose memory was already migrated once (for example serena → NotebookLM, then → HLMemo) holds the same
knowledge in two shapes. The older store is fine-grained, often explicitly dated, and frozen at the bundling date. The
newer store bundled it, usually as undated concatenations, and everything written after the freeze exists only there.
- **Map the layers and the freeze date** in the inventory: which store, which period, which shape.
- **Default strategy.** Before the freeze, take facts, lessons and episodes from the fine-grained originals: better
  dates, one claim per file. After the freeze, take them from the newer store's notes. Use the bundles only as a
  cross-check and as pointers; do not import them. Importing both doubles every fact, and the bundles' undated text
  would get the import date and look newer than its own originals.
- **Reconcile the counts**: the originals against what the bundling claims it moved. Look for details the bundling
  lost or merged, recover them from the originals and mark them as recovered.
- **Record the layer** each item came from, in the inventory and in `REVIEW.md`, and put truth-set questions across the
  freeze date (one answered by a pre-freeze original, one only by a post-freeze note, one where a post-freeze note
  replaced a pre-freeze fact).

**A store resumed after its freeze.** The older store may have been frozen, bundled, and then written to again (new
files, or old files edited in place under a "current state" header), while the newer store kept going too. The
files on disk are then no longer the originals.
- Read the originals as they were at the freeze: `git show <freeze-commit>:<file>`.
- Treat the post-resume edits and files as their own layer, dated by their commits (§5.7), and reconcile them with
  the newer store's notes of the same period.
- Re-check a freeze premise of an earlier plan against the pulled repository (§19 step 1): a resume may have happened
  since the plan was written.

Layered legacy memory implies the Full tier.

## 6. Curation

**The curation spec.** Write it before curating, from `templates/CURATION-SPEC.md`: the kind/directory layout, a
closed tag list, title rules, date rules, size limits, the line or topic prefixes, and the language. Pilot it on one
package and fix it before fanning out; a spec fixed after nine packages costs nine re-curations.

**Per file or section:** keep, drop (duplicate, stale, trivial, secret, personal data, index-only) or fix. Facts keep
their wording; a curator may drop, mark (`CURATOR NOTE`), verify against the live repo (`VERIFIED <date>: …`), or fix
structure. Present-state claims are checked against the repo; production and network facts stay marked unverified. A
statement that cannot be verified stays as written and is flagged.

**Packages (Full).** Split by topic or period so each package fits one agent. Each package has a curator and an
independent checker with a brief that names the error classes: an N-of-M count without its denominator, prose that
contradicts the table or JSON it summarises, stale present tense, a pointer past the end of its file, a date taken
from a verification stamp rather than the content, an imperative stored as memory. The main thread recomputes a sample
from the raw data. A **consolidation pass** follows: duplicate groups merged (the superset kept), decision rows merged,
pointers repaired, lessons given one owner (a domain lesson goes to its earliest evidence; a process lesson seen in
three or more topics goes to a process line), all logged.

**Personal data.** Mail addresses, named people and their devices, account and key identifiers, private network
addresses: drop or generalise them; name a person by role. Prod memory is private, but it is not the place for data
that serves no later chat.

## 7. Item formats (tested 2026-10-06)

The generic `hlm import markdown` path decides the kind from the directory (`importers/markdown.py`): `status/` → fact,
`sessions/` → episode, `lessons/` → lesson, a `decisions/` log → one fact per row. Hand-built `hlm_export` files are not
used: untested for hand-made files, and their raw `supersedes` links skip the update guards (R13).

| kind | where | shape |
|---|---|---|
| fact | `status/<topic>/<name>.md`, one per file | frontmatter `title`, `date`, `tags`, `source_path`; body starts with the claim, then why and evidence |
| lesson | `lessons/<topic>/<name>.md`, one per file | frontmatter `title` (the rule, ≤ 120 chars), `date`, `tags: [<scope>@<ver>, active\|resolved(, historical)]`; body `## Mistake` (When…) / `## Fix` (Do…, Avoid…) / `## Context` (Evidence, Not verified for, Scope, Status, Era) |
| episode | `sessions/<topic>/<topic>-<YYYY-MM>.md`, a monthly log | **no frontmatter and no H1**; one `## YYYY-MM-DD <PREFIX> · <claim>` heading per episode; a source line inside each entry |
| decision | `decisions/DECISIONS.md` | **no preamble**; rows `D-NNN \| YYYY-MM-DD \| STATUS \| text`, at least two |

- **Grouped files start with their first entry.** Frontmatter or an H1 above it becomes an extra item, undated unless
  it carries `date:` (D-248).
- **A monthly log with one entry** is read as a single item titled by its file name (`topic 2026 05 · sessions/…`).
  Give such a file a frontmatter `title` and `date`, or merge the entry into a neighbouring month; `lint` warns.
- **File names are unique across directories** (`same_name` is reported), and a fact is not grouped: a dated heading
  always makes an episode, and the size split never cuts below 1000 characters.
- **Titles** carry the claim, prefixed with the topic or line (`PREFIX · claim`), because no tool filters by tag. The
  lexical and title indexes use `to_tsvector('simple')`, which does no stemming: put bare stems and identifiers in the
  title (inflected words match only through trigram and vector search), plus the English term where an agent would
  search in English. An optional one-line `EN:` summary helps a bilingual project.
- **Lessons hold lasting rules, no current values.** A version string or a setting in use goes into a fact the lesson
  points to; inside the lesson it is stale with the next change (test-drive 2026-10-07). One rule per file: two rule
  headings would split the file and share its tags.
- **Keep the frontmatter short**: it stays in the body of a generic import and is searchable noise.
- **Owner decisions are `OD-nn`, not `D-nn`.** The decision log's rows are `D-NNN`, and HLMemo's own decisions are
  `D-NNN` too; an owner decision written as "D-12" in an item reads as a project decision row. `lint` warns when an
  item mentions an `OD-` id, because owner decisions about the migration belong in `PLAN.md`, not in memory.
- **Sizes**: facts and lessons about 300–1500 characters, episodes up to about 3000, every entry under 8000 (no size
  split). `lint` warns above 1500 and 3000. The 80-character title aim counts the whole title, an episode heading's
  date and prefix included.
- **Scope tags** (`<stack>@<version>` on lessons) can be checked: list the allowed ones in `[tags].scopes` of
  `migration.toml`, and `lint` errors on any other. Side files next to the curated tree (notes, manifests) go into
  `[lint].exclude`; non-markdown files are skipped.

## 8. Dates

- Explicit evidence first: frontmatter `date`/`valid_from`, a decision row, a heading that starts with the date,
  `SESSION <date>`. Take the date of the content, not of a later verification stamp.
- Without evidence, an estimated date (the source file's last commit, else its mtime, the later one when edits are
  uncommitted), marked by a visible line and the tag `date-estimated` (D-215). An undated item would get the import
  time and look newer than everything dated, its own corrections included.
- Two items of one day need a time in `date:` (`2026-08-17T18:00:00`): inside a run the importer writes in key order, so
  only `valid_from` carries the order. A time in a heading is not read as a date; put it in frontmatter.
- Era (the model in use) comes from evidence, never from a guess (D-236). A model-behaviour lesson from an earlier era
  is tagged `resolved` + `historical`; `active` together with either is refused by the server (PV-5).

## 9. Supersession inside the import

With facts holding only the present (§1), the import needs little supersession. What remains is a decision that
reverses an earlier one. Write it in the row text, where `hlm links explicit` finds it, and write everything else so
that it does not trigger a link: **the links pass reacts to more words than "supersedes"**. The table lists every
marker. `hlm migrate markers --format md` prints it from the code (`core/explicit_supersession.py`, `MARKERS`), and a
test keeps this copy equal to it.

<!-- markers:begin -->
| language | relation | wording that triggers it | where it counts | the declaring item is |
|---|---|---|---|---|
| English | `merged_from` | merged … from / merged … out of | anywhere; target within 5 words | newer (it replaces the target) |
| English | `merged_into` | merged into | anywhere; target within 5 words | older (the target replaces it) |
| English | `superseded_by` | superseded by / superseded through | anywhere; target within 5 words | older (the target replaces it) |
| English | `supersedes` | supersedes / supersede / superseding | anywhere; target within 5 words | newer (it replaces the target) |
| English | `replaced_by` | replaced by / replaced with | anywhere; target within 5 words | older (the target replaces it) |
| English | `replaces` | replaces / replaced / replace / replacing | anywhere; target within 5 words | newer (it replaces the target) |
| English | `instead_of` | instead of / rather than / in place of | decision rows; D-id right after | newer (it replaces the target) |
| German | `merged_from` | zusammengeführt aus / von | anywhere; target within 5 words | newer (it replaces the target) |
| German | `superseded_by` | ersetzt / abgelöst / überholt durch / von | anywhere; target within 5 words | older (the target replaces it) |
| German | `supersedes` | ersetzt | anywhere; target within 5 words | newer (it replaces the target) |
| German | `instead_of` | anstelle von / anstatt / statt | decision rows; D-id right after | newer (it replaces the target) |
| Turkish | `merged_from` | birleştirildi / birleştirilmiştir / birleştirilerek | anywhere; target right before | newer (it replaces the target) |
| Turkish | `superseded_by` | tarafından geçersiz kılındı / kılınmıştır, tarafından değiştirildi / değiştirilmiştir | anywhere; target right before | older (the target replaces it) |
| Turkish | `supersedes` | geçersiz kılar / kılıyor / kıldı / kılmıştır | anywhere; target right before | newer (it replaces the target) |
| Turkish | `replaces` | yerini alır / aldı / almıştır / alıyor | anywhere; target right before | newer (it replaces the target) |
| Turkish | `instead_of` | yerine | decision rows; D-id right before | newer (it replaces the target) |

- "within 5 words": the D-id or file path follows the phrase after at most 5 filler words. "right after" / "right before": nothing but a case suffix or a list joiner in between ("X instead of Y (D-110)" does not link; "X instead of D-110" does; "D-110 ve D-111'i geçersiz kılar" links both).
- No link comes from a question, from a marker after a negation or a hypothetical word ("would", "if", "proposes", "eğer", "belki"…), from fenced or inline code, or from quoted text.
- A partial change needs a wording that is not a marker: narrows D-xxx / complements D-xxx / extends D-xxx; D-xxx'in … kısmını değiştirir / D-xxx'i daraltır / D-xxx'e ek olarak; ergänzt D-xxx / schränkt D-xxx ein.
<!-- markers:end -->

How a marker finds its target:
- **English and German markers** link the first D-id or document path that follows within 5 filler words in the same
  clause; a list joined by "," "/" "&" or "and" counts as one target list. The decision-row-only markers need the D-id
  right after them (at most one filler word): "instead of D-110" links, while "instead of cursor paging (D-110)" does
  not, because a parenthesised citation after other words is a reference, not the object of the marker.
- **Turkish markers** take the D-ids that end right before them, with an optional case suffix on each, and lists joined
  by "ve", "ile" or "/": "D-300/D-308'i geçersiz kılar", "D-350'yi ve D-351'i geçersiz kılar" (D-249).
- A marker does not count in a question, after a negation or a hypothetical ("would supersede", "not replacing"),
  inside code or quotes, or when used as a noun ("a supersedes link").
- A one-row decision item resolves only when its source anchor is its D-id (`DECISIONS.md#D-006`), which the importer
  gives every row (D-249).

Wording to use:
- a **full reversal** names the old row with a marker: "supersedes D-012", "D-012'yi geçersiz kılar";
- a **partial change** uses neutral words, so the whole earlier row is not marked as outdated: "narrows D-012",
  "complements D-012", "changes the threshold part of D-012", "D-012'nin eşik kısmını değiştirir";
- **in a decision row, "instead of D-xxx", "rather than D-xxx" and "D-xxx yerine" link** whether or not the row means to
  retire D-xxx: "use the local store instead of D-110" marks D-110 as partly outdated. When that is not the intent,
  write "the local store, not the option of D-110", or cite the D-id in parentheses after other words.
- Check the result before the import: `hlm migrate lint` previews the links the pass would propose, and the list must
  equal the reversals you meant (the review package states them).

The operator runs the links pass after the import (§16).

## 10. Secret and personal-data gate

Three checks on the exact file set, all required: `gitleaks dir <final> --redact --no-banner` with 0 findings; the
importer's own filter (a dry run reports `skipped 0`); and `tools/migrate/scan.py --spec …`, which replaces the
hand-written grep. It covers credential-shaped assignments (`*_KEY=`, `*_SECRET=`, `*_TOKEN=`, `PASSWORD=`, DSNs with
passwords), plain hex tokens in prose, and **personal data**: mail addresses, device identifiers such as 40-hex UDIDs,
private IP addresses, account and tax identifiers, local key paths. It prints the rule, `file:line` and a masked
value (`<HEX40>`, `<MAIL>`), never the value, and a `[scan]` allow-list in `migration.toml` takes documented false
positives. Gitleaks and the importer filter once missed a real assignment and once a plain hex token, and in the
second migration the real exposure was personal data, which neither looks for; they are a floor, not proof. When a
leaked value is known (a token found in the sources), `--known-values FILE` compares the final set against it by
code without printing it. A hit goes to a cleanup
list (redact, drop, or keep it outside HLMemo) and to the owner if the value needs rotating. Since D-254 the server
also refuses key-shaped secrets on writes (PV-1), but the import path relies on this gate first.

## 11. Local rehearsal and recall pre-check

`tools/migrate/local_stack.sh up <slug>`, then `eval "$(tools/migrate/local_stack.sh env <slug>)"` (neutral working
directory, local URL, scratch token). The repository root's `hlm.toml` names production and is read when the client
runs there, so the client runs from the neutral directory with an explicit `--project`. Then `hlm migrate run --target
local` (dry run), then `--apply`. The idempotence check is `hlm migrate verify --target local`: every batch comes back
`unchanged` (a second `run` stops on purpose, because `run` accepts only new or missing records). Then
`hlm migrate roundtrip --target local` exports what the stack stored and compares every body with the final set,
verbatim; it is an F4 gate and shows that nothing was cut or rewritten on the way in (angle brackets, backticks,
non-ASCII letters, long markdown). `hlm migrate recall` checks each sealed question against the top hits: a cheap
loop before the paid blind check, and the right time to sharpen titles. It needs the embed worker
(`local_stack.sh up <slug> --embed`); without it, retrieval is lexical and trigram only, and inflected Turkish
questions score lower than they will in production.
`hlm query --project <slug>` answers ad-hoc questions against the stack.

## 12. Batches, seal and manifests

`hlm migrate plan` buckets **per file**: every record of a file lands in the batch of the file's newest month, oldest
batch first. A file split across runs lets a later run treat its earlier records as missing and remap one of them onto
a similar new record, which turns a new item into a revision of an old one (`--keep-missing` stops closes, not the
remap; D-248). Drains between batches are optional while the librarian is an observer.

`hlm migrate seal` records a sha256 per file, a tree digest and the per-batch counts, which must equal the importer's
parse. The prod run refuses when the tree no longer matches the seal: what the owner approved is what gets written.
A content fix after sealing means a new seal and a note in `REVIEW.md` (and a new truth-set version if quotes moved).

## 13. Truth set and blind check

**Before F5**, write 10–20 questions (`templates/TRUTHSET.md`) with gold answers and verbatim quotes from the final
set: facts, a temporal question including at least one superseded value, a procedure, a lesson, and at least two
negatives that must abstain; both languages if the project writes in two; questions across the freeze date for
layered legacy memory. **Before sealing, verify each gold answer** against the items it cites and, for a claim about
behaviour, against the code: a gold that says "never", "only" or "there is no route" gets the same check as a fact
that says so, because a wrong gold turns a correct answer into a miss. Seal it (`sha256sum`), and keep the file
private. When a later content fix changes a quote, publish a new version with both hashes; gold answers do not change.

**After F5**, the operator runs `tools/migrate/blindcheck/`: each question is asked once through production
`memory.ask` by a relay that never sees the gold and runs with an hlm-only MCP configuration (no user settings, hooks
or other servers; `ask` exits non-zero when no question came back answered), the raw tool result is saved by code (no model retypes it, D-216),
and two blind graders score the packets; on a split the stricter grade counts. The bar: at least 0.80 correct on the
answerable questions, 0 superseded values stated as current, every negative abstains. A failure is a finding, not a
retry. When a miss traces to a gold shown wrong by the cited items or the code, the operator may re-grade that one
question: record both scores (raw and corrected), the evidence and the reason, and keep the raw score as the
headline; the gold is fixed in a new truth-set version.

## 14. Review package and consult

`REVIEW.md` (`templates/REVIEW.md`) gives the owner what is needed to decide: counts by kind and source (and by layer,
§5.9); dropped files and why; flagged uncertain or stale statements; conflicts and how they were resolved; date
coverage (explicit, estimated); the batch plan; the slug; the exact prod commands; the expected librarian cost
(about $0.0013–0.003 per item); the truth-set and seal hashes; the promotion candidates; the open owner questions. A
title review file (all titles, one per line) and a card draft (present tense, ≤ 420 tokens) come with it; check the
draft with `hlm migrate card --file CARD-DRAFT.md`, because the limit is in the server's tokenizer (o200k) and other
tokenizers count differently.

**Consult (Full).** Before the owner's OK, two independent reviewers check the package in parallel: another model, or
a fresh-context agent that did not curate (Codex models reviewed the first migrations, while they were available).
They get a written prompt, the threats to guard against (stale stated as current, secrets and personal data, wrong
scope, unintended links) and a severity rubric; at most two rounds; a triage file records each finding as accepted
or rejected with the reason. The owner decides residual risks and may skip the consult; `REVIEW.md` then says so.

## 15. Production import (F5)

1. The owner says OK on `REVIEW.md` in the project chat; a `D-` entry records it.
2. The operator creates the project, grants the importing device `write`, takes the pre-import dump, and confirms over
   MCP that the slug answers with its skeleton card and 0 hits. A deploy is not run while batches are running.
3. The chat runs each batch: `HLM_MIGRATE_ALLOW_PROD=1 hlm migrate run --target prod --batch <B> --apply --spec …`. The
   tool runs the same batch's dry run first and stops on anything other than `new` (or `missing` under keep-missing),
   and on any `failed`, `rejected`, `changed`, `closed` or `skipped` count.
4. `hlm migrate verify --target prod --spec …` (read-only, no prod flag needed) must report every batch `unchanged`.
5. The chat closes with `call_the_day`: notes with counts and pointers, and `card_update` with `expected_version_id` =
   the skeleton card's version (from `memory_query`'s `card.clue`).
6. The operator copies the pre-import dump to `/var/backups/hlmemo/migration/` and verifies its sha256.

## 16. After the import (F6)

1. **Queues.** The operator checks once that the embed queue is done (`indexing_pending: false` on a query) before the
   blind check; the librarian queue may still run.
2. **Decision-row links.** `hlm links explicit --project <slug> --dry-run` inside the API container; compare the edges
   with the local run and with the row texts; the owner approves; apply (one evented pass, reversible with `--revert`).
3. **Blind check** (§13).
4. **Librarian proposals.** When the queue is drained, export them (`ops librarian audit --project <slug> --json`), let
   two verifier agents read both subjects of every proposal (read-only drilldown) and classify them (no conflict, dated
   history, stale current claim, real contradiction, refines yes/no), check every claimed conflict by hand against the
   source, and check by code that no current-state fact is the older side of a pair judged as history. Real findings
   become writer corrections (`updates`); then, with the owner's OK, all proposals are withdrawn in one event
   (`tools/migrate/withdraw.sh`). Accepting a proposal is not the way to apply it: accepted proposals wait and are
   mass-applied by a later role promotion (D-244, D-245).
5. **Mapping.** The operator adds the project folder to `[projects]` in `~/.config/hlm/capture.toml` once the card is
   written and the blind check has passed; from then on, new chats there start in HLMemo mode with the brief.
6. **Old stores.** Auto-memory archived and reduced to a pointer; the old notebook frozen as an archive; the project's
   `CLAUDE.md` memory section reduced to one line naming the slug (the global `CLAUDE.md` and the injected digest
   carry the protocol). This is the chat's F6 work, and by default it happens in **a second, short session** that
   ends with its own `call_the_day`, because the import session already closed with the card. A chat may instead
   keep the import session open and close it only after F6; `PLAN.md` says which.
7. **AUDIT** (`templates/AUDIT.md`), signed by the owner and the operator; a `D-` entry records the scores. The public
   decision log carries counts and pass/fail without project content; the AUDIT itself stays private.

## 17. Rollback

Memory is bi-temporal and append-only; there is no "delete project". Retire in this order and stop at the first step
that is enough:
1. **Stop the exposure.** `device ungrant <device> <slug>` for every device that can read or write it, and
   `project policy set <slug> librarian_cross_project exclude`.
2. **Close the items.** Validity ends now; history stays queryable as-of. Run a corrective import of the same source
   type from an empty directory with `--confirm-close` (the mass-close guard needs it), once per importer type used.
3. **Correct single items.** Re-import the fixed curated file: one revision, the old version stays as history.
4. **Erase** a leaked secret or personal data. Not possible through the API, because history is kept by design: it
   needs an owner decision and an operator procedure on the server, either restoring the pre-import dump (§15 step 6)
   or a manual purge plus re-embedding.
Afterwards, record what was retired and why in a `D-` entry and fix the step that let it through.

## 18. Pitfalls

| pitfall | what happens | what to do |
|---|---|---|
| importing a stale `STATUS`/handover note as a fact | stale stated as current | facts hold only the present, verified against the repo; the note becomes a dated episode or is dropped |
| a grouped file with frontmatter or an H1 | an extra, possibly undated item | start grouped files with their first entry (D-248) |
| a file split across batches | a new record silently becomes a revision of an old one | batches per file (`hlm migrate plan`) |
| undated items | they look newest and outrank their own corrections | explicit dates, else marked estimates (D-215) |
| two items on one day without a time | wrong order | a time in `date:` |
| one-row decision items gave no links | reversed decisions stay current-looking | fixed in D-249; write the reversal marker in the row, run the links pass |
| a partial change written like a reversal | the whole earlier decision is hidden | partial wording (§9) |
| the bundles and the originals both imported (layered legacy memory) | every fact doubled; undated bundle text looks newest | §5.9: originals before the freeze, notes after, bundles as cross-check only |
| a lesson with a current value | the lesson goes stale with the next change | values in facts; the lesson points to them |
| imperatives or standing authority stored as memory | a later chat reads them as permission | rules go to `CLAUDE.md`; memory keeps reasons and evidence |
| scanners miss a plain token or an assignment | a secret in append-only history | the manual grep and the known-value compare (§10) |
| the client reads the repo's prod `hlm.toml` | a local rehearsal writes to prod | neutral working directory, explicit URL and `--project` (§11) |
| card update without `expected_version_id` | refused: every slug has a skeleton card | use the skeleton's version (D-248) |
| accepting librarian proposals | they wait and are mass-applied later | verify, correct by `updates`, withdraw (§16) |
| the pre-import dump in `pre-upgrade/` | pruned after a few deploys | copy it to `migration/` (D-255) |
| a title that ends with the import path | an agent looks for a repo file that is not there | the repo path is `source_path` in the body; the importer fix is planned |
| `risk_check` returning `no_matching_evidence` | a relevant lesson was dropped by the judge | also query lessons for the component (R18) |
| `revise` on an item whose title states the old claim | the title stays stale | use `supersede` (R11) |
| the brief missing at session start | the server answered slower than the hook's budget | fixed in D-252 (6.5 s); fall back to `memory_query` |
| the `postgres-closed` gate failing on a mobile network | a false alarm: the carrier accepts every port | the server-side listener check decides (D-255) |
| NotebookLM notes cut at their first `<` when saved | months of silently missing text | `hlm migrate nlm-check` on the export; recover cut notes from the stores written alongside; check every NotebookLM-first project before its migration (§5.6) |
| a gold answer that is itself wrong | a correct answer graded as a miss | verify golds against the cited items and the code before sealing; re-grade with both scores recorded (§13) |
| owner decisions numbered like decision rows | "D-12" in an item reads as a project decision | `OD-nn` for owner decisions; `lint` warns (§7) |
| local recall without the embed worker | Turkish and inflected questions under-score | `local_stack.sh up <slug> --embed` (§11) |
| "instead of D-xxx" or "D-xxx yerine" in a decision row | the links pass marks D-xxx as partly outdated | the marker table and wording of §9; check `lint`'s link preview |
| a one-entry monthly episode file without frontmatter | an item titled by its file name | frontmatter `title`/`date`, or merge it; `lint` warns (§7) |
| an older store resumed after its freeze | the "originals" on disk were edited after the bundling | read them at the freeze commit; the resumed edits are their own layer (§5.9) |
| a plan prepared from an old checkout | wrong counts and premises | pull every repository first and re-validate the plan (§19) |
| the blind-check relay without the hlm server | no question is answered, yet the run looks finished | the hlm-only MCP configuration; `ask` fails loudly (§13) |
| personal data in the sources | credential scanners do not see it | `tools/migrate/scan.py` with its personal-data rules (§10) |

## 19. The first 30 minutes

1. **Fetch and pull every repository first**, then read its state: uncommitted paths (commit or list them), the
   remote, the slug rules (§2). A plan prepared earlier is re-validated against the pulled tree (source counts, freeze
   markers, the files it names) before anything else.
2. Ask the owner: tier, slug, scope, deep documents as pointers or doc chunks, language, which dated-note sources
   exist, the era boundary if there are model-behaviour lessons.
3. Create `docs/private/migration/<slug>/` (0700), confirm `git check-ignore -v`, copy the templates in, start `PLAN.md`
   (phases, gates, an owner-decision log, a "repo fixes made during migration" list) and `migration.toml`.
4. `tools/migrate/local_stack.sh up <slug>` and `env`: confirm the neutral directory, the loopback URL and `--project`.
5. Inventory: Light, read the sources; Full, start the inventory agents (docs, results, auto-memory + `CLAUDE.md` +
   serena, NotebookLM export, git timeline, rule catalog), each writing records and a summary and nothing else.
6. Secret pre-scan of the sources (gitleaks and the manual grep): file, line and rule id only.
7. For layered legacy memory, map the layers and the freeze date now (§5.9).
8. Write `QUESTIONS-FOR-OPERATOR.md` with only the open points; the template lists the decided defaults.

## 20. Artifact map

| artifact | where | public |
|---|---|---|
| plan, inventory, exports, curated and final sets, manifests, seal, `REVIEW.md`, truth set, answers files, `AUDIT.md` | `docs/private/migration/<slug>/` | no |
| blind-check run (questions, answers, packets, grades, result) | `docs/private/migration/blindcheck-<slug>/` | no |
| decision and scores without project content | `docs/decisions/DECISIONS.md` (`D-` entries) | yes |
| this playbook, the templates, the kit tools | `docs/migration/`, `tools/migrate/` | yes |
