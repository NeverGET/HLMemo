# 135: agy port release: threat model and severity rubric (written before the review)

Date: 2026-11-26. Scope: one release containing
1. **MCP wire names** (server): `tools/list` advertises `memory_query`-style names (`.` → `_`); `tools/call` accepts the
   wire name and the canonical dotted name. Internals keep the dotted name. Reason: Antigravity CLI (`agy`) 1.2.9
   rejects tool names outside `^[a-zA-Z0-9_-]{1,64}$` (the Gemini/OpenAI function-name rule), so all HLMemo tools
   were dropped in agy since its self-update on 2026-09-23.
2. **Brief hook agy dialect** (client): `hlmemo.brief.hook` reads agy's camelCase SessionStart input and prints
   `injectSteps`; Claude input and output unchanged.
3. **Capture agy dialect** (client): `hlmemo.capture.hook` on agy's per-turn `Stop`, with an agy transcript reducer
   and a size threshold; Claude path unchanged.

## Assets and trust boundaries
- Production memory (append-only, private, multi-account with guest isolation D-274/D-275).
- The device bearer of each client; owner-only tools (`hlm.questions`) behind the owner client capability.
- Agent context: what the brief and capture put into a model's context or into memory.

## Threats to check
- T1 Authorization bypass by name: a wire alias that skips the owner-only check, the guest/account checks, the
  read/write classification (`READ_TOOL_NAMES`), metering or budgets, or dispatches a client-only tool that must
  not be listed.
- T2 Advertising drift: a conditional tool (`memory.ask` while research is enabled) listed when it should not be, or
  a client tool (`hlm.export`, `hlm.questions`, `hlm.project_create`) appearing on `tools/list`.
- T3 Compatibility: an existing client (Claude Code on two machines, Codex, the `hlm` CLI, brief/capture hooks,
  gates G7/G-SURF/probe) that breaks because it compares advertised names or calls a name the server no longer accepts.
- T4 Collision: two canonical names mapping to one wire name.
- T5 Capture privacy: an agy transcript part (tool output, file contents, secrets) reaching memory without the
  existing scrub; capture of an unmapped folder; a hook that blocks or slows the agy loop (Stop is synchronous).
- T6 Capture duplication or loss: per-turn Stop writing overlapping segments, or the tail never captured.
- T7 Brief injection: the agy path printing a brief for an unmapped folder, or reading the hook process's cwd
  (the hooks.json folder) instead of the session's.

## Severity rubric
- HIGH: an auth/isolation bypass (T1), private data written to memory without scrub (T5), a break of an existing
  client or prod gate (T3), or silent data loss/duplication in memory (T6). Needs a reproducing test before the fix.
- MEDIUM: wrong listing (T2), a hook that can block the agy loop for over a few seconds, a wrong-folder brief (T7).
- LOW: wording, logs, docs, test gaps without a concrete trigger.

Reviews: two fresh-context agents in parallel, different models and angles (correctness/regressions;
security/privacy/contracts), each with this file and the diff, findings with `file:line` and a trigger; at most two
rounds (D-266, D-125). Cemal decides residual risks and the deploy.
