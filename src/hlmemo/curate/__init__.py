"""``hlm curate``: the owner-run, LOCAL supersession-curation pipeline (D-240, D-244).

export -> candidates (librarian proposals or a mapping pass) -> pass 1 (N verifier agents) -> build
``hlm links backfill`` records -> deterministic gate -> pass 2 (refuting agents) -> FIXes + re-gate ->
authority filter -> preview bundle (``final.jsonl``, ``held.jsonl``, ``REVIEW.md``, ``summary.json``)
-> with ``--apply``: the RUNBOOK ``hlm links backfill`` commands.

Everything except the agent passes is deterministic, pure Python (``gate``, ``bundle``). The agent
command is configuration (D-017): a shell-style template run once per slice, the prompt on STDIN.
"""
