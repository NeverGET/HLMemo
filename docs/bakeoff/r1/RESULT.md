# R1 result — Phase-0 final audit (review round)
Scoring per PROTOCOL.md: CONFIRMED High 3 / Medium 2 / Low 1, PLAUSIBLE 0.5, REJECTED −1; severity is the
adjudicator's re-assessment (not the claimant's); a duplicate group counts once per contestant.
Adjudication: blinded reproduction by a separate worker (repro/VERDICTS.md, 17 failing tests in repro/).

| | Opus 5.5 | gpt-6-astra |
|---|---|---|
| Findings submitted | 8 | 9 |
| CONFIRMED | 8 | 8 |
| REJECTED | 0 | 1 (F10: behaviour matches PHASE0-SPEC) |
| High (re-assessed) | 2 — F13 device_scope bypass on revision (overwrite of another device's private item), F11 revocation blocked by a slow-body request | 0 |
| **Score** | **15** | **10** |
| Wall time | 6 min 27 s | 9 min 4 s |
| Verdict | NO-GO | NO-GO |

Both independently found the same two root causes: G-A (correction copies unrecorded last_access_at → replay divergence) and
G-B (middleware pins a pooled connection + device-row lock for the whole request/stream). Judgment call disclosed: in G-B the
Opus finding was graded High because its described impact blocks revocation; the Astra finding (same root cause) was graded
Medium because its described impact is availability only. Scored as-described per protocol.
Unique finds: Opus F01, F03, F05, F06, F13, F16; Astra F04, F08, F09, F12, F15, F17.

All 14 distinct confirmed defects become the pre-VPS fix backlog (D-033).
