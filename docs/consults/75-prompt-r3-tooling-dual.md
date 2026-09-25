# Consult 75 — CRITICAL dual review (D-085), RELEASE-GATING for R3: env-aware release tooling (r3-tooling @ 3b2ce64)

Your cwd is a clean export of 3b2ce64. `TOOLING.patch` is the whole diff against main; it touches only deploy/ and tests/deploy. Read the rows D-065, D-094, D-098, D-099, D-108, D-111 and D-116 in docs/decisions/DECISIONS.md, plus deploy/RUNBOOK.md ("R3 release"). Read-only.

## What R3 is
Main = J + F; the query rewrite is shelved and is NOT in R3. It is a code-only upgrade from R2 (no alembic, compose or Dockerfile change). The R3 llm.env adds the D-094 per-task fallback profiles, which the R2 image CANNOT load. That is why:
- llm.env is part of the release state: it is snapshotted with every release, and rollback restores the previous release's env BEFORE starting the previous image. If recovery fails, the newer env is put back.
- `check_librarian` validates llm.env against `RELEASE_MANIFESTS["r3"]`:
  - a missing llm.env fails;
  - rewrite and cap must be absent or false;
  - in R3 mode (`--release r3`, or `HLM_ENV_RELEASE=r3` running) both api and librarian must run the R3 env with the D-094 keys;
  - an unmarked R2 env passes as the labelled D-108 interim.
- The D-108 order: deploy the R3 image with the R2 env → verify → install the R3 env → verify → (rollback: restore the R2 env first, then the R2 image).

## Focus
- **Walk every state of the D-108 order, including crash or interrupt at each step.** Is there ANY state in which a running image has an env it cannot load, or api and librarian run different env releases, or a rollback leaves the R3 env under the R2 image? Is the re-run of an interrupted deploy or rollback idempotent?
- **The snapshot store:** permissions (the env holds API keys), atomic writes, symlink safety, cleanup of superseded snapshots, and never logging secret values.
- **The manifest check:** can it PASS wrongly (e.g. a stale container env vs the file on disk)? Can it FAIL wrongly on a correct R3 cutover?
- **One-way door (D-065):** the previous-release pre-W0 guards are still intact.
- Test adequacy for the crash/interrupt paths.

## Output contract (≤ 35 lines)
- `## Verdict (MERGE | FIX-NEEDED | DO-NOT-MERGE)`, with one paragraph.
- `## Findings`: a table of severity | file:line | trigger | fix. Real defects only.
- `## Release-safe`: yes or no, plus the reason.
