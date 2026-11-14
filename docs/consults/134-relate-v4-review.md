# 134: relate/v4 (D-283), one fresh-context reviewer

2026-11-14. Change: `relate/v4` as an opt-in prompt version (relate/v3 plus one block, the D-282 test), two lines
in `prompts/__init__.py`, a unit test. Reviewer: one agent on another model than the author, read-only, no test
runs. Rubric: HIGH = production behaviour changes without the setting, a gate, packaging step or config check
fails or rejects version 4, or schema-invalid answers; MEDIUM = a sentence that plausibly causes wrong answers on
a common case, or a test that does not test what it claims; LOW = wording, comments, docs.

## Findings
- **No HIGH.** Loading, pins, the live and shadow settings, the startup check (no allow-list of version numbers),
  packaging (hatch packages the directory), the schema (byte-identical to v3's): clean.
- **MEDIUM (prompt, the "NOT IN EFFECT" bullet):** "says or implies that it is not built" could let a model read a
  later decision row that reverses a design choice or default, without the word "built", as not in effect, and
  answer "none". Suggested: "says" instead of "says or implies", and name a reversed decision, design choice or
  default as replaced when the later item states it as the standing choice.
- **LOW (prompt):** "limits who uses it" next to v3's "a rule narrowed or replaced in part" (a narrowing that
  makes the old wording false is still a contradiction); "after the other item" assumes the other item is the
  later one.
- **LOW (test):** a hard-coded default version; no test that the startup check accepts version 4.
- **LOW (docs):** the env template and the runbook do not say that 4 is selectable.

## What was done
- **The MEDIUM is not applied to the text, on purpose.** The shipped text is the measured text (D-283: frozen
  before the held-out run), and a prompt change is re-measured before it ships. The case the finding describes
  was in the measurement: of 46 true pairs in which a later decision row replaces an earlier one, today's prompts
  raised 46 and the new text 45. The reviewer's rewording and both LOW wording points are a candidate revision,
  to be run as its own arm in the replay on the next import.
- **Tests:** the default is now computed from the version list, and the startup check is tested with version 4
  on the live and the shadow side.
- **Docs:** left as they are until the live switch, which changes the env template anyway.
