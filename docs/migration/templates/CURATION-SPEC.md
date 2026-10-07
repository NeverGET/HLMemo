# CURATION-SPEC: <slug> (template, PLAYBOOK §6–§9)

Copy to `docs/private/migration/<slug>/CURATION-SPEC.md` and fill the `<…>` blanks. Pilot it on one package (Full) or
on ten files (Light), fix it, then curate the rest. Every rule here carries its reason, so a curator can judge a case
the spec does not name.

## 1. Scope and language
- Slug: `<slug>`. Sources in scope: `<list>`. Out of scope: `<list, with reason>`.
- Body language: `<language>`. Titles: `<PREFIX> · <claim>` with bare stems and identifiers, plus the English term
  where agents search in English (the indexes do not stem). Optional `EN:` line: `<yes/no>`.
- Lines/topics and their prefixes: `<PREFIX>` = `<what it covers>`, …

## 2. Layout and kinds
| kind | directory | one per file? | notes |
|---|---|---|---|
| fact | `status/<topic>/` | yes | present state only, verified against the repo |
| lesson | `lessons/<topic>/` | yes | Mistake / Fix / Context; a lasting rule, no current values |
| episode | `sessions/<topic>/<topic>-<YYYY-MM>.md` | no: a monthly log | no frontmatter, no H1; `## YYYY-MM-DD <PREFIX> · <claim>` per episode |
| decision | `decisions/DECISIONS.md` | no: one log | no preamble; `D-NNN \| YYYY-MM-DD \| STATUS \| text`; numbering `<range>` |
File names unique across directories.

## 3. Content rules
- **A1: facts hold only the present.** Earlier values become dated episodes or decision rows. `STATUS`-type notes never
  become facts.
- One claim per fact, one rule per lesson. Title = the claim (≤ 80 chars; lessons ≤ 120).
- Numbers verbatim with their pointer (`path:line`); a table or JSON value beats prose; give N-of-M with its denominator.
- Present-state claims verified against the live repo (`VERIFIED <date>: …`); production and network facts marked unverified.
- No imperatives, handover instructions or standing authority; rules go to `CLAUDE.md`.
- No secret values; no personal data (`<policy for people: by role>`).
- Reversals in decision rows: `<full-reversal marker, e.g. "supersedes D-NNN">`; partial changes: `<partial wording,
  e.g. "narrows D-NNN", "D-NNN'in … kısmını değiştirir">`. **The links pass reacts to every marker in PLAYBOOK §9**
  (`hlm migrate markers --format md` prints the table), not only to "supersedes": "replaces", "superseded by",
  "merged from" anywhere, and "instead of" / "rather than" / "in place of" / "yerine" next to a D-id in a
  decision row. Use those words only for a real reversal, and compare `hlm migrate lint`'s link preview with the
  reversals you meant.

## 4. Frontmatter and tags
- Facts and lessons: `title`, `date`, `tags`, `source_path` (and `curated:` if wanted). Keep it short.
- Closed tag list: `<topic tags>`; lessons add `<stack>@<version>` + exactly one of `active` / `resolved` (+ `historical`).
- `date-estimated` on every estimated date, with a visible line saying from what.

## 5. Dates
- Explicit evidence first; else an estimate (last commit, else mtime) marked as one. The content's date, not a
  verification stamp. Same-day items get a time in `date:`.
- Era boundary for model-behaviour lessons: `<date and evidence>`.
- Layered legacy memory: freeze date `<date>`; originals before it, newer notes after it (PLAYBOOK §5.9).

## 6. Sizes
Facts and lessons about 300–1500 characters; episodes up to about 3000; every entry under 8000 (`lint` warns above
1500 and 3000). A monthly episode file with one entry needs a frontmatter `title` and `date`, or it is titled by its
file name.

## 7. Checker error classes (Full)
Missing denominators; prose contradicting its table/JSON; stale present tense; pointer past the end of its file; a date
from a verification stamp; an imperative stored as memory; a fact that holds history; a duplicate of another package's item.
