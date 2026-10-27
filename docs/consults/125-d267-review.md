# Review 125: D-267 (brief resilience, foreign-clue hint, write-update reasons)

Threat model and rubric: `125-d267-threat-model.md` (written before the review). Two fresh-context agents in parallel,
neither the author (D-266): Fable (security, privacy, contracts) and Sonnet (correctness, regressions). Read-only,
diff `main..d267-brief-resilience`. Two rounds.

## Round 1
| # | Sev | Reviewer | Finding | Outcome |
|---|---|---|---|---|
| 1 | HIGH (Fable) / MEDIUM (Sonnet) | both | The saved brief was shown after any `error:server`, including refusals (`E_FORBIDDEN_PROJECT` for a revoked read grant, `E_NOT_FOUND`) | fixed c3b935d: an allowlist (`cache_allowed`: timeout, no answer, `E_UNAVAILABLE`, `E_RATE_LIMITED`); tests for four refusal codes and two transient ones |
| 2 | MEDIUM | Fable | The background vocabulary download had no network timeout; the rate-limit marker never recorded success, so hung children could pile up | fixed 86732ca: 60 s socket timeout in the child, marker removed on success |
| 3 | MEDIUM | both | Cache tmp file opened with `O_TRUNC` following symlinks; unbounded read | fixed c3b935d: `unlink` + `O_EXCL|O_NOFOLLOW`; symlink and 256 KB guard; test |
| 4 | MEDIUM | Sonnet | `replacement_in_other_item` for a short/common replacement or one in several siblings | fixed c3b935d: ≥ 2 words and exactly one sibling; parametrized test |
| 5 | LOW | Fable | Watchdog fallback armed only after the cache read | fixed 86732ca: digest line armed first |
| 6 | LOW | Fable | Armed line said "6.5 s" while the watchdog fires at the wall clock | fixed 86732ca |
| 7 | LOW | Sonnet | Watchdog slug regex stops at `_` | no change: server slugs match `^[a-z0-9][a-z0-9-]{1,63}$` |
| 8 | LOW | Sonnet | Interpreter start before `main()` is outside the wall clock | accepted (unchanged from before; WALL_S 8 vs the 10 s registered timeout) |
| 9 | LOW | Fable | One extra query for a scope-hidden row in a readable project (timing) | accepted (sub-millisecond, only for projects the caller reads) |
| 10 | LOW | Fable | `os.environ["TIKTOKEN_CACHE_DIR"]` set process-wide | accepted (only the hook process loads the brief encoder) |

Checked clean by both: `_readable_home` runs the identical predicates with the same scopes, times and statuses and
needs READ on the other project (no leak; G5 unchanged); single stdout line; exit 0; healthy-path fetch still 6.5 s;
no download inside the hook (the read_file swap also covers the hash-mismatch path); only one write outcome changes;
the pessimistic ack bound holds.

## Round 2
Both: every finding closed, nothing new. Tests at 86732ca: unit 288, integration 35 (Sonnet); unit 85 +
integration 35 (Fable).

## Lessons recorded (owner rule 2026-10-22)
hlmemo v4100 (download inside a deadline-bound hook), v4101 (shrink loop sized by a penalising counter), v4103
(fallback keyed on a broad error class), v4104 (background download without a timeout).
