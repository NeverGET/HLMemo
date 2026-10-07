---
name: hlm-library
description: "The HLMemo library operator's procedures: status and spend, the librarian-proposal review (verifier agents, operator checks, withdraw), decision-row links, card refresh, the doc→memory sync, a migration's post-import steps (queues, links, blind check, AUDIT, capture.toml mapping, dump copy), test-drive feedback triage, and releases and deploys. Use it in sessions the owner starts to manage the library (\"let's manage the library\", \"library session\", \"kütüphane oturumu\"), for the operator side of a migration, or before an HLMemo release or deploy."
---

# HLMemo library operator

The library operator is the orchestrator in a session the owner starts (D-246). Project chats write memory under the
protocol; the server librarian only flags candidates and answers `memory.ask`; the operator verifies, curates and
prepares the one-way doors, and **the owner opens them**. Every step below that changes shared or production state
says where the owner's OK comes in. The writer rules of the `hlmemo` skill still apply to anything you write.

Why the role exists: the librarian's labels were measured as untrustworthy on their own ("contradiction" right 2 of
277 times, D-244; no real contradiction among a migration's proposals, D-253), while a stronger model with the item
texts in front of it and a human gate was reliable. This skill is that stronger model's checklist.

Paths are relative to the HLMemo repository. `<state>` is the deploy state directory under `deploy/.local/`
(gitignored: ssh configs, wrappers, logs); `HLM_OPS_STATE=<state>` selects it for `deploy/scripts/hlm_ops.sh`.

## 0. Before touching production: which network path works

The owner's machine reaches the server over IPv6 at home and over IPv4 on some mobile networks; one of them may not
route at all. Test both before a deploy or an ops run, and use the one that answers:

```bash
ssh -F <state>/ssh_config      -o ConnectTimeout=8 hlm-deploy true && echo ipv4-ok
ssh -F <state>/ssh_config.ipv6 -o ConnectTimeout=8 hlm-deploy true && echo ipv6-ok
```

`hlm_ops.sh` and `tools/migrate/withdraw.sh` take `HLM_OPS_SSH_CONFIG=<state>/ssh_config.ipv6` when IPv6 is the
working path. macOS has no `timeout` command; do not wrap commands in it.

## 1. Status

- `HLM_OPS_STATE=<state> bash deploy/scripts/hlm_ops.sh status`: readiness, the migration head, the worker and
  librarian queues (`ready`, `in_flight`), the breaker, today's and this hour's spend (caps 3/8/60 USD per hour, day,
  month), the model chains.
- Per project: `memory_query` shows the card (is it stale?), and `librarian.pending_questions` the review backlog.
- Recent session notes: the brief of each project, or `memory_query` with `kinds: ["session_note"]`.
- Before any risky step, `memory_risk_check` **and** a `memory_query` with `kinds: ["lesson"]` for the component: the
  risk judge can drop a relevant lesson (R18), so read both `warnings` and `dropped_by_judge`. On a warning, drill
  the lesson and say how you comply.

## 2. Librarian proposals: verify, correct, withdraw

Proposals do not change reads while the librarian is an observer, but they must not pile up: **accepting one is not
the way to apply it.** Accepted proposals wait as `accepted_pending` and a later librarian role promotion mass-applies
every one of them (D-244, D-245). Real findings become writer corrections; the proposals are then withdrawn.

1. Let the librarian queue drain (status shows `ready=0 in_flight=0`), then export:
   `HLM_OPS_STATE=<state> bash deploy/scripts/hlm_ops.sh librarian audit --project <slug> --json > <private dir>/audit.json`.
2. Write a compact input per proposal (number, question id, relation, the librarian's reason, both subjects' clues,
   kinds and titles) and split it into halves.
3. Give each half to a Sonnet verifier agent with `references/verifier-brief.md`: it drills both subjects read-only
   and classifies each pair as NO_CONFLICT, HISTORY_REFUTED, STALE_CURRENT, GENUINE_CONTRADICTION (or REFINES_OK /
   REFINES_NO), with verbatim spans. Recount their verdicts by code; agents miscount.
4. **Check by code** that no current-state fact (kind `fact`, not a decision row) is the older side of a pair judged
   NO_CONFLICT or HISTORY_REFUTED: that is where a stale current claim would hide.
5. **Check by hand** every STALE_CURRENT and GENUINE_CONTRADICTION against the source repository (read-only). Two
   values for one experiment are often two different measures; decide which item is wrong and why.
6. Bring the owner the counts, the real findings and the plan. With the owner's OK:
   - a real finding becomes a writer correction: a `memory_write` with `updates` (revise one sentence, or supersede a
     whole stale item) in that project's slug, the owner having named it; or a note for the project's chat;
   - all proposals are withdrawn in one event:
     `HLM_OPS_STATE=<state> bash tools/migrate/withdraw.sh <slug> <ids-file> <expect> --reason '<why>' [--owner <name>]`.
     It snapshots the id file once, checks the live open set equals it, dry-runs under the locks, then writes one
     event; any mismatch stops it before anything is written. `--status accepted_pending` handles an older queue.
7. Record privately (`docs/private/librarian-review/<slug>-<date>/`: inputs, verdicts, the state file); publicly only a
   `D-` entry without project content.

## 3. Decision-row links

A decision row that fully reverses an earlier one says so in its text (`supersedes D-012`, `D-012'yi geçersiz
kılar`); `hlm links explicit` turns those declarations into part-scope `supersedes` links. It runs inside the API
container:

```bash
ssh -F <state>/ssh_config hlm-deploy 'cd /opt/hlmemo/app && HLM_ENV_FILE=/etc/hlmemo/prod.env bash deploy/scripts/stack.sh exec -T api hlm links explicit --project <slug> --dry-run --json'
```

Compare the edges with a local run on the migration's scratch database and with an independent extraction from the
row texts (0 missing, 0 extra). Show the owner the edges with each declaration. With the OK, run the same command
without `--dry-run` (one evented pass, reversible with `--revert`), then the dry run again: it must show
`already_linked` = the applied count and 0 proposals. A `memory_raw` of an old row should show `superseded_by` with
scope `part`. In the `hlmemo` project itself, the pass yields a known false positive; do not apply it there without a
reviewed dry run.

## 4. Cards

A card is the present-state summary every new chat sees first (≤ 420 tokens; the brief marks it stale after 3 days).
Refresh it through `memory_call_the_day` with `card_update`: present-tense lines only, each backed by an item or file
you read, `expected_version_id` = the current card version (the number in `card.clue`). A new project has a skeleton
card from `project create` (D-015): its version is the one a first card takes; the brief hides skeleton cards. On
`E_VERSION_CONFLICT`, re-read the card and redo the edit.

## 5. Doc→memory sync

Repository docs are the source of truth for a project that keeps them; the sync carries changes into memory:
`hlm import markdown <docs> --project <slug> --keep-missing --dry-run` first. Expect `new` and `changed` for the files
you know changed; an unexpected `changed` or any `closed` stops the run until explained (the mass-close guard needs
`--confirm-close`). Then the apply, with the owner's OK for production. Run it from the repository whose `hlm.toml`
names the intended server, with an explicit `--project`.

## 6. A migration's post-import steps (PLAYBOOK §16)

The project's chat runs the batches and writes the card; the operator does the rest, because it needs operator tools
and an agent that did not prepare the import.

1. **Before the batches** (F5, after the owner's OK on the review): `hlm_ops.sh project create <slug> --name '<Name>'
   --exists-ok`, `hlm_ops.sh device grant <device> <slug> write`, and the pre-import dump
   (`ssh … 'cd /opt/hlmemo/app && HLM_ENV_FILE=/etc/hlmemo/prod.env bash deploy/backup/backup.sh --pre-upgrade <hex>'`,
   note the printed path). Confirm over MCP that the slug answers with its skeleton card and 0 hits. Do not deploy
   while batches run.
2. **Dump copy.** Deploys prune `pre-upgrade/` to the newest 5 dumps, so copy the pre-import dump to
   `/var/backups/hlmemo/migration/<slug>-pre-import-<stamp>.dump` and compare the sha256 of both (D-255).
3. **Queues.** A query's `indexing_pending: false` means the embeddings are done; the librarian queue may still run.
4. **Links** (§3 above).
5. **Blind check** with `tools/migrate/blindcheck/` (its README): `extract` (checks the truth set's seal) → `ask` (one
   code-saved `memory.ask` per question through an isolated relay that never sees the gold) → `packets` →
   `run_graders.sh` (two isolated graders) → `score`. The bar: ≥ 0.80 correct on the answerable questions, 0
   superseded values stated as current, every negative abstains; on a grader split the stricter grade counts. Recompute
   the score yourself from the raw grade files. A failure is a finding, not a retry.
6. **Librarian proposals** (§2 above), once the queue has drained.
7. **Mapping.** After the card is written and the blind check passed, add `"<project folder>" = "<slug>"` to
   `[projects]` in `~/.config/hlm/capture.toml` (back it up first) and preview the hook for that folder:
   `echo '{"hook_event_name":"SessionStart","source":"startup","cwd":"<project folder>","session_id":"x"}' |
   HLM_SERVER_URL=<url> HLM_DEVICE_NAME=<device> .venv/bin/python -P -m hlmemo.brief.hook`. The brief must show the
   card and the lessons; "unavailable" means the fetch timed out or failed, so retry before concluding.
8. **AUDIT** (`docs/migration/templates/AUDIT.md`), private, signed by the owner and the operator; a public `D-`
   entry with pass/fail and no project content.

## 7. Test-drive feedback

A project chat may leave notes in `docs/private/test-drive/<slug>.md`. For each note: reproduce it (the same query or
call), decide (correct, partly, not reproducible), and answer **in the same file** under a dated "HLMemo answer"
section, with how to work until a fix ships, because the project's next session reads it there. Product changes go to
`docs/status/BACKLOG.md` without project content; protocol and skill wording changes ship as soon as they are clear
(the digest is injected into every mapped project at its next session start). An uncontrolled first test drive gets no
instructions from the operator: observe on the server side only, unless the owner asks for a controlled test.

## 8. Releases and deploys

- **Before review**: write the threat model and the severity rubric (`docs/consults/<n>-…-threat-model.md`).
- **Review**: Codex Astra low and Sol xhigh in parallel for one-way doors (data, security, releases), at most two
  rounds; every HIGH gets a reproducing test; measure what a new validation would refuse on real items before claiming
  it is safe (D-236); the owner decides residual risks. Tools-only changes get Astra low.
- **Release gate** (D-063): `make gate-release` against a disposable clone of the loaded retrieval world
  (`CREATE DATABASE <clone> TEMPLATE hlm_retr`, then `alembic upgrade main@head`); in a git worktree, point
  `HLM_MODELS_DIR` at the main checkout's `models/`. It must not run against an empty database.
- **Before push**: token-shaped test fixtures are built at runtime (string concatenation), or GitHub push protection
  and the pre-commit grep block the push; the `.githooks/pre-push` privacy gate runs on every push.
- **Deploy**: with the owner's standing OK, the private wrapper `bash deploy/.local/deploy_prod.sh <full sha>` picks the
  working network path and deploys only a commit on `origin/main` (extra argument: `--accept-compose-change=<sha256>`
  when the compose model changed). Without it: `PATH="$PWD/<state>/bin:$PATH" bash deploy/scripts/deploy.sh hlm-deploy
  <sha>` (`bin6` for IPv6). Tell the owner before each deploy.
- **After the deploy**: the live commit (`git -C /opt/hlmemo/app rev-parse HEAD` over ssh), then
  `bash deploy/scripts/remote_gates.sh --url <https url> --state <state> --ssh-config <state>/ssh_config[.ipv6]
  --librarian --no-drill`. **Always `--no-drill`**: the drill restores over live data (D-245). On a mobile network
  that accepts TCP on every port, `postgres-closed` fails falsely; the server-side listener check decides (D-255).
  Then live probes of what the release changed (for example a write that must be refused), and a query that shows
  nothing was stored.

## 9. Closing a library session

- `memory_call_the_day` in `hlmemo`: what changed, why, pointers, then `## Open`; decisions one line each with the
  reason; Promotion candidates if any.
- A `D-` entry in `docs/decisions/DECISIONS.md` for every one-way door opened, with counts and hashes only.
- Public files carry no project names, counts or findings; details live in `docs/private/`.
- Update the resume notes, so the next session starts from files, not from this chat.
