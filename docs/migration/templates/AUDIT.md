# AUDIT: <slug> migration (template, PLAYBOOK §16)

PRIVATE. Copy to `docs/private/migration/<slug>/AUDIT.md`. Signed by the owner and the operator; a `D-` entry records
the outcome without project content.

| check | result |
|---|---|
| Tier, slug | <Light/Full>, `<slug>` (project id <n>) |
| Curated set | <n> items, sealed (`<tree digest>`), owner-approved REVIEW (`D-<n>`) |
| Secret gate | gitleaks <0>, importer `skipped 0`, manual scan <clean>; leaked values found in sources: <none / handled how> |
| Local rehearsal | dry → apply <n>/<n>, re-run `unchanged` |
| Pre-import dump | `<path in migration/>`, sha256 `<prefix>` verified against the original |
| Prod import | <time window>; batches <counts>, each dry → apply; written <n>, 0 failed/changed/closed/skipped; verify all unchanged |
| Card / session note | skeleton v<n> → v<n>; note v<n> |
| Decision-row links | proposals <n> = local run = independent extraction; owner OK; applied (event <n>); re-check `already_linked <n>` |
| Indexing | `indexing_pending: false` before the blind check |
| Blind check | truth set v<n> `<hash>`; correct <x>/<n> = <ratio>; superseded as current 0; negatives <n>/<n>; per language <…>; graders agree <n>/<n>; spend <$> |
| Librarian proposals | <n> verified (verdict counts), real findings <n> → writer corrections <clues>; withdrawn (event <n>) |
| Mapping | `capture.toml` → `<slug>`; brief checked (card, lessons) |
| Old stores | auto-memory archived; old notebook frozen; project `CLAUDE.md` memory section reduced |
| Grant | `<device>` keeps `write` (project chats write here) / removed |

Findings for HLMemo (to the backlog): <list>.

Signed: owner <date>, operator <date>.
