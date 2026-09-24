"""CC-5 cassettes for G-LIVE-B: the W2b live-gate runner replays every recorded fixture call
(both profiles alone and the production chain) through the production prompts, payload builders
and D-067 + v2 guards with no network, and passes its thresholds. Proves parsing and the guard
pipeline, never model quality (that is the live run). Both prompt versions are replayed: v2 (the
production default: both profiles alone and the chain) and v1 (pinned, the comparison run of
D-076's judgement v2: luna alone and the chain; the recorded deepseek-alone v1 run FAILS its gate —
JSON failures and supersession direction — and is kept as the evidence of that comparison, not
replayed here)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from hlmemo.librarian.prompts import pin_versions

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(("prompts", "fallback"), [("v2", "openrouter"), ("v1", "")])
def test_glive_b_runner_replays_recorded_fixtures(prompts: str, fallback: str) -> None:
    spec = importlib.util.spec_from_file_location("run_w2b", ROOT / "eval" / "live" / "run_w2b.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # the runner's dataclasses resolve their module
    spec.loader.exec_module(mod)
    try:
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
                "--prompts",
                prompts,
                "--cassette-dir",
                str(ROOT / "tests" / "cassettes" / "w2b"),
                "--profile",
                "openrouter-gpt6-luna",
                "--fallback",
                fallback,
                "--chain",
                "openrouter-gpt6-luna+openrouter",
            ]
        )
    finally:
        pin_versions(None)  # the runner pins process-wide
    assert rc == 0
