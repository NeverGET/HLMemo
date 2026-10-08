# risk_check `unjudged` and frontmatter-free previews: review triage (consults 118, 119)

Two rounds (the D-125 cap), Codex Astra low and Sol 5.6 xhigh in parallel, each on a clean export of the candidate.
Threat model and rubric: `118-risk-unjudged-threat-model.md`, written before round 1.

## `unjudged` (risk_check)
No finding in either round from either reviewer. Answered explicitly: (a) no entry from an item the caller could not
get as a warning (same candidate set, D-062 re-check after a judge call); (b) `verdict`/`warnings`/`omitted`/`judged`
and the budget errors unchanged (Sol ran 3,000 differential budget cases); (c) no new unredacted path (the known
Redactor gaps remain, accepted in the threat model).

## Previews (`preview_text`)
| round | reviewer | finding | severity | fix |
|---|---|---|---|---|
| 1 | Astra | code or a list between two horizontal rules stripped as frontmatter | HIGH | bbd820d: a block must open with a `key:` line and hold a key the importers write |
| 1 | Sol | a task list between two rules stripped | HIGH | bbd820d (same rule); test added |
| 1 | Sol | a `#` comment inside real frontmatter blocked the skip | MEDIUM | 1d65aa3: comment lines allowed |
| 2 | Astra | a known key plus Markdown (heading, task list) between rules still stripped | HIGH | 9b7c523: line-by-line validator; a list item or an indented line needs a key with an empty value |
| 2 | Sol | `--- ` with a trailing space: the importer does not split it, the preview did | HIGH | 9b7c523: the importers' exact `FRONTMATTER_RE` (a unit test keeps them equal) |
| 2 | Sol | a 40-line cap missed long real frontmatter | MEDIUM | 9b7c523: no cap; linear (200,000 lines unclosed: 0.013 s) |

Every HIGH has its reviewer's input as a unit test (`tests/unit/test_d055_retrieval.py`).

## Measured after the fixes
- Real corpus (the owner's local Markdown memory stores and both migrations' curated files; counts kept privately):
  every importer-frontmatter file with a body is skipped, and the skipped text equals the importer's own split in all
  of them.
- PyYAML as an oracle rejects about 1 % of those real blocks (values such as `'[UPDATED: …]' in the heading`), so it
  cannot be the validator. In a 20,000-block random Markdown fuzz, 75 skipped blocks are YAML-invalid; each holds a
  top-level key the importers write and list or indented lines under a key with an empty value.

## Residual risk (owner decides)
- A document whose first lines are `---`, a key the importers write, list or indented lines under an empty key, and
  `---` is treated as frontmatter in its preview even when YAML would reject it. The importer splits the same block as
  frontmatter. Only the preview changes; drilldown and stored data never do.
- Values are not parsed (`title: [WIP] x` counts), for the same reason.
