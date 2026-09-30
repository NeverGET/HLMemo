"""R4.1 review round 2 N-1: credential names with an explicit component (``API_KEY``, ``APIToken``...)
are detected even next to a non-secret component (``PRIMARY_API_KEY``); camelCase acronyms split right."""

from __future__ import annotations

import pytest

from hlmemo.importers import common

VAL = "Abcd" + "1234!"  # built at runtime: the literal would trip the repo secret scan

NAMES = [
    "PRIMARY_API_KEY", "CACHE_API_KEY", "APIToken", "JWTToken", "apiKey", "API_KEY", "MY_SERVICE_API_KEY",
    "HMAC_KEY", "SIGNING_KEY", "CLIENT_SECRET", "DB_PASSWORD", "GITHUB_TOKEN", "ACCESS_TOKEN", "REFRESH_TOKEN",
]  # fmt: skip


@pytest.mark.parametrize("name", NAMES)
def test_credential_name_hits(name: str) -> None:
    assert common.secret_hit(f"{name}={VAL}") == "env-secret-assignment"
    assert common.secret_hit(f"export {name} = '{VAL}'") == "env-secret-assignment"


def test_camel_acronym_split() -> None:
    assert common._secret_name("APIToken") and common._secret_name("JWTToken")


@pytest.mark.parametrize("name", ["PRIMARY_KEY", "CACHE_KEY", "TOKENIZER", "MAX_TOKENS", "SORT_KEY", "API_KEY_FILE"])
def test_non_credential_names_stay_clean(name: str) -> None:
    assert common.secret_hit(f"{name}={VAL}") is None
