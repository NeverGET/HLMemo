"""Review 107 #4: the RUNBOOK's D-244 withdraw procedure fails CLOSED. Its checks, taken from the
RUNBOOK text exactly as documented, pass only on 298 pending = 18 kept + 280 withdrawn (disjoint,
covering the live pending set) and stop the script on any other count."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

RUNBOOK = Path(__file__).resolve().parents[2] / "deploy" / "RUNBOOK.md"


def _check(marker: str) -> str:
    """The ``python3 -c`` program of the RUNBOOK check that prints ``marker``."""
    (line,) = [
        ln for ln in RUNBOOK.read_text().splitlines() if ln.startswith("python3 -c '") and marker in ln
    ]
    return shlex.split(line.rstrip(" \\"))[2]


def _run(program: str, *args: object) -> int:
    return subprocess.run(
        [sys.executable, "-c", program, *map(str, args)], capture_output=True, text=True, timeout=30
    ).returncode


def _write(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data) if not isinstance(data, list) else "\n".join(data) + "\n")
    return path


def test_the_expected_counts_are_the_reviewed_ones() -> None:
    text = RUNBOOK.read_text()
    assert "EXPECT_PENDING=298 EXPECT_KEEP=18 EXPECT_WITHDRAW=280" in text
    assert "set -euo pipefail" in text and "2 real" not in text


@pytest.mark.parametrize(
    ("case", "ok"),
    [
        ("exact", True),
        ("keep_17", False),
        ("overlap", False),
        ("missing_one", False),
        ("extra_pending", False),
    ],
)
def test_the_id_check_needs_18_plus_280_covering_the_298(tmp_path: Path, case: str, ok: bool) -> None:
    ids = [f"{i:08d}-0000-4000-8000-000000000000" for i in range(299)]
    pending, keep, withdraw = ids[:298], ids[:18], ids[18:298]
    if case == "keep_17":
        keep, withdraw = ids[:17], ids[17:298]
    elif case == "overlap":
        withdraw = ids[17:297]  # one kept id also withdrawn, one pending id in neither file
    elif case == "missing_one":
        withdraw = ids[18:297]
    elif case == "extra_pending":
        pending = ids  # 299 live pending: a question the review never saw
    args = (
        _write(tmp_path / "pending.json", {"proposals": [{"question_id": q} for q in pending]}),
        _write(tmp_path / "keep.txt", keep),
        _write(tmp_path / "withdraw.txt", withdraw),
    )
    expect = re.search(r"EXPECT_PENDING=(\d+) EXPECT_KEEP=(\d+) EXPECT_WITHDRAW=(\d+)", RUNBOOK.read_text())
    assert expect is not None
    assert (_run(_check('print("ids"'), *args, *expect.groups()) == 0) is ok


def test_the_preview_withdraw_and_kept_checks_fail_closed(tmp_path: Path) -> None:
    preview = _check('print("preview", "PASS" if ok else "FAIL", d["withdrawn"], d["by_status"])')
    withdraw, kept = _check('print("withdraw"'), _check('print("kept"')
    good = {"dry_run": True, "withdrawn": 280, "by_status": {"accepted_pending": 280}, "event_id": None}
    assert _run(preview, _write(tmp_path / "p.json", good), 280) == 0
    assert _run(preview, _write(tmp_path / "p.json", {**good, "withdrawn": 279}), 280) != 0
    mixed = {**good, "by_status": {"accepted_pending": 279, "open": 1}}
    assert _run(preview, _write(tmp_path / "p.json", mixed), 280) != 0
    done = {**good, "dry_run": False, "event_id": 5720}
    assert _run(withdraw, _write(tmp_path / "w.json", done), 280) == 0
    assert _run(withdraw, _write(tmp_path / "w.json", {**done, "event_id": None}), 280) != 0
    assert _run(withdraw, _write(tmp_path / "w.json", {**done, "dry_run": True}), 280) != 0
    role = {"would_release": {"total": 18, "by_project": {"hlmemo": {"accepted_pending": 18, "approved": 0}}}}
    assert _run(kept, _write(tmp_path / "r.json", role), 18) == 0
    role["would_release"]["by_project"]["hlmemo"]["accepted_pending"] = 19
    assert _run(kept, _write(tmp_path / "r.json", role), 18) != 0
