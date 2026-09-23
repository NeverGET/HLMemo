"""``eval/realdata/import_corpus.py --temporal-rule prod|eval`` (W1.5 item 6): the eval harness can
apply the production temporal rule; the default (eval) keeps the D-054/D-057 baselines."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / "eval" / "realdata" / "import_corpus.py"


@pytest.fixture(scope="module")
def ic():  # noqa: ANN201
    spec = importlib.util.spec_from_file_location("import_corpus_under_test", PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(mod)
    return mod


def test_default_rule_is_eval(ic) -> None:  # noqa: ANN001
    a = ic.parse_args(["--root", ".", "--project", "p", "--manifest", "m.jsonl"])
    assert a.temporal_rule == "eval"
    assert (
        ic.parse_args(
            ["--root", ".", "--project", "p", "--manifest", "m", "--temporal-rule", "prod"]
        ).temporal_rule
        == "prod"
    )


def test_prod_rule_takes_only_explicit_evidence(ic) -> None:  # noqa: ANN001
    fm = "---\ndate: 2026-03-05T10:00:00Z\n---\n# T\n\nbody\n"
    assert ic.prod_valid_from(fm, fm) == ("2026-03-05T10:00:00Z", "frontmatter")
    assert ic.prod_valid_from("# SESSION 2026-05-01 notes\n\nx\n", "x")[1] == "dated-heading"
    two = "# 2026-05-01 a\n\nx\n\n# 2026-05-02 b\n\ny\n"
    assert ic.prod_valid_from(two, two) == (None, "none")  # two dated entries in one piece: ambiguous
    one_row = "D-047 | 2026-09-23 | ACCEPTED | x | y | z\n"
    assert ic.prod_valid_from(one_row, one_row)[1] == "decision-row"
    two_rows = one_row + "D-048 | 2026-09-24 | ACCEPTED | x | y | z\n"
    assert ic.prod_valid_from(two_rows, two_rows) == (None, "none")  # ambiguous: import time
    assert ic.prod_valid_from("# Plain\n\ntext\n", "# Plain\n\ntext\n") == (None, "none")


def test_prod_rule_moves_file_dates_into_provenance(ic) -> None:  # noqa: ANN001
    items = ic.file_items("docs/a.md", "# Plain\n\ntext\n", "doc", "docs", "2026-01-01T00:00:00+00:00", 64000)
    assert ic.apply_prod_rule(items, "# Plain\n\ntext\n", "commit") == []
    assert items[0].valid_from is None
    assert (items[0].provenance, items[0].provenance_kind) == ("2026-01-01T00:00:00+00:00", "commit_date")
    future = (datetime.now(UTC) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = f"---\ndate: {future}\n---\n# F\n"
    items = ic.file_items("docs/f.md", text, "doc", "docs", "2026-01-01T00:00:00+00:00", 64000)
    assert [d for _it, d in ic.apply_prod_rule(items, text, "mtime")] == [future]
