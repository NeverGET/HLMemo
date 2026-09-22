# R2 result — production deploy tooling (implementation round)
Scoring per PROTOCOL.md: 1 point per judge-run gate (G-D1..G-D7); adjudicated cross-review defects subtracted
(High −3, Medium −2, Low −1, PLAUSIBLE −0.5). Adjudication: blinded experiments on identity-scrubbed copies
(submission A = gpt-6-astra, B = Opus 5.5; mapping unsealed after scoring).

| | Opus 5.5 | gpt-6-astra |
|---|---|---|
| Judge gates (G-D1..G-D7 + DB exposure) | 7/7 | 7/7 |
| Adjudicated defects in own submission | 2 High, 4 Medium, 1 Low (+1 Low judge-observed) | 0 High, 2 Medium, 3 Low, 1 PLAUSIBLE Medium |
| **Total** | **−9.0** | **−0.5** |
| Wall time | 17 min 54 s | 14 min 11 s |
| Cross-review it wrote of the other (confirmed / plausible / rejected) | 5 / 1 / 0 | 7 / 0 / 0 |

Opus High defects: `aws s3 sync --delete` from a fresh host wipes remote dumps (D10); documented rollback re-runs the
new image's migration on a restored old DB → old code cannot locate the revision → outage (D12).
Disclosed judgment calls: (1) the judge-observed env-file duplicate-key divergence (scripts take the first value,
Compose the last) was counted as Low only against Opus because only that submission's scripts re-parse the env file;
without it Opus = −8.0. (2) Documented behaviours observed by the judge (W1 needs HLM_ENV_FILE exported; W2 serves
503 for ~10 s after restore) were not penalised. (3) The neutral G-D4 drill script was fixed mid-run (export the
same vars as run_gates, wait for edge /ready); both submissions were re-run with the final version and both passed.

Outcome: the gpt-6-astra branch (bake/r2-astra) is merged to main; its 6 adjudicated defects are the deploy backlog (D-034).
