"""``hlm bench`` (W2f): the user-facing librarian model benchmark (D-017).

* ``v1`` / ``v2``: task packs, payloads, validators and deterministic scorers (stdlib only, shared
  with the legacy ``bench/run.py`` harness and the live gate ``eval/live/run.py``);
* ``runner`` / ``engine``: the run on the PRODUCTION path (provider, redaction, reservation);
* ``report``: summaries, the markdown report, McNemar, offline re-scoring;
* ``leaderboard``: the W-E leaderboard (``eval/results/``).

Keep this module import-free: ``bench/v2/tasks_v2.py`` imports ``hlmemo.bench.v2`` from a venv
that has no project dependencies.
"""
