"""AL5 / GOAL-PLAN B2: the SessionStart brief (D-207 redesign, D-225 "assist now").

An LLM-free, client-side Claude Code SessionStart hook that injects a SHORT, trustworthy memory brief:
the project card, the decisions and open items of the newest session notes, current lessons, the
pending-review count and the memory's "as of" date. Only verbatim memory lines (truncated with an
ellipsis, never summarised), every line with its handle, every superseded or non-current item excluded.

Modules: ``config`` (kill switch, ``[brief]`` table, cwd -> project via the capture mapping), ``fetch``
(read-only memory.query / memory.raw calls and the supersession filter), ``assemble`` (pure: snapshot
-> brief text within the token budget) and ``hook`` (the entry point: hard wall clock, fail-open).

Guiding principle (GOAL-PLAN): half-learning is worse than not knowing. On any doubt, say nothing.
"""
