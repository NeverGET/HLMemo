"""GOAL-PLAN B1: session capture.

A Claude Code SessionEnd/PreCompact hook turns a session transcript into a short, grounded session
note (+ decisions, + explicit lessons) and writes it with ``memory.call_the_day``.

Modules: ``config`` (mapping, kill switch, paths), ``reduce`` (transcript -> <= 40k chars), ``scrub``
(secrets + emails, before any LLM call and before writing), ``summarize`` (``claude -p`` Haiku, strict
JSON, validation + fallback), ``write`` (the prod writer), ``run`` (the detached worker: lock, state,
pipeline) and ``hook`` (the < 1 s stdlib-only entry point).

Guiding principle (GOAL-PLAN): half-learning is worse than not knowing. Nothing that is not in the
transcript is written, uncertainty is explicit, every item carries provenance, and any failure writes
less (a minimal note) or nothing, never garbage.
"""
