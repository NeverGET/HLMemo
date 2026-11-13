# 08: Human memory as a design source: reminder layers, and the brain's memory systems (2026-11-13)

A research note, not a decision. Cemal asked for layers of reminders in the notes that travel between his
machines: things that must be remembered, notes that come up when the place changes, a to-do list nobody has to
recite, lessons, and reminders that show when their topic comes up. The baseline report (`00`) covers what was
(retrospective memory); it says nothing about remembering to do something later. Psychology calls that
**prospective memory**, and its findings map onto this request closely.

## What the literature says
Read from the abstracts and excerpts of the sources at the end; the claims below stay within them.

1. **Two parts.** A prospective memory task has a retrospective part (what to do, and the cue) and a prospective
   part: bringing the intention up at the right moment with nobody asking (McDaniel & Einstein 2000, as
   summarised in [1]). Most everyday failures are of the second part.
2. **Three kinds of cue.** Event-based (do it when something appears), time-based (do it at a time or after an
   interval) and activity-based (do it at a natural break, after finishing something). Time-based intentions need
   self-started checking and lean more on executive control; they are the hard ones for people [2][3].
3. **Two ways an intention comes back** (the multiprocess framework [4][5]). *Monitoring*: keeping the intention
   active and watching for the cue, which costs attention in everything else. *Spontaneous retrieval*: the cue
   itself brings the intention back at no standing cost; this works when the cue is *focal*, that is, processed
   anyway as part of the ongoing work. It would be maladaptive to rely on monitoring alone.
4. **The two alternate** (the dynamic multiprocess framework [6]). With long delays and changing contexts people
   do not monitor at first; once a cue has fired in a context, they start monitoring *in that context*.
5. **If-then plans.** An intention stored as "if situation Y, then I do Z" links a concrete situation to the
   action and is carried out far more reliably than a bare goal [7]. The price: it helps in the specified
   situation and can hurt in situations the plan did not name [8].
6. **Finished intentions do not switch off by themselves.** A completed intention can still come up at its old
   cue, slow other work and even be carried out again (a commission error); deactivation is gradual and can fail
   [9][10]. One account: completion attaches a "stop tag" that is retrieved together with the intention [11].
7. **External reminders work, and are set by confidence, not need.** Offloading an intention raises the chance it
   is fulfilled; people set reminders when they *believe* they will forget (more under load and when
   interruption is likely), and sometimes more often than is optimal [12][13][14].

## What this means for a memory server
An agent's situation is harsher than a person's: between sessions it keeps nothing active. Every intention is
therefore offloaded by necessity, and monitoring is possible only at the points where code runs anyway. The
findings translate into rules:

- **R1 Store the cue with the intention** (5). A reminder is "when X, do Y", never a bare "do Y".
- **R2 Prefer cue-driven surfacing; keep the always-on layer tiny** (3). Whatever is shown at every session start
  costs attention in every session, as monitoring does.
- **R3 Make cues focal** (3). A cue should be something the session processes anyway: the folder it starts in,
  the project it works on, the package it is about to change, the date.
- **R4 Check at natural breaks** (2): session start, before a risky step, session close. These are the moments
  the server already owns (the brief, `risk_check`, `call_the_day`).
- **R5 After a cue has fired, widen the watch in that context** (4): once one reminder about a topic has come
  up in a session, related ones should be shown for the rest of it.
- **R6 Completion needs a stop tag** (6). A reminder that was dealt with must be closed by an explicit act with
  its evidence, or it will be acted on twice; "open" must be a stored state, not an impression.
- **R7 Offload when acting here and now is impossible or the delay is long** (7): another machine, another
  project, a blocked dependency. That is a rule for the writer, and it should not wait for a human to say it.
- **R8 Do not over-remind** (5, 7). A reminder narrows attention to its named situation, and people already set
  more reminders than they need. Each layer gets a small budget, and a reminder names how much it matters.

## Proposed layers
| Layer | Human analogue | Trigger | Where it surfaces | State today |
|---|---|---|---|---|
| L0 Standing rules | Habit, procedure | Always | `CLAUDE.md`, the protocol digest, the project card | Exists |
| L1 Must-remember | Monitoring (kept active) | Every session start in its scope | The brief, a few lines, hard cap | `pinned` exists on items; the brief does not use it for this |
| L2 Place-bound notes | Event cue: arriving somewhere | Session start on that machine or in that project | The brief | Stored (the notes project between the machines); shown only if the session remembers to look |
| L3 To-dos | Open intentions | Session start (count and the oldest), on demand (the list), a due date (time cue) | The brief; a list call | Missing as a state: no "open intention" kind, no due date |
| L4 Topic-cued reminders | If-then plans, spontaneous retrieval | The session touches the cue: a stack and version, a path, a keyword, a project | `risk_check`, and later a push at the same moments | Missing; `risk_check` matches lessons by similarity, not by a stated cue |
| L5 Lessons | Semantic memory of mistakes | A similar task | `risk_check`, the brief's lesson titles | Exists |

A reminder record needs, beyond an ordinary item: a **cue** (place, project, topic tags, due date), an
**importance** that decides its layer, a **state** (open, done, dropped) changed only by a closing item that
carries the evidence, and optionally an **expiry**, so that a reminder nobody closes does not live for ever.

## The wider picture: the brain's memory systems
Cemal's second pointer: the brain's own memory systems as the source. Each row is a finding and what it
suggests here; "today" names what HLMemo already does.

| The brain | Finding | HLMemo today | What it suggests |
|---|---|---|---|
| Several systems, not one store | Declarative memory splits into episodic (events in their context) and semantic (facts); beside it stand non-declarative systems: skills and habits, priming, conditioning [15][16] | `episode` and `fact`; `lesson` and `experience` for rules from mistakes; habits live outside memory, in `CLAUDE.md`, the digest and hooks | Keep the split, and treat *priming* as the missing one: a cue that makes related items easier to reach without a question being asked (the topic-cued layer L4) |
| Two learning speeds | The hippocampus stores new items at once without disturbing what is known; the neocortex learns slowly, finding structure across many experiences, through repeated reinstatement interleaved with other memories [17] | Fast: session notes, episodes, any write. Slow: the project card, curated facts, lessons promoted to the global project. The slow path is hand work | A consolidation pass that turns several episodes into, or updates, the slow layer |
| Offline consolidation | Recent memories are replayed during sleep and integrated into cortical networks, becoming more stable and resistant to interference; replay turns episodic memory into schema-like memory [18][19] | The librarian reviews each write as it arrives; nothing runs over a day's or a week's writes together | An idle-time job ("sleep"): replay the recent episodes of a project and propose card, fact and lesson updates. It is background work, so the background share of the spend caps (D-280) applies |
| Schemas | New information that fits an existing schema is consolidated very quickly; without a schema it is slow [20][21] | The card and the decision log are the schema; placement files a new item | Assimilate what fits the card at once, and send only what does not fit to review: the mismatch is the signal |
| Reconsolidation | Retrieving a memory makes it changeable; a prediction error at retrieval (the reminder does not match what is found) drives the update; old, strong memories are harder to change [22][23] | The protocol asks a session that reads an outdated memory to write `updates`; nothing prompts it | The moment of use is the moment to correct: when a session acts on a memory and reality differs, that mismatch should produce the correction, and a long-standing item should need stronger evidence |
| Tag and capture | A weak memory is kept if a salient event happens within hours before or after it; the strong event's resources are "captured" by the weak one's tag [24][25] | Importance is set per item by its writer; nothing looks at neighbours | When a lesson is registered or an incident recorded, keep and link the ordinary notes of that session and day: they are the context someone will need later |
| Encoding specificity | Recall is best when the cues at retrieval match the context stored at encoding [26] | The brief is keyed on the folder; items carry project, device scope and tags | Store more of the context (place, stack and version, files touched) and use the session's current context as the cue, not only its question |
| Forgetting has a function | Recalling one memory weakens competing similar ones, which keeps what is current reachable (reported from Wimber et al. 2015 in [27]; a press account, not the paper) | Nothing is deleted; superseded items are flagged, resolved lessons still rank beside active ones | Demote, do not delete: resolved and historical items should rank below active ones (readers asked for this on 2026-11-12) |
| Holding an intention while busy | Prospective memory, with the rostral prefrontal cortex involved in keeping an intention alive during other work [3] | Missing | The reminder layers above |

Working memory, the small active store, was not researched for this note; the token budgets of every read are
its counterpart here.

### What the comparison says we lack, in order of how often it would matter
1. **The prospective layer** (this note's first part): nothing remembers *to do*.
2. **Use-time correction:** a mismatch between a memory and what the session finds should update the memory.
3. **Context as the cue:** place, stack and files, beyond the folder.
4. **Offline consolidation:** a pass over the recent writes of a project, not only one write at a time.
5. **Demotion of what is resolved.**
6. **Salience capture:** keeping the neighbours of an important event.

## Open questions
- Whether L3 and L4 are a new item kind or a convention on existing kinds (tags plus `updates` for closing, as
  the notes project does now). A convention ships at once; a kind makes "open" queryable and cheap.
- How a topic cue is matched: exact tags (`next@15`) are deterministic and cheap; free-text cues need retrieval
  and inherit its misses. The Next.js example suggests starting with stack and version tags.
- The budget of L1 and of the brief's reminder section, and who may pin.
- Measuring it: a seeded set of intentions with cues, then count fired at the right moment, missed, fired when
  irrelevant, and acted on twice.

## Sources
1. Time- and event-based prospective memory in MCI and Alzheimer's disease, review (2025): https://pmc.ncbi.nlm.nih.gov/articles/PMC11965160
2. HED task catalogue, Prospective Memory Task (event-, time-, activity-based): https://www.hedtags.org/hed-resources/hed-task/tasks/hedtsk_prospective_memory.html
3. Time-based prospective memory: https://en.wikipedia.org/wiki/Time-based_prospective_memory
4. McDaniel & Einstein (2000), Strategic and automatic processes in prospective memory retrieval: https://profiles.wustl.edu/en/publications/strategic-and-automatic-processes-in-prospective-memory-retrieval
5. Scullin et al. (2010), Focal/nonfocal cue effects in prospective memory: https://pmc.ncbi.nlm.nih.gov/articles/PMC2864946
6. Scullin, McDaniel & Shelton (2013), The Dynamic Multiprocess Framework: https://pmc.ncbi.nlm.nih.gov/articles/PMC3809757
7. Gollwitzer (1999), Implementation intentions: strong effects of simple plans: https://www.prospectivepsych.org/sites/default/files/pictures/Gollwitzer_Implementation-intentions-1999.pdf
8. If-then planning, review (2020): https://www.tandfonline.com/doi/full/10.1080/10463283.2020.1808936
9. Scullin & Bugg (2013), Failing to forget: prospective memory commission errors: https://pmc.ncbi.nlm.nih.gov/articles/PMC3598897
10. Möschl et al. (2020), Aftereffects and deactivation of completed prospective memory intentions: https://psycnet.apa.org/record/2019-79375-001
11. The fate of completed intentions (stop-tag and deactivation accounts): https://bpb-us-e2.wpmucdn.com/sites.wustl.edu/dist/e/969/files/2017/09/The-fate-of-completed-intentions-2nnqsgt.pdf
12. Gilbert (2015), Strategic offloading of delayed intentions into the external environment: https://pmc.ncbi.nlm.nih.gov/articles/PMC4448673
13. Excessive use of reminders: metacognition and effort-minimisation (2020): https://www.sciencedirect.com/science/article/abs/pii/S1053810020300179
14. Interview with Boldt and Gilbert on intention offloading: https://featuredcontent.psychonomic.org/for-a-cognitive-boost-offload-it-interview-with-boldt-and-gilbert
15. Squire (2004), Memory systems of the brain: a brief history and current perspective: https://whoville.ucsd.edu/PDFs/384_Squire_%20NeurobiolLearnMem2004.pdf
16. The memory divisions of Tulving versus Squire (2024): https://inpact-psychologyconference.org/wp-content/uploads/2024/07/202401OP003.pdf
17. McClelland, McNaughton & O'Reilly (1995), Why there are complementary learning systems in the hippocampus and neocortex: https://pubmed.ncbi.nlm.nih.gov/7624455
18. Systems memory consolidation during sleep (2025): https://pmc.ncbi.nlm.nih.gov/articles/PMC12576410
19. Sleep: a brain state serving systems memory consolidation (2023): https://www.sciencedirect.com/science/article/pii/S0896627323002015
20. Tse et al. (2007), Schemas and memory consolidation: https://pubmed.ncbi.nlm.nih.gov/17412951
21. McClelland, Incorporating rapid neocortical learning of new schema-consistent information: https://stanford.edu/~jlmcc/papers/McClellandIP_JEPGRapidNeocorticalLearning.pdf
22. An update on memory reconsolidation updating (2017): https://pmc.ncbi.nlm.nih.gov/articles/PMC5605913
23. Sinclair & Barense (2019), Prediction error and memory reactivation: https://barense.psych.utoronto.ca/wp-content/uploads/2019/11/2019_Sinclair_Barense_TINs.pdf
24. Tag and capture: how salient experiences target and rescue nearby events in memory (2022): https://pmc.ncbi.nlm.nih.gov/articles/PMC9378568
25. Salient experiences enhance mundane memories through graded prioritization (2025): https://www.science.org/doi/10.1126/sciadv.ady1704
26. Encoding-specificity paradigm (Tulving & Thomson 1973), as summarised in: https://pmc.ncbi.nlm.nih.gov/articles/PMC7909183
27. Press report on Wimber et al. (2015), retrieval-induced forgetting: https://www.businessinsider.com/forgetting-is-a-crucial-part-of-memory-2015-3
