# claude-mem compared with HLMemo (2026-10-15)

Subject: github.com/thedotmack/claude-mem at fef3402 (2026-10-13, v13.39.0), Apache-2.0. Read by a research agent
from a local clone; the key claims were spot-checked in the code. The ideas below are parked: evaluate them later,
one at a time, measure first (BACKLOG "Ideas from claude-mem").

## Verdict
The two projects solve two halves of one problem. claude-mem is an automatic, zero-effort log of "what did we do":
every tool call is turned into an observation by an observer LLM, and SessionStart injects the newest ones. HLMemo
keeps "what is true now, and what did we learn" correct over time. claude-mem leads on adoption, install and first-use
experience; HLMemo leads on correctness over time, privacy and measured quality.

## Facts worth keeping
- No supersession, versions or validity dates: `valid_from`, `valid_until` and `superseded_by` do not occur in the
  code; stale observations keep being injected by recency. A title-identical dedup can drop the newer observation.
- Semantic search drops hits older than 90 days: `RECENCY_WINDOW_MS = 90 days` at
  `src/services/worker/search/types.ts:7` (issue #4770).
- Privacy: all tool I/O goes to an LLM provider, telemetry is on by default (#4605), secret redaction is off by
  default (`CLAUDE_MEM_REDACT_ENABLED`), raw tool output is kept indefinitely, the local worker has no auth.
- Other open issues: observation loss on worker restart (#4580), the "10x token savings" claim never measured (#4138).
- Engineering volume: about 8k tests with CI, about 128k lines of TypeScript, 366 releases; one main maintainer.

## Ideas worth borrowing (parked)
1. **File-anchored recall.** When the agent reads a file (PreToolUse on Read), memories tied to that file are injected
   (`src/shared/hook-constants.ts`, `src/shared/worker-utils.ts`). This is the cheapest form of the "spontaneous recall
   outside risk_check" vision item (BACKLOG, third test-drive findings). HLMemo items already carry `describes`, which
   projects to `code_refs(version_id, path, commit)` (`core/import_contract.py:10`). Measure first: how often a read
   file has `code_refs`, and whether the injected items help or distract.
2. **Fitting the session-start block by measuring.** claude-mem renders the block, drops the most expensive section and
   renders again (`src/services/context/ContextBudget.ts`, `ContextBuilder.ts`). Our brief already does a measured
   fit: it is held to a token budget and drops the tail of Lessons, then Open, then Decisions, then shrinks the card
   (`brief/assemble.py:232`), inside a 9500-character room (`brief/hook.py:54`). What is left to compare is the drop
   order (which section is worth most) and whether the room matches the host's real limit.
3. **A write-and-exit spool for capture.** The hook writes the event to a file and exits at once; a repeated event
   dedups by file name. A good pattern for hook reliability (a capture never blocks or fails the session). Compare with
   our capture path before adopting.

## Not to adopt
- An LLM summary of every tool call as memory: noise, cost and privacy exposure, with no gain in correctness.
- Recency-only injection at session start.
- A dedup that discards the newer item when titles match.

## Where HLMemo is weaker
- Packaging: recommending HLMemo today means "run your own server". The gap is installation and usability (one-command
  install, an optional managed server, a viewer), not the memory model.
- Writers carry protocol overhead (tokens and attention); claude-mem's capture costs the writer nothing.
- Open items of our own: librarian precision, risk judge timeouts.
