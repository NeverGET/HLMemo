# Bake-off protocol: Claude Opus 5.5 vs codex gpt-6-astra
Started 2026-09-22 (owner request, D-031). Purpose: decide whether Astra stays an ACTIVE IMPLEMENTER
(current strategy since the Fable limit, D-028..D-030) or returns to an ADVISOR/REVIEWER role.

Decision rule (owner): if Opus 5.5 clearly outperforms Astra → Astra returns to advisor role.
If tied or Opus is behind → keep the current strategy (Astra implements, Claude orchestrates).

## Conflict of interest
The orchestrator/judge is itself Opus 5.5 and one contestant is an Opus 5.5 subagent. Mitigations:
1. Identical prompts, identical constraints, identical start commit; contestants are not told they compete.
2. Scores come from deterministic gates run BY THE JUDGE on both outputs with the same commands.
3. Findings/defects are adjudicated by reproduction (a failing test or an exact failing input), not opinion.
   A blinded verification worker sees findings stripped of their source label.
4. Cross-review: each contestant reviews the other's deliverable under a neutral label.
5. All raw outputs, gate logs and scoring arithmetic are committed under docs/bakeoff/ for owner audit.

## Round types and scoring
Review round (R1): static audit of the same committed code.
- Each finding: CONFIRMED (reproduced) / PLAUSIBLE (not reproduced, code-read supports it) / REJECTED.
- Score = Σ severity weight of CONFIRMED (High 3, Medium 2, Low 1) + 0.5×PLAUSIBLE − 1×REJECTED.
- Also reported: unique confirmed finds, wall time.

Implementation round (R2+): same brief, separate git worktrees/branches from the same commit.
- Gate score: judge runs the brief's gates identically on both branches (1 point per gate passed).
- Defect score: cross-review findings, adjudicated as above, SUBTRACTED from the implementer
  (High −3, Medium −2, Low −1).
- Also reported: wall time, orchestrator interventions needed, lines changed.
- The winning branch (or a documented best-of merge) lands on main; the other is archived as a branch.

## Rounds planned
| Round | Type | Task |
|---|---|---|
| R1 | review | Phase-0 final audit (doubles as the scheduled codex round-11 GO/NO-GO) |
| R2 | implementation | Production deploy tooling (`deploy/`): compose.prod + Caddy TLS, backups/restore, Terraform Hetzner, runbook |
| R3 | tbd | chosen after R2 (likely the D-026 raw-paging TODO or the Phase-1.5 importer) |
