"""D-056: the suite must refuse to truncate the dev stack's database. D-236: without HLM_TEST_DSN it
stops at once, with no fallback to any compose database."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from tests.conftest import MISSING_DSN, PROTECTED_DB_NAMES, _refuse_protected, _require_test_dsn


def test_dev_database_is_protected() -> None:
    assert "hlm" in PROTECTED_DB_NAMES


@pytest.mark.parametrize(
    "dsn",
    ["postgresql://hlm:hlm@127.0.0.1:5432/hlm", "host=127.0.0.1 port=5432 dbname=hlm user=hlm"],
)
def test_protected_dsn_is_refused(dsn: str) -> None:
    with pytest.raises(pytest.UsageError):
        _refuse_protected(dsn)


@pytest.mark.parametrize("name", ["hlm_verify", "hlm_test", "hlm_retr"])
def test_dedicated_databases_are_allowed(name: str) -> None:
    _refuse_protected(f"postgresql://hlm:hlm@127.0.0.1:5432/{name}")


# --------------------------------------------------------------------------- D-236: no DB fallback
@pytest.mark.parametrize("value", [None, "", "   "])
def test_a_missing_dsn_stops_the_session(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv("HLM_TEST_DSN", raising=False)
    else:
        monkeypatch.setenv("HLM_TEST_DSN", value)
    with pytest.raises(pytest.exit.Exception) as ei:
        _require_test_dsn()
    assert ei.value.returncode == pytest.ExitCode.USAGE_ERROR
    assert str(ei.value) == MISSING_DSN and "no fallback" in MISSING_DSN


def test_a_set_dsn_is_used_as_is(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HLM_TEST_DSN", "postgresql://hlm:hlm@127.0.0.1:1/hlm_verify")
    assert _require_test_dsn() == "postgresql://hlm:hlm@127.0.0.1:1/hlm_verify"


def test_a_run_without_dsn_fails_fast_and_never_touches_docker(tmp_path: Path) -> None:
    """The reproducer of the D-236 ACTIVE lesson: a DB test run without HLM_TEST_DSN used to start
    (or reuse) the compose stack's Postgres. Now it exits with code 4 before any database work, and
    a ``docker`` on PATH is never called."""
    marker = tmp_path / "docker-called"
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "docker").write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    (fake / "docker").chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "HLM_TEST_DSN"}
    env["PATH"] = f"{fake}{os.pathsep}{env.get('PATH', '')}"
    root = Path(__file__).resolve().parents[2]
    t0 = time.monotonic()
    proc = subprocess.run(  # noqa: S603 - a fixed pytest command line
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/unit/test_conftest_guard.py::test_dev_database_is_protected",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    out = proc.stdout + proc.stderr
    assert proc.returncode == pytest.ExitCode.USAGE_ERROR, out
    assert MISSING_DSN in " ".join(out.split())
    assert not marker.exists()
    assert time.monotonic() - t0 < 60
