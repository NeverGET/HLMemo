"""The 0006 crash-injection hook can never fire outside tests: it needs HLM_TESTING=1 as well."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "0006_librarian.py"


def _module():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("mig_0006_librarian", MIGRATION)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("step", ["flush", "downgrade"])
def test_fault_variable_alone_is_ignored(monkeypatch: pytest.MonkeyPatch, step: str) -> None:
    mig = _module()
    monkeypatch.delenv("HLM_TESTING", raising=False)
    monkeypatch.setenv("HLM_MIGRATION_FAULT", f"0006:{step}")
    mig._fault(step)  # production: no effect
    monkeypatch.setenv("HLM_TESTING", "0")
    mig._fault(step)
    monkeypatch.setenv("HLM_TESTING", "1")
    with pytest.raises(RuntimeError, match=f"injected fault at {step}"):
        mig._fault(step)
