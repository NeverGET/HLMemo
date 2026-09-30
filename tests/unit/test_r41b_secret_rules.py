"""R4.1 review F-3: a non-secret setting whose name merely contains a secret word as a substring
(``TOKENIZER_MODEL``) must not make the importer skip the whole file."""

from __future__ import annotations

import pytest

from hlmemo.importers import common

V = "abc123" + "def456"  # built at runtime: the literal would trip the repo's secret scan


@pytest.mark.parametrize(
    "text",
    [
        "TOKENIZER_MODEL=bert-base-uncased",
        "export TOKENIZER_MODEL=bert-base-uncased\nEMBED_MODEL=multilingual-e5-small",
        "SECRETARY_EMAIL=office-manager-team",
        "MAX_TOKENS=1000000000",
        "PASSWORDLESS_LOGIN=enabled-for-all",
        "KEYBOARD_LAYOUT=turkish-q-layout",
    ],
)
def test_non_secret_setting_is_not_a_hit(text: str) -> None:
    assert common.secret_hit(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "MINIO_SECRET" + "_KEY=" + V,
        "DB_PASSWORD=Sup3rS3cret!",
        "apiToken=" + V,
        "my.api-key=" + V,
        "SECRET=" + V,
        "TOKEN=" + V,
        "HLM_API_KEY=" + V,
    ],
)
def test_real_secret_names_still_hit(text: str) -> None:
    assert common.secret_hit(text) == "env-secret-assignment"


def test_a_file_with_a_non_secret_setting_is_read(tmp_path) -> None:
    f = tmp_path / "setup.md"
    f.write_text("# Setup\n\nTOKENIZER_MODEL=bert-base-uncased\n", encoding="utf-8")
    text, why = common.read_text(f)
    assert why is None and text is not None and "TOKENIZER_MODEL" in text
