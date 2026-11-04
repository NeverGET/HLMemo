# 129: D-277 the live relation review's own models, the policy-aligned verifier and the lesson-record guard: threat model and rubric

Written before the review (D-266). The owner set the goal on 2026-11-04: implement the recommendations of the shadow
week's close.
- Move the live librarian's relation review to the measured shadow pipeline (Claude Haiku 5.5 high on relate/v3,
  Claude Opus 5.5 low as the verifier). The role stays observer.
- Measure the fixes for the week's blockers on a labelled replay set before shipping them.
- Keep the shadow running.

## The change
- **Settings, all empty or 0 by default, so that unset means today's behaviour:**
  - `librarian_relate_profile`, `librarian_relate_version`;
  - `librarian_relate_verify_profile`, `librarian_relate_verify_version`, `librarian_relate_verify_max_tokens`;
  - `librarian_shadow_verify_version`.
- **Live relate chain** (`write_review.live_relate_chain`): the named profile, then the relate task's default chain
  (D-094) as its fallback.
- **Live verifier chain** (`live_verify_chain`): the named profile, then the D-067 verifier chain.
- **Versions.** `live_relate_task` and `live_verify_task` pick the prompt version, an opt-in one included.
- **Startup.** `check_relation_review` makes the librarian refuse to start, naming the variable, when a profile or
  version does not resolve (as `check_chains` does).
- **New prompt.** `relate_verify/v3` is opt-in (never the default). It is the verifier aligned with the written
  supersession policy: rule narrowing and status or plan changes are conflicts; history, including a lesson's
  evidence or status, never conflicts. The schema is unchanged.
- **New guard.** `guards.lesson_record` runs after `guards.historical`, before the verifier, in both the live and
  the shadow pipeline. A supersession whose replaced side is a lesson is dropped when the quote of that side lies in
  the lesson's dated record (its `When:` paragraph or Context section) and not in its rule (the Mistake title, or
  Fix Do/Avoid). Lessons without a Fix section are never split.
- **Ops status and the startup log** show the relation review's own primary and version on the relate and
  relate_verify chains.
- **Possibly** `OLD_TEXT_CHARS` 1500 → 4000, if the replay set supports it (B2).

## Assets and threats
- **T1 bypass.** A configured profile's call skips the budget reservation, the per-job call cap (lineage), the
  privacy precheck before every attempt, the redactor, the breaker, or the per-attempt deadline. Every call must
  still go through `provider.complete` with the same arguments as today; only `chain` and `task` change.
- **T2 default drift.** With the settings unset, any change to the live prompts, chains, max_tokens, judgements or
  audit. (The lesson guard is an intended behaviour change for both pipelines; it must only ever drop.)
- **T3 role or ladder.** A model switch changes what the librarian may apply. The observer role must stay the only
  gate; `auto_ok` and tier logic are unchanged except for the guard's drops.
- **T4 lesson guard errors.**
  - It drops a supersession of a lesson's rule (a false negative beyond its definition).
  - It raises on an odd body: regex, empty quote, missing sections.
  - It touches a side that is not a lesson.
  - It runs after the verifier, so a dropped judgement still spends a verifier call or keeps a stale verification.
- **T5 availability.**
  - A typo in a new variable makes the librarian refuse to start, which is the intended fail fast. It must not
    break the api process or ops status.
  - The ops `describe_chains` path must never raise on a bad name.
- **T6 spend.** The named profiles' reservations (relate/v3 max_tokens 12000, a verifier max_tokens up to 32000)
  stay inside the hour, day and month caps, and the per-job call cap still binds.
- **T7 D-017 and D-094 invariants.** No model id outside `profiles/`; the generic fallback code (`config.py`,
  `profiles.py`) names no task.
- **T8 shadow.** The shadow's own verifier version applies only to the shadow and never changes the job (D-272).
- **T9 the fallback mix.** When the named relate profile fails, the default chain answers relate/v3 (or the
  configured version). That answer must be guarded, audited and verified exactly like one from the primary.

## Rubric
- **HIGH:** T1, T3, or a crash path in the live librarian (T4, T5) reachable with a valid configuration. It needs a
  reproducing test.
- **MEDIUM:**
  - T2 drift when unset;
  - a T4 false-negative path beyond its definition;
  - T6 caps not binding;
  - a T7 invariant broken;
  - T8 leaking into the job;
  - T9 unguarded.
- **LOW:** docs, naming, audit field semantics, ops display.
