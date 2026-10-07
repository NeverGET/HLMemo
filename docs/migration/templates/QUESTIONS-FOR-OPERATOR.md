# Questions from the <slug> migration chat to the HLMemo operator (template, PLAYBOOK §2)

PRIVATE. Copy to `docs/private/migration/<slug>/QUESTIONS-FOR-OPERATOR.md`. The operator answers in
`ANSWERS-FROM-OPERATOR.md`, tagging each answer **[code]** (verified, with `file:line`), **[tested]** (run on a local
stack) or **[rec]** (a recommendation).

## Already decided (do not ask again unless your project differs, and then say how)
| topic | default | where |
|---|---|---|
| who creates the slug and grant, when | the operator, at F5, right after the owner's OK on REVIEW | PLAYBOOK §2 |
| pre-import dump | the operator, right before batch 1; copied to `migration/` after the import | §2, §15 |
| who runs the prod batches | this chat, under the owner's OK given here | §2 |
| drains between batches | not needed; the operator checks queues once afterwards | §12 |
| format | generic markdown import with the directory → kind layout; no hand-built `hlm_export` | §7 |
| lesson shape and tags | Mistake/Fix/Context; `<stack>@<ver>` + `active` or `resolved` (+ `historical`) | §7, §8 |
| supersession inside the import | facts hold the present; reversals as decision-row markers; links pass by the operator | §1, §9, §16 |
| the card | `call_the_day` `card_update` after the import, `expected_version_id` = skeleton's version | §15 |
| deep documents | pointers by default; a few living documents as doc chunks only on the owner's say | §5.4 |
| local API | `tools/migrate/local_stack.sh up <slug>`; neutral cwd, explicit `--project` | §11 |
| item count | no cap; every item must be one a later chat would act on | §1 |
| mapping in `capture.toml` | the operator, after the card and the blind check | §16 |

## Open questions
1. <question, with the project facts the operator needs: sizes, sources, what you tried>
