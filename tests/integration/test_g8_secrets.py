"""G8 secrets (VALIDATION-GATES): gitleaks clean over the repo history; local secret files untracked.

Runs on the host against the checkout (no DB, no stack). `test_gitleaks_clean` skips when the
`gitleaks` binary is not installed; `test_env_untracked` only needs git.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    """Override tests/conftest.py's autouse DB truncation: this module needs no database and must
    not wipe the developer's onboarding state (devices/projects) in the compose db."""
    yield


# Paths that must never be tracked (local secrets, model weights).
NEVER_TRACKED = (".env", ".hlm-dev.env", "models/")


def _git(*args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        pytest.skip(f"git {' '.join(args)} failed: {proc.stderr.strip()[:200]}")
    return proc.stdout


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks not installed")
def test_gitleaks_clean() -> None:
    proc = subprocess.run(
        ["gitleaks", "git", "--no-banner", "--exit-code", "3", "--redact", str(REPO_ROOT)],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    tail = (proc.stdout + proc.stderr).strip()[-2000:]
    assert proc.returncode == 0, f"gitleaks exit {proc.returncode}:\n{tail}"


def test_env_untracked() -> None:
    tracked = _git("ls-files").splitlines()
    # Files match exactly (".env.example" is the committed template and stays); dirs match by prefix.
    leaked = [
        p
        for p in tracked
        if any((p.startswith(pat) if pat.endswith("/") else p == pat) for pat in NEVER_TRACKED)
    ]
    assert not leaked, f"secret/model paths are tracked: {leaked}"


def test_secret_paths_gitignored() -> None:
    """`git check-ignore` must claim every NEVER_TRACKED path (so a future `git add -A` stays clean)."""
    for pat in NEVER_TRACKED:
        probe = pat.rstrip("/") + ("/x" if pat.endswith("/") else "")
        proc = subprocess.run(
            ["git", "check-ignore", "-q", probe], cwd=REPO_ROOT, capture_output=True, text=True, check=False
        )
        assert proc.returncode == 0, f"{pat} is not covered by .gitignore"
