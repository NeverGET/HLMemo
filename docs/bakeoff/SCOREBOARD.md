# Scoreboard (Opus 5.5 vs gpt-6-astra) — see PROTOCOL.md
| Round | Type | Opus 5.5 | Astra | Winner | Details |
|---|---|---|---|---|---|
| R1 | review (Phase-0 audit) | **15** (8/8 confirmed, 2 High) | 10 (8/9 confirmed, 0 High, 1 rejected) | Opus | r1/RESULT.md |
| R2 | implementation (deploy tooling) | −9.0 (2 High defects) | **−0.5** (0 High) | Astra | r2/RESULT.md |
| R3 | implementation (F13 + G-B security fixes) | −12 (1 unique High) | **−10** (unique Lows only) | Astra | r3/RESULT.md |

## Verdict after R1–R3 (D-036)
Opus 5.5 won the review round (15 vs 10) and wrote the more precise cross-reviews in every round
(R2: 5/1/0 vs 7/0/0, R3: 5/0/0 vs 3/0/1 confirmed/plausible/rejected; plus the High stdin-swallow find on D-034).
gpt-6-astra won both implementation rounds (R2 −0.5 vs −9.0; R3 −10 vs −12). Opus was faster in R1 and R3.
Under the owner's rule (Opus must clearly outperform for Astra to go back to advisor), Opus did NOT clearly
outperform as an implementer → **keep the current strategy**: gpt-6-astra implements; Claude orchestrates and
runs adversarial reviews and neutral gates on every Astra change.
