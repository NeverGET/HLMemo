# Example C: a lesson in the D-222 mapping

Moved out of `../SKILL.md` to keep it under 24 KB. Read it before `memory.register_lesson`.

`memory_register_lesson` requires `project`, `request_id`, `mistake` and `fix`; `context` and `tags` are optional.
The server takes the title from `mistake` line 1, builds the body as `## Mistake`, `## Fix`, `## Context`, and
returns `clue`, `logical_id`, `version_id`, `replayed`.
```json
{"project": "my-project", "request_id": "<fresh UUID>",
 "mistake": "Test schema migrations on a restored prod copy before prod\nWhen: applying a schema migration to the production database. It ran on prod first, locked a large table for 4 minutes and timed out requests.",
 "fix": "Do: run it on a restored copy of the prod dump, time it, and schedule lock-heavy steps in a quiet window.\nAvoid: running an untested migration on prod; trusting a dev-sized table to predict the prod lock time.",
 "context": "Evidence: \"lock timeout on table events\" (deploy log 2026-09-28, episode v733).\nNot verified for: online schema-change tools.\nScope: postgres@16.\nStatus: active.\nEra: observed 2026-09-28, the current model era.",
 "tags": ["postgres@16", "active"]}
```
- `mistake` line 1 is the title the brief shows: at most 120 chars, readable on its own. The rest of `mistake` is
  **When** + what went wrong; `fix` = **Do** + **Avoid**; `context` = **Evidence** (a verbatim quote and its
  pointer), **Not verified for**, **Scope**, **Status**, **Era** (from dated evidence, not guessed).
- `tags` = the scope (`<stack>@<version>`) plus exactly one of `active` or `resolved`, plus `historical` for a
  lesson from an earlier model era. A historical lesson is `resolved` + `historical`; the server refuses
  `active` together with either (PV-5).
- A lesson states a lasting rule. A value that changes (a version string, a setting in use) belongs in a `fact` the
  lesson points to; inside the lesson it goes stale with the next change.
- A lesson that would hold in every project is still registered in your project and named under "Promotion
  candidates". Merging duplicate lessons and changing a lesson's status or era is the operator's job.

