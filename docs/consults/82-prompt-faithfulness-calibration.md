# Consult 82 — independent faithfulness calibration (D-158a, consult 81 condition)

Context: HLMemo's research librarian answers a coding agent's question with prose. Each sentence of the answer is attributed to source handles (memory items) that the agent can open. The release gate requires "faithful ≥ .95": each sentence must be stated by the sources it cites. An LLM judge scored one run's sentences. An internal audit, by the same model family as the implementer, disagreed with the judge on many rejections. You are the INDEPENDENT check. The files contain no judge or audit verdicts.

Files (in this directory):
- `packet/statements.jsonl`: 25 statements. Each has `k`, the `question`, the `statement`, and its `cited_sources` (handle, title, text of the cited chunk).
- `packet/corpus.jsonl`: the whole memory (91 items: handle `vN`, title, full body). Chunk handles `vN.M` belong to item `vN`.

For EACH statement, choose exactly one label:
- **S**: the cited sources (their text, or their title) state everything the statement claims.
- **W**: the cited ITEM states it, but outside the cited chunk (search the item's full body in corpus.jsonl).
- **E**: not in the cited sources, but another corpus item states it. Give its handle.
- **P**: partly supported. It adds an inference, a generalisation, a causal link, an actor, a qualifier or a value the sources do not state.
- **U**: no corpus item states it.
- **C**: a source says the opposite, including a dropped or added negation, or a superseded value presented as current.

Language note: statements may be Turkish while sources are English, and vice versa. Judge the meaning, not the wording. Identifiers must match.

Output ONLY a JSON-lines block, one line per statement: `{"k": <k>, "label": "S|W|E|P|U|C", "evidence": "<handle(s)>", "why": "<≤ 20 words>"}`. After the block, add ≤ 5 lines on any systematic pattern you noticed.
