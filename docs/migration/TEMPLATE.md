# Per-project memory migration: moved to the PLAYBOOK

The migration procedure (v1, 2026-09-30) grew into `docs/migration/PLAYBOOK.md` (2026-10-07), with two tiers, source
adapters, item formats, the kit tools and the templates in `docs/migration/templates/`. Older decisions and consults
cite this file; their topics now live here:

| former section of this file | now |
|---|---|
| Guiding principle, hard rules | PLAYBOOK §1 Principles, §10 Secret gate |
| Naming, scope and grants | §2 Slug, scope and roles |
| 1. Inventory | §5 Source adapters, `templates/INVENTORY.md` |
| 2. Curation | §6 Curation, `templates/CURATION-SPEC.md` |
| 3. Exclusion and secret scan | §10 Secret and personal-data gate |
| 4. Local dry run, review package | §11 Local rehearsal, §14 Review package, `templates/REVIEW.md` |
| 4a. Chronological import | §8 Dates, §12 Batches, seal and manifests |
| 5. Owner review and OK | §2 Roles and gates, §14 |
| 6. Production import | §15 Production import |
| 7. Truth set and blind check | §13, `templates/TRUTHSET.md` |
| 8. AUDIT.md sign-off | §16 After the import, `templates/AUDIT.md` |
| Rollback | §17 Rollback |
| Artifact map | §20 Artifact map |

The full text of v1 is in the git history of this file (last version before 2026-10-07).
