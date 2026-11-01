"""`hlm init --user` and the settings loader agree on the user config directory (HLM_CONFIG_DIR)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hlmemo.cli.client_config import config_dir
from hlmemo.config import _toml_candidates


def test_the_settings_loader_reads_the_user_file_from_hlm_config_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home, cfg, work = tmp_path / "home", tmp_path / "cfg", tmp_path / "work"
    for d in (home, cfg, work):
        d.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("HLM_CONFIG_DIR", str(cfg))
    monkeypatch.delenv("HLM_CONFIG", raising=False)
    monkeypatch.chdir(work)
    (config_dir() / "hlm.toml").write_text('[client]\nserver_url = "https://example.test/mcp"\n')
    (home / ".config" / "hlm").mkdir(parents=True)
    (home / ".config" / "hlm" / "hlm.toml").write_text("[client]\n")

    assert _toml_candidates() == [cfg / "hlm.toml"]


def test_without_an_override_the_user_file_is_under_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("HLM_CONFIG_DIR", raising=False)
    monkeypatch.delenv("HLM_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    user = tmp_path / ".config" / "hlm" / "hlm.toml"
    user.parent.mkdir(parents=True)
    user.write_text("[client]\n")

    assert _toml_candidates() == [user]
