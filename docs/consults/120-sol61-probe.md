# Sol 6.1 vs Sol 5.6 as the one-way-door reviewer: one probe on a known review (2026-10-08)

Owner, 2026-10-08: Sol 6.1 is out; it may replace Sol 5.6, after its quality is seen. Codex is used only this week, so
the probe reuses real work: the two review rounds of consults 118 and 119, with the same prompts and the same clean
exports. Codex CLI updated 0.155.1 → 0.161.0 first (`gpt-6.1-sol` answers). All runs at reasoning effort xhigh
(Astra at low); wall time includes the CLI.

| round | reviewer | time | tokens | verdict | valid findings | unique |
|---|---|---|---|---|---|---|
| 1 | Astra low | ~2 min | 50,914 | NO-GO | HIGH preview strips code between rules | — |
| 1 | Sol 5.6 xhigh | ~8 min | 147,264 | NO-GO | HIGH task list stripped; MEDIUM comment blocks the skip | the comment case |
| 1 | **Sol 6.1 xhigh** | 7.3 min | **82,899** | NO-GO | HIGH tab-indented code stripped; **MEDIUM preflight says nothing passed the threshold when the 3 warnings vanish in the re-check and a 4th above-TAU candidate is only in `unjudged`** | **the only finding in the risk path, missed by both others** |
| 2 | Astra low | ~1.7 min | 33,588 | NO-GO | HIGH a known key plus Markdown still stripped | — |
| 2 | Sol 5.6 xhigh | ~4 min | 67,706 | NO-GO | HIGH `--- ` delimiter differs from the importer; MEDIUM 40-line cap | both |
| 2 | **Sol 6.1 xhigh** | 5.5 min | **42,185** | NO-GO | HIGH a known key plus a task list (Astra's), its repro also using the `--- ` delimiter | — |

Read: on this sample, Sol 6.1 found the main defect class in both rounds and the one valid finding in the code that
matters most (the risk path), at about 40 % fewer tokens than Sol 5.6; it missed Sol 5.6's 40-line MEDIUM in round 2.
No false positives from either. Two rounds on one change is a small sample; it measures fit for this review style, not a
general ranking. All findings are fixed with tests (`119-risk-unjudged-triage.md`, commit e15ff22 for the Sol 6.1 one).
