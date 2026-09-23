"""CC-5 cassettes for G-LIVE-B: the W2b live-gate runner replays every recorded fixture call
(both profiles alone and the production chain) through the production prompts, payload builders
and D-067 guards with no network, and passes its thresholds. Proves parsing and the guard
pipeline, never model quality (that is the live run)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_glive_b_runner_replays_recorded_fixtures() -> None:
    spec = importlib.util.spec_from_file_location("run_w2b", ROOT / "eval" / "live" / "run_w2b.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rc = mod.main(
        [
            "--mode",
            "replay",
            "--reps",
            "1",
            "--max-usd",
            "0.01",
            "--out",
            "",
            "--cassette-dir",
            str(ROOT / "tests" / "cassettes" / "w2b"),
            "--profile",
            "openrouter-gpt6-luna",
            "--fallback",
            "openrouter",
            "--chain",
            "openrouter-gpt6-luna+openrouter",
        ]
    )
    assert rc == 0
