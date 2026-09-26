## Verdict

FIX-NEEDED

## Findings

| threat | severity | file:line | reproducing scenario | fix |
|---|---|---|---|---|
| T1 | HIGH | `core/memory_map.py:451`; `librarian/privacy.py:125`; `tasks/map_summary.py:88` | Projects A and T; T has `librarian_cross_project=exclude`; caller reads both; item `[A,T]` contains `T-ONLY-SECRET`. `memory.ask(project=A)` includes it in the map/provider prompt; map-summary also sends it as A content. | Apply D-083 `relation_allowed`/isolation rules in view loading, privacy rechecks and summary grouping; add a prompt-leak regression. |
| T4 | HIGH | `core/research_service.py:454`; `tasks/research.py:1089`; `provider.py:560` | With question cap `$0.01` and per-attempt worst cost `$0.006`, a schema-invalid attempt plus retry costs about `$0.012`; fallback can do likewise. The budget is checked only before the logical call. | Reserve/check the remaining per-question USD and token budget before every provider attempt, using that profile’s worst case. |
| T4 | MEDIUM | `tasks/map_summary.py:187,237,286` | One due group fails schema validation on an otherwise idle DB: `_pending=False`; even after 301 seconds, `due()` returns `[]` because the event marker is unchanged. Back-off retry never occurs. | Re-arm `_pending` after `_failed()` and make cache failure/back-off state independently trigger scans. |
| T5 | HIGH | `config.py:246,259`; `deploy/llm.env.example:45`; `deploy/scripts/check_librarian.py:71` | Existing valid R3 env: librarian enabled/live, `HLM_ENV_RELEASE=r3`, research flags absent. Deploying this image passes the R3 manifest but silently advertises `memory.ask` and starts map-summary provider spend because both defaults are true. | Default new features off and introduce a release manifest/fingerprint that explicitly pins research, map-summary and their fallback configuration. |
| T6 | HIGH | `tasks/research.py:194,481,699` | Source: “The release gate is **not** enabled by default.” Model claim/quote omits “not”. `find_in_order` accepts it, and a literal-free claim remains `answered=True`, citing text that contradicts the answer. | Require a contiguous normalized quote match; remove the word-skipping fallback or reject inserted negations/modality. Add this exact adversarial test. |

## Safe-to-ship

No — scope isolation, per-question caps, release-state correctness and citation honesty are bypassable. Also, this export ends at D-136; D-140–D-146 could not be verified.