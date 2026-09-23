"""bench v2 shim: the system prompt, messages, validators and scorers now live in ``hlmemo.bench.v2``
(W2f port, byte-identical scoring). ``bench/run.py`` (the legacy raw-API harness) imports this module;
it keeps the old ``default_packs(include_private=...)`` behaviour (public packs + the private pack when
present). Stdlib only: ``hlmemo.bench.v2`` has no third-party imports.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "src"))

from hlmemo.bench import v2 as _v2  # noqa: E402
from hlmemo.bench.v2 import *  # noqa: E402,F401,F403
from hlmemo.bench.v2 import (  # noqa: E402,F401
    FENCE_RE,
    SCORERS,
    T5_LABELS,
    T6_REL,
    T12_FINDING_KEYS,
    TURKISH_ONLY,
    alt_in,
    alts,
    injection_fired,
    jaccard,
    key_in,
    specific_tokens,
)

PUBLIC_DIR = _v2.PUBLIC_DIR
PRIVATE_DIR = REPO / "docs" / "private" / "bench-v2"


def default_packs(include_private: bool = True) -> list[Path]:
    out = _v2.default_packs()
    if include_private and PRIVATE_DIR.exists():
        out += sorted(PRIVATE_DIR.glob("*.json"))
    return out
