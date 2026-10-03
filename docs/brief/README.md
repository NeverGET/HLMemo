# AL5: the SessionStart brief (D-225 "assist now") and the "HLMemo mode" digest

An LLM-free, client-side Claude Code `SessionStart` hook. When a session starts in a mapped (registered)
project it injects, in this order:

1. the **"HLMemo mode" digest**: the writer rules of `docs/protocol/HLMEMO-PROTOCOL.md` section 6 with the
   project's slug filled in (39 lines, about 3,200 characters / 780 tokens; see "The digest" below);
2. a short **memory brief** (at most 1,500 o200k_base tokens, the same meter as the server's budget
   block). Everything in it is a verbatim memory line (cut with an ellipsis, never summarised) with its
   `[vN]` handle, so the agent can `memory.drilldown` / `memory.raw` it.

An unmapped cwd gets nothing at all, so the hook can be installed globally (see "Install").

```
# Memory brief: project <slug> (read-only, verbatim lines from HLMemo, not model-written)
## Now (project card [vN])          the card text (<= 420 tokens) + a stale flag
## Recent session decisions (auto-captured, unreviewed)
                                    "Decisions" bullets of the newest 3 session notes recorded in the last
                                    7 days (4 per note), each as [YYYY-MM-DD vN auto]; older notes add nothing
## Open                             "Open / Uncertain / Unverified" bullets of the same notes (3 per note)
## Lessons                          newest 5 current lessons: title + first body line
## Pending review                   count of pending librarian questions + the newest notice
as of <date>; auto-captured items (auto) are unreviewed. Drill down with the [vN] handles.
```

Empty sections are skipped; the whole brief is skipped when the card is still the D-015 skeleton and
there is no decision, open item or lesson. `auto` after a handle = written by the capture hook, not
reviewed by the owner. "as of" = newest `recorded_at` among the items read.

## Trust rules

- Read-only: only `memory.query` and `memory.raw` are called. No writes, no LLM, no relay (the capture
  relay runs `claude -p`; it cannot fit the budget, so with no device token the hook says nothing).
- Superseded or non-current items are excluded (see `src/hlmemo/brief/fetch.py`). The brief PREFERS the
  server's own status (B3, closing D-207 defect #5): a `memory.query` hit flagged `superseded` and the
  INCOMING `superseded_by` of `memory.raw` (live links whose superseder the device can see, whatever its
  kind or age). A whole-scope entry excludes the item as `superseded`, part-scope entries only as
  `superseded-part` (the brief cannot tell whether the line it would show is the outdated statement).
  The old pool fallback stays as a second check (and for an older server): an item is also dropped when a
  candidate-pool item has a LIVE outgoing `supersedes` link to its `logical_id`. An item is also dropped
  when its own version is expired/superseded or `memory.raw` failed. A span revision's link from an item
  to ITSELF never hides it (it supersedes the old version). A version a D-118 update or its reversal
  wrote has no request item of its own: its body is rebuilt from its chunks' exact offsets, and an item
  whose body cannot be rebuilt is dropped as unverified (never shown empty).
  Known gaps: on an older server a superseder outside the pool (newest 8 session notes + 14 lessons) is not
  seen and `scope=part` links look whole; decisions are bullets inside a note, so a later note that
  reverses an earlier decision without a link is not detected.
- Fail-open, exit 0 always. Unmapped project, kill switch or bad input: nothing is printed. Mapped project
  whose brief cannot be produced (no token, unreachable server, any exception, an empty result, the 3.2 s
  fetch timeout, or the 4 s wall clock of the watchdog thread): the digest plus ONE line
  `# Memory brief: project <slug>: unavailable this session (<why>). Use memory.query (token_budget 3000)
  for your task.` With `HLM_BRIEF_DIGEST=off` such a run prints nothing, as before the digest. The
  watchdog (`os._exit(0)`) prints that digest-only fallback itself when it has to end a hung process
  (a blocking keychain call or import cannot be cancelled by the fetch timeout).

## The digest ("HLMemo mode")

- Source of truth: the ```` ```text ```` block of `docs/protocol/HLMEMO-PROTOCOL.md` section 6. The hook
  ships a byte copy as package data, `src/hlmemo/brief/protocol_digest.txt`, and fills its `<slug>`
  placeholders with the mapped slug. `tests/unit/test_brief_digest.py` fails when the two differ, so a
  protocol edit must update the resource in the same commit.
- Every mapped project gets it on `startup`, `clear` and `compact` (the `[brief] sources`), whether or not
  the brief has anything to show. It is not counted in the brief's 1,500-token budget.
- Size: Claude Code caps a hook's `additionalContext` at 10,000 characters (beyond that it saves the text
  to a file and the model sees only a 2,000-character preview). The hook holds digest + brief to 9,500
  characters (`CONTEXT_CHARS` in `hook.py`): the brief keeps its token budget and also gets only the
  characters the digest leaves (about 6,300); over that, `assemble` drops lines in its usual order
  (lessons, open, decisions, then shrinks the card).
- Suppress only the digest with `HLM_BRIEF_DIGEST=off` (also `0`, `false`, `no`). If the resource is
  unreadable (a broken install), the brief is still injected without it.

## Config

Same mapping file as capture, `~/.config/hlm/capture.toml` (`[projects]`, `[capture] exclude`, `[writer]`
server/device overrides; the hlm client config `./hlm.toml` and the keychain token are used as for capture).

Registering a project = one `[projects]` line: the cwd prefix (path-component boundary, longest prefix
wins, symlinks resolved) and the project's HLMemo slug (`^[a-z0-9][a-z0-9-]{1,63}$`; an invalid slug is
ignored). Any session whose cwd is that folder or below it gets the digest and the brief:

```toml
[projects]
"/Users/cemalkurt/Projects/HLMemo" = "hlmemo"
"/Users/cemalkurt/Projects/<folder>" = "<slug>"   # the slug must exist on the server, with a grant

[capture]
exclude = [".claude/worktrees"]   # path fragments never mapped: honoured by the brief and the digest too
```

The `[capture] exclude` fragments apply to the brief exactly as to capture (`BC.map_cwd` is
`CC.map_cwd`): with the line above, a session in `<project>/.claude/worktrees/<name>` gets nothing. Note
that `[projects]` is shared: a line added for the brief also enables capture for that folder wherever the
capture hook is installed (today: the HLMemo project only, see `docs/capture/README.md`).

Optional table (the brief is NOT silenced by `[capture] enabled = false`):

```toml
[brief]
enabled = true                                   # false = off
sources = ["startup", "clear", "compact"]    # resume is off: the context is already present
decisions_max_lines = 0                         # >0: that many decision and open lines, from the NEWEST qualifying non-auto note only
lesson_body_chars = 0                            # >0: add the first body line (cut) to each lesson
include_auto = false                             # true = also show auto-captured (unreviewed) notes and lessons
decisions_max_age_days = 7                       # session notes older than this add no decisions/open items
budget_tokens = 1500                             # 200..1500
```

Kill switches:

| switch | effect |
| --- | --- |
| `HLM_BRIEF=off` (also `0`, `false`, `no`, `disabled`) | nothing at all: no digest, no brief |
| `HLM_BRIEF_DIGEST=off` (same values) | the brief only, as before the digest; a failed brief then prints nothing |
| `[brief] enabled = false` | nothing at all, for every project |
| remove the project's `[projects]` line | nothing for that project |

Dry run: `HLM_BRIEF_DRYRUN=<dir>` writes `<session>-<source>-<epoch>.brief.txt` (the would-be
`additionalContext`: digest + brief, or digest + the unavailable line) and a `.meta.json` (status, digest,
tokens, sections, excluded handles with reasons, ms, chars) and injects nothing. Every run appends one
content-free line to `<capture state dir>/brief.log` (`~/.local/state/hlm/capture/`):
`<time> <status> <ms>ms tokens=<brief tokens> chars=<context chars> digest=<0|1> sections=Digest,Now,...`.

Follow-up (not implemented): a dedicated hook device with a read-only grant, so the hook does not use the
owner's personal device token.

## Install

Status: installed project-locally for the HLMemo project since D-228 (`.claude/settings.local.json` of
the HLMemo repo, gitignored). Global install (every mapped project) is the entry below in
`~/.claude/settings.json`, merged into its existing `hooks`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|clear|compact",
        "hooks": [
          {
            "type": "command",
            "command": "/Users/cemalkurt/Projects/HLMemo/.venv/bin/python -P -m hlmemo.brief.hook",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

- **Absolute interpreter.** The command is the HLMemo repo's own `.venv/bin/python`, where `hlmemo` is
  installed editable (`src/hlmemo`), so it works from any project's cwd with no `PYTHONPATH`. If that
  venv is missing or broken the command fails; Claude Code treats that as a non-blocking hook error and
  the session starts normally.
- **`-P` is required for the global entry.** Claude Code runs the hook with the session's cwd, and
  `python -m` would otherwise put that cwd first on `sys.path`: a project with its own `json.py` (or any
  other stdlib-named module) would run inside the hook (reproduced; `tests/unit/test_brief_digest.py`
  guards the documented command).
- **One entry only.** When the global entry is installed, remove the SessionStart entry from the HLMemo
  repo's `.claude/settings.local.json`. Claude Code runs an identical handler once, but the old local
  command has no `-P`, so both would run and the HLMemo project would get the digest and brief twice.
- **Unmapped cwd: fast and silent.** Before the mapping check the hook imports only light stdlib modules
  (no `asyncio`, no socket, no fetch/assemble/tiktoken/hlm client), opens no connection and prints
  nothing. Measured on the owner's machine: about 33 ms per run (a bare interpreter start is 16 ms);
  `test_unmapped_path_imports_nothing_heavy_and_prints_nothing` pins the import list.
- **Worktrees** under a mapped folder are excluded by `[capture] exclude = [".claude/worktrees"]` (see
  Config); keep that line when adding projects.
- Capture (`SessionEnd`/`PreCompact`) is a separate hook and stays project-local; installing this entry
  does not install capture anywhere.

Hook input (stdin): `session_id`, `transcript_path`, `cwd`, `hook_event_name = "SessionStart"`, `source`.
Output (stdout, exit 0): `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"<digest>\n\n<brief>"}}`.
To inspect before relying on it, set `HLM_BRIEF_DRYRUN=/tmp/hlm-brief` in the hook command for a few
sessions and read the files.

## Reviewed items only by default (D-206)

`include_auto = false` (default): decisions/open lines of auto-captured notes and auto-captured lessons
are NOT shown; only the card, non-auto lessons and non-auto notes (within the age window) appear. The
"Pending review" line counts them ("at least M auto-captured items", from what the brief fetched) next to
the librarian questions. The "Now" heading shows the card version's date and "may be stale" after 3 days.

## Titles and the card, not history (blind gate)

The blind gate (22 lines: 11 useful, 10 noise, 1 stale) showed that decision-history lines and lesson
bodies are noise and an older note's decision can be stale. Defaults now: `decisions_max_lines = 0` (no
decision/open history; the card is the canonical current state) and `lesson_body_chars = 0` (lesson titles
only; drill down for the body). With `decisions_max_lines > 0`, lines come from the newest qualifying
non-auto note only, never mixed with older notes.
