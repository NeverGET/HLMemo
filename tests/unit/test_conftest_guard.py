"""D-056: the suite must refuse to truncate the dev stack's database."""

import pytest
from tests.conftest import PROTECTED_DB_NAMES, _refuse_protected


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
