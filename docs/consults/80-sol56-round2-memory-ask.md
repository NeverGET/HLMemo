## Verdict (FIX-NEEDED)

T1 — FIXED — `src/hlmemo/core/memory_map.py:497`; `src/hlmemo/librarian/privacy.py:164`; `src/hlmemo/librarian/tasks/map_summary.py:113`.
T2 — FIXED — `src/hlmemo/librarian/tasks/research.py:369`; `src/hlmemo/librarian/tasks/map_summary.py:173`.
T4-high — FIXED — `src/hlmemo/librarian/provider.py:668`; `src/hlmemo/core/research_service.py:506`.
T4-med — FIXED — `src/hlmemo/librarian/tasks/map_summary.py:214,261,344`.
T5 — FIXED — `src/hlmemo/config.py:247,262`; `deploy/scripts/check_librarian.py:76,99,502`; `deploy/scripts/install_llm_env.sh:56,217`; `deploy/scripts/llm_env_release.py:85`; `deploy/RUNBOOK.md:677`. R4 marker, env-label manifest selection, R3-off state and fingerprint coverage verified.
T6 — OPEN — `src/hlmemo/librarian/tasks/research.py:241-257` | Source/quote says “not enabled”; claim says “enabled … and not disabled” → `answered=True` reproduced | compare polarity per proposition/window and add this regression.
New HIGH: none; T6 is the still-open round-1 HIGH.

## Residual (owner decision)

- `deploy/RUNBOOK.md:697` still describes the common step as `evaluate --release r3`; the R4-specific section is correct.
- Timeout/unknown-usage attempts charge worst-case USD but not tokens: `provider.py:723-785`.
- Turkish suffix negation remains unsupported.
- Targeted validation: 104 tests passed plus 48 subtests; DB integration was not rerun because `HLM_TEST_DSN` was unset.