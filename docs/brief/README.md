# AL5: the SessionStart brief (D-225 "assist now")

An LLM-free, client-side Claude Code `SessionStart` hook. When a session starts in a mapped project it
injects a short memory brief (at most 1,500 o200k_base tokens, the same meter as the server's budget
block). Everything in it is a verbatim memory line (cut with an ellipsis, never summarised) with its
`[vN]` handle, so the agent can `memory.drilldown` / `memory.raw` it.

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
- Superseded or non-current items are excluded (see `src/hlmemo/brief/fetch.py`, `read_raw` and
  `superseded_pool_ids`). `memory.query` hits carry no superseded flag (D-207 defect #5), so the brief
  reads `memory.raw` of each candidate and drops it when any candidate-pool item has a LIVE `supersedes`
  link to its `logical_id` (or its own version is expired/superseded, or `memory.raw` failed).
  Known gaps: a superseder outside the pool (newest 8 session notes + 14 lessons) is not seen; `scope=part`
  links look whole (excluded as a whole); decisions are bullets inside a note, so a later note that reverses
  an earlier decision without a link is not detected.
- Fail-open: unmapped project, kill switch, bad input, no token, unreachable server, any exception, an
  empty result or the 4 s wall clock (a watchdog thread, `os._exit(0)`) print nothing and exit 0.

## Config

Same mapping file as capture, `~/.config/hlm/capture.toml` (`[projects]`, `[capture] exclude`, `[writer]`
server/device overrides; the hlm client config `./hlm.toml` and the keychain token are used as for capture).
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

Kill switches: `HLM_BRIEF=off` (also `0`, `false`, `no`), `[brief] enabled = false`, or no mapping.
Dry run: `HLM_BRIEF_DRYRUN=<dir>` writes `<session>-<source>-<epoch>.brief.txt` and a `.meta.json`
(tokens, sections, excluded handles with reasons, ms) and injects nothing. Every run appends one
content-free line to `<capture state dir>/brief.log` (`~/.local/state/hlm/capture/`).

Follow-up (not implemented): a dedicated hook device with a read-only grant, so the hook does not use the
owner's personal device token.

## Install (NOT done; owner decision)

Project-scoped, in `<project>/.claude/settings.local.json` (gitignored), merged into the existing `hooks`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|clear|compact",
        "hooks": [
          {
            "type": "command",
            "command": "/Users/cemalkurt/Projects/HLMemo/.venv/bin/python -m hlmemo.brief.hook",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

Hook input (stdin): `session_id`, `transcript_path`, `cwd`, `hook_event_name = "SessionStart"`, `source`.
Output (stdout, exit 0): `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"<brief>"}}`.
Suggested first step: run with `HLM_BRIEF_DRYRUN=/tmp/hlm-brief` set in the hook command for a few sessions,
read the files, then remove it.

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
