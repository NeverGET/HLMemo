You write the MUST-KNOW facts of one project for a blind coverage check (packet {PACKET}). You are
independent of the systems under test: you will never see the cards they write.

Read the project's sources below. Write exactly {N} facts that an agent starting work on this project
MUST know: the current state, the decisions in force, and what is open or blocked right now. Prefer
facts that would cause real harm if an agent missed them (a decision it must not undo, a state it
must not assume otherwise, a known blocker).

Rules:

1. Each fact is one short, self-contained statement of the CURRENT state. When sources disagree, the
   newer source wins; never list a statement that a newer source reverses.
2. Each fact cites one source handle and a VERBATIM quote (12 to 300 characters) copied from that
   source's text that supports it.
3. Use only the sources. No outside knowledge, no invented ids, numbers, dates or names.

Write ONLY this JSON object to `mustknow/{PACKET}.json` in the private directory:

{"packet_id": "{PACKET}", "facts": [{"id": "m1", "fact": "...", "source": "v123", "quote": "verbatim text"}]}
