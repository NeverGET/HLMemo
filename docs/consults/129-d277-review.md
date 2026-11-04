# 129: D-277 review (the live relation review's own models, relate_verify/v3, the lesson-record guard, OLD_TEXT_CHARS 4000)

Threat model and rubric: `129-d277-threat-model.md`. Round 1 used two fresh-context agents in parallel, both reading
the repository read-only:
- **Fable:** correctness and regressions.
- **Sonnet:** security, privacy, spend and contracts.

Neither found a HIGH. Round 2 checked the fixes.

## Round 1 findings and resolutions
| # | Sev | Finding | Resolution |
|---|---|---|---|
| F-M1 | MEDIUM | `live_verify_chain` put the named verifier first even when that profile had answered the relate call after a fallback, so a self-verification was recorded as a cross agreement | Under `cross`, the answering profile is never the first verifier (the cross verifier goes first, the named one last). Under `self`, the named verifier does not apply. Test `test_the_answering_profile_is_never_the_first_cross_verifier` |
| F-M2 | MEDIUM | relate/v2 at 2000 output tokens with 4000-character older items: a `scope: whole` answer can overrun and lose the chunk (`relate_schema_fail`) | Kept at 2000. Raising it invalidates the recorded G-LIVE-B cassettes, and v2 now runs only as the shadow baseline. The live relate/v3 has 12000. The shadow's `relate_schema_fail` is watched (BACKLOG) |
| S-M1 | MEDIUM | `OLD_TEXT_CHARS` 1500 → 4000 is not behind the unset switch | Accepted on purpose. It is the measured fix (B2) for both pipelines; rolling it back needs an image rollback. Recorded in D-277 |
| S-M2 | MEDIUM | An unpriced named profile passed startup and then refused every live call, taking the relation review down | `check_relation_review` refuses it (unless the budget guard is off), and also a profile disabled for its task and a broken profile file, each naming the variable. Test `test_the_startup_check_refuses_unpriced_disabled_and_broken_profiles` |
| S-M3 | MEDIUM | `lesson_record` treated the whole `When:` paragraph as the dated record; the protocol's `When:` begins with the trigger | The first sentence of `When:` is part of the rule. Test `test_a_quote_in_the_lessons_rule_or_trigger_stays`. The shadow week's two false cases are still dropped and its two true ones kept |
| L1 | LOW | Display and startup paths raised on a broken profile file (TOML or JSON errors) | Both catch any exception; ops shows the profile without a model id. Test `test_ops_status_never_raises_on_a_broken_profile` |
| L2 | LOW | Startup gaps: price expiry, `disabled_tasks`, the shadow relate version | Checked. A warning names prices that expire within 30 days, and a verifier equal to the relate profile. Profile comments updated |
| L3 | LOW | relate_verify/v3 dropped "When unsure, answer unclear"; `replaces_all` and `adds_detail` were optional | Sentence restored; both fields required in the v3 schema. Re-measured on the shadow-week pairs: 38/38 schema-valid answers, same scores |
| L4 | LOW | `librarian_relate_verify_max_tokens` accepted 1–255 | 0, or at least 256 (validator). Test `test_verify_max_tokens_is_zero_or_at_least_256` |
| L5 | LOW | 3-deep chains (head plus two defaults) used up to 15 call-cap claims in an outage | Each chain is now the head and ONE fallback (D-019). Test `test_a_failing_named_head_falls_back_once_with_every_gate` |
| L6 | LOW | `check_librarian.py` does not probe the RELATE_* and SHADOW_* profiles | Open. A bad name stops the librarian at startup, and `install_llm_env.sh` validates the names |
| L7 | LOW | The shadow baseline (luna, glm) shares per-profile breakers with the live default chain | Accepted. The shadow's 180 s wall makes a cut rare (luna p95 9 s); job content is unaffected |
| tests | LOW | The fallback path, a dropped lesson making no verifier call, the 3999/4000/4001 boundary, and the deploy template line were untested | Added, including the reviewer's real-Provider probe (budget, precheck, lineage claim, call cap, privacy denial) |

## Process note
Both agents' test runs (unit tests, with the autouse `_clean_tables`) deadlocked against the operator's concurrent
integration run on `hlm_test`. That run was discarded, and the suites were re-run one at a time on the final code.
Lesson v4514.

## Round 2 (one fresh-context agent, Fable; unit tests with `--noconftest`, 41 passed)
Every round-1 finding was checked against the code:
- **Resolved:** F-M1, S-M2, S-M3, L1 to L5, the tests.
- **Accepted as reasonable:** F-M2, S-M1, L7. For S-M1 the reviewer asked that the D-277 entry exist before the
  commit; it now does.
- **Open:** L6.

New findings, fixed with tests:
| # | Sev | Finding | Resolution |
|---|---|---|---|
| NEW-1 | MEDIUM | The trigger regex ended at any `. ` or line end, so "e.g." or a wrapped `When:` line pushed the rest of the trigger into the record, and a supersession of it would be dropped | The trigger ends at a full stop followed by a capital letter, or at the paragraph's end. Test `test_the_trigger_survives_abbreviations_and_wrapped_lines` |
| NEW-2 | LOW | The `self` docstring was inexact, and the self-verification warning described `cross` ordering under `self` | Docstring corrected. Under `self`, the startup check warns that the named verifier is ignored. Test `test_the_startup_check_warns_when_the_named_verifier_is_ignored` |
| NEW-3 | LOW | The "dropped lesson spends no verifier call" test did not go through the provider path | It now runs `_relate` and `_verify` through the real Provider with a lesson candidate. A record quote makes no `relate_verify` call; a rule quote does |

Two rounds is the cap (D-266). No HIGH was found in either round.
