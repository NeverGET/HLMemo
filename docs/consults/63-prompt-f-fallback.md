# Consult 63 — routine check (D-085, astra-low): workstream F, per-task fallback profiles

Your cwd is a clean export of ed88f80 (no .git, no secrets). `F.patch` is the diff from main to ed88f80. Spec: the D-093/D-094 rows in docs/decisions/DECISIONS.md, plus D-017, D-071 and D-084. Read-only.

## Claims to verify
- **Config:** `HLM_FALLBACK_PROFILE` is the default. Per-task overrides come from `HLM_FALLBACK_PROFILE__<TASK>` (env) or `[hlm] fallback_profile__<task>`, are resolved by `profile_chain`, and are swapped in per call by `Provider.chain_for`. There is no hard-coded task list, and no model id appears outside profiles/ (D-017).
- **Startup:** the api and the librarian fail fast on an unknown profile. The api does so only when the librarian is enabled. An unknown task name produces a warning.
- **D-084 and breakers:** D-084 still holds (the primary gets ≤55% of the remaining time and the fallback runs inside the deadline). Breakers are per model, and a task-specific fallback gets its own key. The ledger records the price of the fallback profile actually used.
- **Privacy precheck:** it still runs before every attempt, including fallback attempts. A fallback profile cannot bypass `data_collection=deny` or `require_parameters`.
- **risk_judge (D-071):** the task-specific fallback is used only if its profile does not disable risk_judge; otherwise the result is retrieval-only.
- **Production mapping:** deploy/llm.env.example follows D-094, and install_llm_env handles the new keys.
- **Ops:** ops status shows the effective chain per task.

## Output (≤ 20 lines)
- `## Verdict (MERGE | FIX-NEEDED)`
- For each claim: OK or PARTIAL/OPEN, with file:line.
- New findings, only if they are real defects: severity | file:line | trigger | fix.
