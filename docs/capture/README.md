# Session capture (GOAL-PLAN B1)

When a Claude Code session in a **mapped** project ends (or compacts), HLMemo receives, automatically:

- one short **session note**: what was decided and changed, with file and commit pointers;
- the **decisions** that were explicitly made (at most 12);
- **lessons**, only where a mistake or learning is explicit in the session (0 is the normal case).

Everything goes through `memory.call_the_day` (no card update). Status: **built and evaluated offline,
not installed anywhere**. Installation is an owner decision (see "Install").

Guiding principle (GOAL-PLAN): learning something halfway is worse than not knowing it. So: nothing that
is not in the transcript, every item carries provenance, uncertainty is explicit, and any failure writes
*less* (a minimal note) or *nothing*, never garbage.

## Where the code lives

`src/hlmemo/capture/` (a package next to `importers/`, `cli/`): it is client-side code like the importers,
it reuses their secret rules (`importers/common.py`, D-213) and the `hlm` client (`cli/`), and it gets unit
tests in `tests/unit/` like the rest. `tools/` does not exist in this repo.

| module | job |
| --- | --- |
| `hook.py` | the hook entry point: stdlib only, ~35 ms, queues the input and starts the worker **detached** |
| `run.py` | the detached worker: per-session lock, state, pipeline; `python -m hlmemo.capture.run --dry-run DIR ...` for offline use |
| `reduce.py` | JSONL transcript -> about 40k chars |
| `scrub.py` | secrets + emails, before any LLM call and again before writing |
| `summarize.py` | `claude -p` (Haiku), strict JSON schema, validation, grounding checks |
| `write.py` | payload composition, `uuid5` session key, writers (direct `hlm` client, `claude -p` relay, dry-run file) |
| `config.py` | mapping file, kill switch, paths, log |

## Flow

```
SessionEnd / PreCompact hook  (stdin: session_id, transcript_path, cwd, hook_event_name, reason|trigger)
  hook.py: HLM_CAPTURE=off? -> exit.  cwd not mapped? -> exit.  queue input, Popen(detached), exit 0   (~35 ms)
  run.py (detached, own session):
    lock(session_id)  ->  state: lines already captured, pending write
    reduce(transcript[offset:]) -> scrub -> claude -p haiku (JSON schema) -> validate -> compose -> scrub again
    -> residual-secret scan -> CloseRequest validation -> memory.call_the_day -> state advance -> 1 log line
```

### Trigger and idempotency (one choice: disjoint segments)

`call_the_day` closes a `session_id` **once per project** (`E_SESSION_CLOSED`), so a checkpoint cannot be
revised in place. The three options were: (a) later checkpoint supersedes the earlier one, (b) skip
PreCompact, (c) write disjoint segments. We chose **(c)**:

- state remembers how many transcript lines are already captured; every run captures only the lines after
  that offset, under the segment's own stable key;
- segment 0 (the common, never-compacted session) has the key `uuid5(NS, "<claude session id>:<slug>")`;
  later segments add `:seg<N>`;
- a PreCompact checkpoint is a segment boundary, so very long sessions are summarised piece by piece (each
  piece gets its own 40k-char window, nothing is lost to the cap) and nothing is duplicated;
- SessionEnd after a resume (per the docs, Claude Code fires SessionEnd with `reason: resume` and again at the real end,
  same `session_id`) captures only the lines added since: no duplicate, and no write when nothing is new;
- crash safety: the composed payload is stored as *pending* before the call; a retry resends it unchanged
  (same `request_id`), and `E_SESSION_CLOSED` is treated as "already written";
- (a) is impossible with the current server contract and (b) loses sessions that never end cleanly
  (kill, crash) and gives very long sessions a single 40k window.
- Cost of (c): a compacted session yields two or three notes instead of one. The librarian handles the
  consolidation (Phase D); each note is self-contained and dated.

A per-session `flock` lock file (`<state>/locks/<session_id>.lock`) serialises workers: a SessionEnd worker
that starts while the PreCompact worker still runs **waits** (up to 15 min) instead of dropping its work.

### Reduction (`reduce.py`)

Kept: the owner's messages (<= 1500 chars each), assistant texts (the last text of a turn up to 2500 chars,
other narration only when >= 400 chars, clipped to 800), the compaction summary (first segment only),
commit subjects (hashes from `git commit` results; `-q` commits fall back to the message in the command),
files edited through editor tools. Dropped: tool outputs, thinking, system reminders, hook text, task
notifications, sidechain (subagent) records. Over the cap (40,000 chars), the first owner message and the
last 8 segments are mandatory; the rest is chosen by decision-bearing score (decision/bug/because/D-xxx...)
and recency, with `[... N turns omitted ...]` markers. Shell-made edits are not listed as files (the note
says so).

### Scrub (`scrub.py`)

Before any LLM call and again on the model's output before writing: a line matching the `importers/common.py`
rules (private keys, AWS/GitHub/sk-/Slack/Google/hlm tokens, JWT, `KEY=value` assignments, DSNs with
passwords, email+password pairs) or the capture-specific rules (bearer/Authorization, long mixed-case+digit
tokens, blobs over 4000 chars) is **dropped whole**; emails are masked as `[email]`. A final residual scan
of the composed payload blocks the write if anything is still flagged.

### Summarizer (`summarize.py`)

`claude -p --model claude-haiku-4-5-20251001 --effort low --tools "" --strict-mcp-config
--disable-slash-commands --no-session-persistence --setting-sources "" --json-schema ...`, run in a scratch
directory with `HLM_CAPTURE=off` (so the child's own hooks are a no-op: no recursion). The system prompt
enforces the principle (only the transcript, no speculation, uncertainty into `uncertain`, lessons only when
explicit, decisions only when made). Output validation:

- shape errors -> **minimal note** (date, span, commit list, "summary unavailable"); never prose without a
  validated summary;
- **lessons need a verbatim evidence quote that occurs in the transcript**, else they are dropped (this fired
  2-3 times per evaluated session);
- commit hashes or `D-xxx` ids in the output that do not occur in the transcript are moved into the note's
  `## Uncertain / unverified` section;
- caps: notes 1500 words, 12 decisions (300 chars), 8 lessons, 10 uncertain items.

The model id is configuration (`[capture].model`), not code (D-017).

### Note format (provenance on every item)

The note opens with programmatic facts (not LLM output): "AUTO-CAPTURED ... summarised by <model>; not
reviewed", Claude session id (and segment), time span, owner-message count, commits, files. Lessons carry
the verbatim evidence, the source session and the tag `auto-capture`. Notes are `session_note` items;
the librarian sees them like any other write.

### Writer (`write.py`)

1. **Direct** (preferred): the `hlm` client stack, reusing the Mac's device config: server/device from the
   `hlm` client config (`./hlm.toml` of the session cwd, `HLM_*` env, or `[writer]` in `capture.toml`), token
   from `HLM_DEVICE_TOKEN` / OS keychain / `~/.config/hlm/credentials.toml` (`cli/credentials.py`). The token
   is never printed or logged. `client = "claude-code/capture-hook"`, `session_id` = the uuid5 above,
   `request_id` = uuid4, `occurred_at` = the last transcript timestamp (never in the future).
2. **Relay** (only when no token is found or the direct call errors): `claude -p --allowed-tools
   mcp__hlm__memory_call_the_day`, relaying the already-scrubbed payload through the `hlm` MCP server that
   Claude Code already has configured. An unverifiable relay result is logged as an error and retried later
   (`E_SESSION_CLOSED` then means "done").
3. **Dry run**: `HLM_CAPTURE_DRYRUN=<dir>` (or `--dry-run DIR`) writes the payload JSON (mode 0600) and
   uses a separate state dir; nothing reaches prod.

Not exercised against prod in this workstream (no prod writes allowed): the direct path is unit-tested with
a faked client; before enabling, run one dry run and one real capture of a throwaway session.

## Mapping file: `~/.config/hlm/capture.toml`

```toml
# cwd prefix (path-component boundary, longest wins) -> HLMemo project slug. Unmapped cwd = no-op.
[projects]
"/Users/cemalkurt/Projects/HLMemo" = "hlmemo"

[capture]
enabled = true                       # false = kill switch in the file
min_owner_messages = 3               # sessions with fewer owner messages are not captured (relay/subagent runs)
exclude = [".claude/worktrees"]      # path fragments that are never captured
# model = "claude-haiku-4-5-20251001"
# cap_chars = 40000

# [writer]                           # optional overrides of the hlm client config
# server_url = "https://mcp.hlmemo.com/mcp"
# device_name = "cemals-mb-pro-3"
# relay_fallback = true
```

Start with this one project only (owner decision). A missing or unreadable file means capture is off.

## Install (NOT done; owner decision)

Project-scoped, in `/Users/cemalkurt/Projects/HLMemo/.claude/settings.local.json` (gitignored: `.gitignore`
line `.claude/settings.local.json`), merge into the existing `hooks`:

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "/Users/cemalkurt/Projects/HLMemo/.venv/bin/python -m hlmemo.capture.hook",
            "timeout": 5
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "/Users/cemalkurt/Projects/HLMemo/.venv/bin/python -m hlmemo.capture.hook",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

The same block works in `~/.claude/settings.json` (every project; the mapping file still limits it to mapped
cwds). `hlmemo` is installed editable in that venv (`hlmemo.__file__` resolves to `src/hlmemo`), so no
`PYTHONPATH` is needed once this branch is merged. Hook input JSON (verified on this machine with a real
`claude -p` run): `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `reason` (SessionEnd:
`clear|resume|logout|prompt_input_exit|other`) or `trigger` (PreCompact: `manual|auto`), plus `prompt_id`.

Hook facts that shaped the design:

- SessionEnd hooks share a 1.5 s budget unless a longer per-hook `timeout` (seconds, max 60) is set; ours
  exits in ~35 ms (measured, 5 runs, unmapped and killed paths; the mapped path adds one small file write and
  a `Popen`). PreCompact hooks do not block compaction on timeout.
- **Detached child survival.** The docs say a detached child may not survive session exit. Measured here:
  in a `claude -p` run whose SessionEnd hook started a `start_new_session=True` child that slept 6 s after the
  parent had exited, the child finished (file written). Not verified in an interactive session exit (cannot
  be driven from a subagent): the first real `/exit` after install must be checked in `capture.log`.
  Fallback if it does not survive: a `launchd` WatchPaths job that runs the worker over
  `<state>/queue/*.json` (the hook already writes that queue file before spawning).

## Disable

- Per session / shell: `HLM_CAPTURE=off` (also `0`, `false`, `no`); the hook and the worker both check it.
- For good: `[capture] enabled = false`, remove the project line from `capture.toml`, or delete the file;
  or remove the two hook blocks from settings.
- Already-written notes are ordinary `session_note` items in HLMemo (provenance says `auto-capture`).

## Safety

- Never blocks or crashes the session: hook and worker swallow every error and exit 0.
- No content is logged: `<state>/capture.log` (`~/.local/state/hlm/capture/capture.log`) gets one line per
  run: status, counts (owner messages, reduced chars, lines dropped, emails masked, decisions, lessons),
  LLM latency, tokens, cost, writer path. State and queue files are mode 0600.
- Sessions with fewer than `min_owner_messages` (default 3) are skipped: subagent and one-prompt relay runs.
- The summarizer call uses `HLM_CAPTURE=off` and a scratch cwd, so it can never trigger a capture itself.

## Cost and latency (measured, offline evaluation 2026-09-30, dry-run, Haiku 4.5, `--effort low`)

| session | transcript | reduced | Haiku in/out tokens | summarizer latency | notional cost |
| --- | --- | --- | --- | --- | --- |
| A: 3aacf803 (multi-day, 44 owner msgs) | 20.3 MB | 38.2k chars | 17.1k / 7.3k | 76 s | $0.071 |
| B: 07becd3e (live, 88 owner msgs) | 43.3 MB | 38.2k chars | 46.0k / 16.7k | 179 s | $0.146 |
| C: B cut at the half (checkpoint case) | 22.2 MB | 38.2k chars | 15.8k / 9.0k | 107 s | $0.077 |

So roughly **$0.07-0.15 and 1-3 minutes per capture, detached** (API-equivalent notional cost; it runs on
the owner's subscription). The hook itself costs ~35 ms. The output-token count is well above the note
length, which points at reasoning/structured-output overhead; `--effort low` cut it from 9.2k to 5.4k tokens
on A. Possible tuning (not measured, the 6-call budget of this workstream was spent): a smaller `cap_chars`,
or a different summarizer model via `[capture].model`. The run used the first prompt revision; the final
revision asks for shorter notes and adds the session span (two small prompt edits after the runs).

Scrub result on the same three runs: 1 line dropped per session (a token-like string, an `sk-` key, a
token-like string), 1 email masked in A, and **0 secrets and 0 emails survived** in the LLM input or in the
payload (residual scan over both). Lesson grounding dropped 2-3 unsupported lessons per session.
Outputs: `docs/private/capture-eval/` (gitignored, 0600).

## Tests

`tests/unit/test_capture.py`, `tests/unit/test_capture_writer.py` (40 tests, no network): reduction (noise
dropped, cap, resume offset, partial last line, compaction summary, MCP edit tools), scrub (secrets, emails,
credential pairs, false positives, idempotence, residual scan), summary validation (grounding, caps,
malformed), pipeline idempotency (written once, later checkpoint = new segment, retry with the same payload,
`E_SESSION_CLOSED`), the per-session lock, minimal-note fallback, unmapped cwd and missing mapping file
(no-op), kill switch, hook exits 0 fast on garbage, dry run, `occurred_at` never in the future, writer error
mapping, payload accepted by the server's own `CloseRequest`.
