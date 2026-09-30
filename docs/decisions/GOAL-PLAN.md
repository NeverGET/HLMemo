# Goal plan: from a passive archive to an active, cross-project memory (2026-09-30)

## The goal (owner, 2026-09-30)
HLMemo gives coding agents a long-term memory that:
- spans projects;
- is handled actively, capturing lessons and learnings and superseding stale facts;
- is supplied when needed, without being asked.

The agent then focuses on "what to do", not on re-reading "what it did", and does not overflow its context. The cost may run higher than planned; a fully working memory saves far more time and money than it costs.

## Guiding principle (owner, 2026-09-30): "Bir şeyi yarım öğrenmek, hiç bilmemekten beterdir"
Learning something halfway is worse than not knowing it: partial or wrong learning builds reflexes that must later be unlearned. Unlike a human, an agent's memory is controllable, so HLMemo must do better than human memory on exactly this:
1. **Provenance on every item:** where it came from and when it was true, so wrong knowledge can be traced.
2. **Unlearning is built in:** new facts supersede old ones without deleting them (bi-temporal store, D-118 write-time supersession, curated links).
3. **Not knowing is explicit:** abstain rather than guess, and say "as of" when the memory may be out of date.
4. **Nothing is trusted unverified:** migrated knowledge is curated, dry-run, scanned, owner-reviewed and blind-checked before it counts.

## Where we are (audit of 2026-09-30, evidence in D-ids)
- **Live in prod (R1–R4):**
  - the Phase 0 core (bi-temporal store, hybrid retrieval, MCP tools);
  - access hardening (W0a/W0b);
  - import/export (W1.5);
  - the librarian foundation (W2a–W2f; the librarian runs as **observer** only);
  - `memory.ask` with the Gemini 3.8 Flash medium writer (D-199…D-203): .76 correct, $0.020/q, p95 31 s.
- **Built but not in prod:** write-time supersession (D-118, branch `wf-write-updates`, never ported).
- **Partial:**
  - the Memory Map (summaries off);
  - legacy migration: 1 project (`hlmemo`, 554 items, ad hoc);
  - operations hygiene.
- **Not started:** consolidation (W3b), decay (W3c), cross-project knowledge sharing (W4a stub), the migration template/runner (W5a), and the rest of W3/W4/W5.
- **What makes it passive today:**
  1. nothing writes to prod memory since 2026-09-26: no capture loop;
  2. recall only happens when asked: no hooks, and the D-014 preflight gate was never measured;
  3. only one project is in memory, and `query`/`ask` are single-project;
  4. new writes are never superseded (D-118 not ported).
- **Hygiene:**
  - `main` (docs to D-205) and `r4-rc` (the prod code) have diverged;
  - STATUS.md is stale;
  - the project card is an empty skeleton;
  - the librarian has 178 open questions;
  - the defects from D-202: silent truncation, the dropped first list item, no "as of" line.

## Plan (at most 3 workstreams at a time; each phase is timeboxed; ceiling-first for LLM-quality work)

### Phase A: foundation (about 1 day, starts now)
- A1: merge `r4-rc` into `main` (one line of history again), and refresh STATUS.md.
- A2: the quick defects:
  - a truncation marker plus a larger default budget for memory.ask;
  - renumber a list when the validator drops an item;
  - an "as of <newest recorded_at>" line on status-type answers;
  - an ops warning on Gemini balance/quota errors (D-205).
- A3: dogfooding restart: write the R4 story (D-192…D-205) into prod `hlmemo` via `memory.call_the_day`, and fill the project card.

### Phase B: the memory comes to the agent (the core of the goal)
- **B1 capture:** a Claude Code session-end hook. It turns the session into a short session note plus candidate lessons and decisions (a cheap summarizer; Sonnet via `claude -p` or luna), and writes them through `memory.call_the_day` / `register_lesson`. Duplicates and noise are left to the librarian.
- **B2 proactive recall:**
  - a UserPromptSubmit hook runs a fast `memory.query` (across the granted projects plus `hlm-global`) on the prompt and injects the top clues within a small budget (about 1–2k tokens);
  - a PreToolUse hook runs `memory.risk_check` before risky commands (deploy, push, rm, migrations);
  - `memory.ask` stays the deliberate deep-dive tool.
  - **Ceiling first:** replay recent real sessions offline and measure how often the injected clues would have changed the next action, before wiring anything globally.
- **B3 active supersession:** port D-118 onto the prod line, measure it once on the client-behaviour benchmark, and release it.

### Phase C: cross-project knowledge (parallel data work)
- **C1 migration runner + template (W5a),** following the Phase-5 protocol, per project:
  1. inventory;
  2. an LLM curation pass by a Sonnet agent: split, classify fact / lesson / decision, flag stale items, drop secrets and personal data;
  3. a dry run;
  4. a gitleaks scan of the exact file set;
  5. owner review;
  6. apply;
  7. a small sealed truth set (10–20 questions) with a blind check.
- **C2 first projects:** a first set of the owner's projects; projects with secret hits come only after a cleanup pass.
- **C3 importers:** a NotebookLM export (markdown) path; a codex memory path later.
- **C4 cross-project read:** let `query`/`ask` span the granted projects plus `hlm-global`, and promote project lessons to `hlm-global` (W4a).

### Phase D: quality and maintenance (after B/C show real use)
- librarian promotion from observer to assistant, gated on measured precision;
- consolidation and decay (W3b/W3c);
- answer quality .76 → .80 (stale-as-current first);
- the budget decision (D-205).

## Costs
- Imports go through the librarian (luna, about $0.0013 per item): 2,000 items ≈ $2.60 on OpenRouter.
- Curation by Sonnet agents runs on the Claude subscription.
- Gemini credit is spent only on `memory.ask` questions.

## Owner decisions (2026-09-30)
- B hooks: **measure first** (the offline ceiling study), then decide; start with this project only.
- C first projects: a first set of the owner's projects (listed privately). Each project gets the owner's review before its prod write.

## Owner decisions this plan needs (original list)
1. Installing hooks in the owner's Claude Code settings: capture (B1) and recall (B2). They change how every session behaves.
2. Moving projects from "NotebookLM-first" (global CLAUDE.md) to "HLMemo-first", project by project, as each is migrated.
3. The review and OK per migrated project before its prod write (C1 step 5).
