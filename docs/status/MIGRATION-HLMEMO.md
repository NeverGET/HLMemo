# HLMemo self-migration into prod project `hlmemo` (W-A, D-130 → D-131/D-132)

Date: 2026-09-25/26 (UTC 22:0x–22:2x). Target: https://mcp.hlmemo.com (R3, 805f4cd), project `hlmemo`
(id 10), device 21 (`cemals-mb-pro-3`, `hlmemo:write`). Tool: the W1.5 `hlm import` CLI from the
owner's Mac (`hlm.toml` → prod). This is the REAL data on which the D-130 Production-Ready gate is
measured. `hlmemo-e2e` (the TEST project) was not modified apart from the isolation policy below.

## Who did what
- Subagent: source selection, offline parse, server dry-run, exclusion check, gitleaks, the plan
  (D-131); after the writes, the read-only verification and this record.
- Main session, under the owner's explicit /permissions grant: the two prod writes (the subagent's
  auto-mode permission check refused them twice; it was not routed around):
  1. `hlm_ops.sh project policy set hlmemo-e2e librarian_cross_project exclude` — read-back
     `{"librarian_cross_project":"exclude"}` (was `{}`); reversible; D-083-style test isolation.
  2. `apply.sh` (22:11:27–22:13:04Z): three `hlm import` runs with `--keep-missing`, all exit 0.

## Sources (208 files → 552 items)
| source | files | items | kind |
|---|---|---|---|
| docs/decisions/ (DECISIONS, roadmap, gates, Phase-0 docs) | 7 | 199 (131 decision rows, up to D-131) | fact |
| docs/status/ (STATUS, BACKLOG, E2E/R3 reports, R3 rehearsal, e2e README) | 6 | 46 | fact |
| docs/research/ | 7 | 34 | doc_chunk |
| docs/consults/ | 166 | 192 | episode |
| docs/bakeoff: r1/r2/r3/sol-5.6-vs-6 RESULT.md, SCOREBOARD.md, NEMOTRON-SCORING.md | 6 | 12 | doc_chunk |
| docs/USAGE.md, README.md, deploy/RUNBOOK.md, HARDWARE.md | 4 | 28 | doc_chunk |
| CLAUDE.md (`hlm import context`) | 1 | 1 | fact |
| Claude auto-memory (`~/.claude/projects/-Users-cemalkurt-Projects-HLMemo/memory/*.md`) | 11 | 40 (34 lessons, 6 facts) | lesson/fact |
| serena memories | — | 0 (no `.serena/memories` in the repo) | — |

Split: 205 whole-file items and 347 section items (22 files sectioned). 34 lessons, one per rule,
from 5 auto-memory feedback files. 131 items carry a decision-row `valid_from`; the rest use the
import time. The bakeoff `score.json` files were not imported (the markdown importer reads .md
only). Result by kind in `hlmemo` after the import: 253 fact, 192 episode, 74 doc_chunk, 34 lesson,
1 project_card = **554 items (2 before + 552)**.

Dry run vs apply: the dry run planned 551. The one extra item is `DECISIONS.md#D-131`, committed
(fef6f19, 22:05:59Z) between the dry run and the apply. No other item's content hash changed
(per-key sha256 comparison of the dry-run and apply reports); D-132's note that STATUS also gained
a section is not borne out by the reports.

Import report: markdown 511 new, context 1 new, automemory 40 new; 0 changed, 0 unchanged, 0 closed,
0 rejected, 0 skipped, 0 remapped, 0 failed; ~356k tokens (o200k).

## Exclusions and secret gate
- Excluded by construction (explicit paths, not repo-wide walks): docs/private/** (sealed hold-out),
  deploy/.local/**, .env*, *.key, tests/, src/, eval/results/*.json, bench raw outputs
  (`raw/`, `out-*`), cassettes. A path check over the 208 keys found none of these.
- All 197 repo files are git-tracked (none ignored or untracked).
- gitleaks (`gitleaks dir --redact`) over a staged copy of exactly the 208 files: **no leaks**;
  re-run over the as-imported DECISIONS.md (with D-131): **no leaks**. A long-token/IP scan found only
  session UUIDs, test names and documented example/loopback addresses. The auto-memory holds pointers
  only (key-file locations; the IPs of the owner's 4 other VPSs), no credential values.
- The 2 pre-existing `hlmemo` items (skeleton card v22, the 2026-09-23 state fact) have no source,
  so the import could not select them: `hlm export` before vs after shows both byte-identical.

## Verification (read-only)
- `hlm export --project hlmemo`: 2 → **554** items.
- memory.query probes (budget 1500), after the embeddings drained (indexing_pending=false),
  expected source rank in the top 5 (clue ids only):
  | probe | expected source | rank | top hit |
  |---|---|---|---|
  | P1 research-librarian PR gate | DECISIONS.md (D-130) | 1 | v774.0 |
  | P2 R3 deploy snapshot/downtime | R3-PROD-DEPLOY.md | 2 | v887.0 |
  | P3 parallel agents own DB | auto-memory parallel-agent-footguns (lesson) | 1 | v939.0 |
  | P4 Turkish retrieval flood | E2E-PROD-REPORT.md | 2 | v879.0 |
  | P5 changed SSH host key alarm | deploy/RUNBOOK.md | 1 | v415.0 |
  During the embedding backlog, P3 missed the top 5 (lexical only); all 5 hit once it drained.
- Embeddings: 552 import jobs drained by ~22:19Z (about 6 min).
- Librarian (observer, concurrency 3): about 15 jobs/min at about $0.0013/job, 0 failed, breaker
  closed. At 22:19:54Z: 511 librarian_write done in total, 415 queued, 3 running.
  `librarian audit --project hlmemo` at 22:19Z: 24 proposals (contradiction), all open, **0 applied**,
  0 batches decided; every proposal's subjects lie in `hlmemo` only, so the e2e isolation holds.
  At this rate the queue drains around 22:48Z for roughly $0.55 more.

## Spend
Today (UTC day 2026-09-25) before the import: $0.0014. At 22:19:54Z: **$0.165** (hour $0.163).
Caps: hour $1, day $2, month $10. Projected total for the import is about $0.7, within the day cap.
The hour cap may pause the librarian briefly (reservations); that is expected, and queued jobs wait.

## Artefacts
Scratchpad (not committed): the dry-run, apply and offline JSON reports, the staged file set,
`export-before/` and `export-after/`, the probe outputs and the audit snapshot.
