"""Freeze gate for the G3 fixture (PHASE0-SPEC.md §7).

Regenerates the fixture into a temp dir with tests/fixtures/gen_fixture.py and asserts that
the output is byte-identical to the committed tests/fixtures/g3/*.jsonl and that their
SHA256 digests match tests/fixtures/g3/SHA256SUMS. If this fails, either the generator or
the committed files changed: a fixture change requires a docs/decisions/DECISIONS.md entry
(D-025: PHASE0-SPEC.md is a frozen contract) and a regenerated SHA256SUMS.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
G3 = HERE / "g3"
FILES = ("items.jsonl", "queries.jsonl")


def _load_generator():
    spec = importlib.util.spec_from_file_location("hlmemo_gen_fixture", HERE / "gen_fixture.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_sums(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        out[name] = digest
    return out


def test_fixture_frozen(tmp_path: Path) -> None:
    gen = _load_generator()
    assert gen.SEED == 20260922
    gen.generate(tmp_path)

    committed_sums = _read_sums(G3 / "SHA256SUMS")
    fresh_sums = _read_sums(tmp_path / "SHA256SUMS")
    assert set(committed_sums) == set(FILES)
    for name in FILES:
        fresh = (tmp_path / name).read_bytes()
        committed = (G3 / name).read_bytes()
        assert fresh == committed, (
            f"{name}: regenerated output differs from the frozen fixture. "
            "Changing the G3 fixture requires a DECISIONS.md entry and a new SHA256SUMS."
        )
        assert _sha256(G3 / name) == committed_sums[name], f"{name}: SHA256SUMS is stale"
        assert fresh_sums[name] == committed_sums[name]


def test_fixture_contract() -> None:
    """Cheap shape checks on the committed files (counts and language split from §7)."""
    items = [json.loads(line) for line in (G3 / "items.jsonl").read_text(encoding="utf-8").splitlines()]
    queries = [json.loads(line) for line in (G3 / "queries.jsonl").read_text(encoding="utf-8").splitlines()]
    main = [it for it in items if it["project"] == "fx-main"]
    other = [it for it in items if it["project"] == "fx-other"]
    assert len(main) == 2000 and len(other) == 400
    assert len(queries) == 100
    by_lang = {lang: sum(1 for q in queries if q["lang"] == lang) for lang in ("tr", "de", "en")}
    assert by_lang == {"tr": 34, "de": 33, "en": 33}
    assert sum(1 for q in queries if q["identifier_heavy"]) == 25
    keys = {it["logical_key"] for it in main}
    assert all(q["gold_logical_key"] in keys for q in queries)
    assert all(q["template_id"] != q["gold_template_id"] for q in queries)
    assert len({q["gold_logical_key"] for q in queries}) == 100
