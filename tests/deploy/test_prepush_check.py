"""R4 (R-15, plan §2.3): deploy/scripts/prepush_check.sh, OFFLINE on throwaway repositories (never on
the real one: the orchestrator runs it at push time).

A clean repository with two refs PASSes every check; a planted (clearly fake, key-shaped) secret, a
private path or a .env file in the history, or a blob over 5 MB in the history FAILs the gate; the
planted secret never appears in the output. The fake secrets are assembled at run time so that no
key-shaped string is committed in this file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "scripts" / "prepush_check.sh"
FAKE_GITHUB = "ghp_" + "FakeTokenForTests" * 2 + "xy"  # 36 characters after the prefix
FAKE_GOOGLE = "AIza" + "FAKEkeyNotRealForTests" + "0123456789abc"  # 35 characters after the prefix
HAS_GITLEAKS = shutil.which("gitleaks") is not None
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        env=GIT_ENV,
        text=True,
        capture_output=True,
        check=True,
    ).stdout


def _commit(repo: Path, files: dict[str, str | bytes | None], message: str) -> None:
    for name, content in files.items():
        path = repo / name
        if content is None:
            _git(repo, "rm", "-q", "--", name)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
        _git(repo, "add", "--", name)
    _git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """main + r4-rc, clean: code, docs and a .env.example."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _commit(
        r,
        {
            "README.md": "# demo\n",
            "src/app.py": "API_KEY = os.environ['GEMINI_API_KEY']  # a risk- word, sk-or- in prose\n",
            ".env.example": "GEMINI_API_KEY=\n",
            "deploy/llm.env.example": "OPENROUTER_API_KEY=\n",
        },
        "init",
    )
    _git(r, "checkout", "-q", "-b", "r4-rc")
    _commit(r, {"src/feature.py": "print('r4')\n"}, "feature")
    _git(r, "checkout", "-q", "main")
    return r


def _run(repo: Path, *refs: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in GIT_ENV.items() if not k.startswith("GITLEAKS_")}
    return subprocess.run(
        ["bash", str(SCRIPT), "--repo", str(repo), *refs],
        env=env,
        text=True,
        capture_output=True,
        timeout=120,
    )


def _line(out: str, check: str) -> str:
    (line,) = [ln for ln in out.splitlines() if ln.split()[1:2] == [check]]
    return line


@pytest.mark.skipif(not HAS_GITLEAKS, reason="gitleaks is not installed")
def test_a_clean_repository_passes(repo: Path) -> None:
    res = _run(repo, "main", "r4-rc")
    assert res.returncode == 0, res.stdout + res.stderr
    for check in ("gitleaks", "private-paths", "large-blobs", "key-grep"):
        assert _line(res.stdout, check).startswith("PASS"), res.stdout
    assert res.stdout.rstrip().endswith("RESULT PASS")


def test_a_planted_fake_secret_fails_and_is_never_printed(repo: Path) -> None:
    _git(repo, "checkout", "-q", "r4-rc")
    _commit(repo, {"docs/notes.md": f"token: {FAKE_GITHUB}\nkey = {FAKE_GOOGLE}\n"}, "oops")
    res = _run(repo, "main", "r4-rc")
    assert res.returncode == 1 and res.stdout.rstrip().endswith("RESULT FAIL")
    assert _line(res.stdout, "key-grep").startswith("FAIL") and "r4-rc:docs/notes.md" in res.stdout
    if HAS_GITLEAKS:
        assert _line(res.stdout, "gitleaks").startswith("FAIL")
    blob = res.stdout + res.stderr
    assert FAKE_GITHUB not in blob and FAKE_GOOGLE not in blob and FAKE_GITHUB[4:20] not in blob


def test_a_secret_removed_later_still_fails_through_the_history(repo: Path) -> None:
    if not HAS_GITLEAKS:
        pytest.skip("gitleaks is not installed")
    _commit(repo, {"leak.txt": f"{FAKE_GITHUB}\n"}, "leak")
    _commit(repo, {"leak.txt": None}, "remove the leak")
    res = _run(repo, "main")
    assert res.returncode == 1 and _line(res.stdout, "gitleaks").startswith("FAIL")
    assert _line(res.stdout, "key-grep").startswith("PASS")  # the tree is clean; the history is not
    assert FAKE_GITHUB not in res.stdout + res.stderr


@pytest.mark.parametrize(
    ("files", "shown"),
    [
        ({"docs/private/notes.md": "private\n"}, "main:docs/private/notes.md"),
        ({"deploy/.local/vm/deploy.conf": "HOST=x\n"}, "main:deploy/.local/vm/deploy.conf"),
        ({".env": "GEMINI_API_KEY=\n"}, "main:.env"),
        ({"deploy/.env.prod": "X=1\n"}, "main:deploy/.env.prod"),
    ],
)
def test_a_private_path_in_the_tree_fails(repo: Path, files: dict[str, str], shown: str) -> None:
    _commit(repo, files, "private")
    res = _run(repo, "main", "r4-rc")
    assert res.returncode == 1 and _line(res.stdout, "private-paths").startswith("FAIL")
    assert shown in res.stdout


def test_a_private_path_deleted_later_fails_through_the_history(repo: Path) -> None:
    _commit(repo, {"docs/private/export.jsonl": "{}\n"}, "add")
    _commit(repo, {"docs/private/export.jsonl": None}, "delete")
    res = _run(repo, "main")
    assert res.returncode == 1 and "history:docs/private/export.jsonl" in res.stdout
    assert "main:docs/private" not in res.stdout  # not in the tree any more, only in the history


def test_a_blob_over_5_mb_in_the_history_fails(repo: Path) -> None:
    _commit(repo, {"data/big.bin": b"\0" * (5 * 1024 * 1024 + 1)}, "big")
    _commit(repo, {"data/big.bin": None}, "drop it")
    res = _run(repo, "main", "r4-rc")
    assert res.returncode == 1 and _line(res.stdout, "large-blobs").startswith("FAIL")
    assert "5242881 bytes  data/big.bin" in res.stdout
    exactly = _run(repo, "r4-rc")  # r4-rc never had it
    assert _line(exactly.stdout, "large-blobs").startswith("PASS")


def test_usage_errors(repo: Path, tmp_path: Path) -> None:
    assert _run(repo).returncode == 64  # no ref
    assert _run(repo, "no-such-ref").returncode == 64
    assert (
        subprocess.run(["bash", str(SCRIPT), "--repo", str(tmp_path), "main"], capture_output=True).returncode
        == 64
    )
