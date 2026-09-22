"""Judge repro harness: reuse the project's DB fixtures. ALWAYS run with HLM_TEST_DSN=.../hlm_r1judge."""
import os

import pytest

assert os.environ.get("HLM_TEST_DSN", "").endswith("/hlm_r1judge"), "run with HLM_TEST_DSN=.../hlm_r1judge"

from tests.conftest import _clean_tables, connect, db_dsn  # noqa: E402,F401

pytestmark = pytest.mark.integration
