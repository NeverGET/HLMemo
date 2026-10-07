"""The migration kit (`hlm migrate`, tools/migrate): spec, per-file batches, lint, seal, the run guard, recall
and the blind-check score. Everything runs on small fixtures through the REAL importer parse; no server is
contacted."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import random
import string
from pathlib import Path
from typing import Any

import pytest

from hlmemo.migrate import runner
from hlmemo.migrate import seal as sealmod
from hlmemo.migrate.batches import UNDATED, batch_counts, batch_keys, file_buckets, load, plan
from hlmemo.migrate.lint import lint
from hlmemo.migrate.recall import rank_of, recall
from hlmemo.migrate.spec import SpecError, load_spec

REPO = Path(__file__).resolve().parents[2]

FACT = """---
title: "API · the cache TTL is 300 seconds"
date: 2026-07-10
tags: [api]
source_path: src/cache.py
---
The API cache TTL is 300 seconds. Why: at 60 seconds the cache stampeded.
Evidence: src/cache.py:12, commit abc1234.
"""

LESSON = """---
title: "API · Measure the cache under peak load before changing its TTL"
date: 2026-07-12
tags: [api, active, python@3.12]
source_path: notes/cache.md
---
# API · Measure the cache under peak load before changing its TTL

## Mistake
When: the TTL was lowered without a load test and the cache stampeded.

## Fix
Do: run the load test first. Avoid: changing the TTL on a guess.

## Context
Evidence: "stampede at peak" (notes/cache.md:3). Scope: python@3.12. Status: active. Era: 2026-07.
"""

SESSIONS = """## 2026-07-30 API · first load test: p95 412 ms
The first load test at 50 rps gave p95 412 ms. Source: notes/load.md:4.

## 2026-08-02 API · second load test: p95 210 ms after the TTL change
The second load test gave p95 210 ms. Source: notes/load.md:9.
"""

DECISIONS = """D-001 | 2026-07-01 | ACCEPTED | Cache layer A for the API.
D-002 | 2026-08-05 | ACCEPTED | Cache layer B for the API — D-001'i geçersiz kılar.
"""


def _tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def _spec(tmp: Path, *, tz: str = "Europe/Istanbul", extra: str = "") -> Path:
    s = tmp / "migration.toml"
    s.write_text(
        f'slug = "kit-test"\ntz = "{tz}"\n{extra}\n[paths]\ncurated = "curated"\nprivate = "private"\n'
        '[tags]\nclosed = ["api"]\n'
        '[local]\nserver_url = "http://127.0.0.1:8799/mcp"\ndevice = "mig-kit-test"\n'
        '[prod]\nserver_url = "https://memory.example.org/mcp"\ndevice = "dev-1"\n',
        encoding="utf-8",
    )
    return s


@pytest.fixture
def good(tmp_path: Path) -> Path:
    _tree(
        tmp_path / "curated",
        {
            "status/api/api-cache-ttl.md": FACT,
            "lessons/api/api-load-test-first.md": LESSON,
            "sessions/api/api-2026-08.md": SESSIONS,
            "decisions/DECISIONS.md": DECISIONS,
        },
    )
    return _spec(tmp_path)


def _token(prefix: str, n: int) -> str:
    """A token-shaped string built at runtime, so no literal reaches secret scanners."""
    rng = random.Random(7)
    return prefix + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(n))


# --------------------------------------------------------------------------- spec
def test_example_spec_loads() -> None:
    s = load_spec(REPO / "tools" / "migrate" / "migration.example.toml")
    assert s.slug == "my-project" and s.tz == "Europe/Berlin"
    assert s.target("local").is_loopback and s.target("prod").device == "my-device"
    assert s.tag_allowed("api") and s.tag_allowed("active") and s.tag_allowed("python@3.12")
    assert not s.tag_allowed("random-topic")
    assert [src.importer for src in s.sources] == ["markdown"]


def test_spec_rejects_bad_values(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text('slug = "Bad Slug"\n[paths]\ncurated = "c"\n')
    with pytest.raises(SpecError, match="slug"):
        load_spec(bad)
    bad.write_text(
        'slug = "ok-slug"\n[paths]\ncurated = "c"\n'
        '[local]\nserver_url = "https://x.example/mcp"\ndevice = "d"\n'
    )
    with pytest.raises(SpecError, match="loopback"):
        load_spec(bad)
    bad.write_text('slug = "ok-slug"\n[paths]\ncurated = "c"\n[prod]\ndevice = "d"\n')
    with pytest.raises(SpecError, match="server_url"):
        load_spec(bad)
    bad.write_text('slug = "ok-slug"\n[paths]\ncurated = "c"\n[[sources]]\nimporter = "pdf"\npath = "x"\n')
    with pytest.raises(SpecError, match="importer"):
        load_spec(bad)


# --------------------------------------------------------------------------- batches
def test_batches_are_per_file_and_undated_last(good: Path) -> None:
    root = good.parent / "curated"
    _tree(
        root,
        {
            "status/api/api-undated.md": (
                '---\ntitle: "API · no date"\ntags: [api]\n---\nNo date at all here.\n'
            )
        },
    )
    spec = load_spec(good)
    loaded = load(spec)
    (ld,) = loaded
    buckets = {r.path: ld.bucket(r) for r in ld.parsed.records}
    # the session log spans July and August: both of its episodes land in the file's newest month
    log = [k for k in buckets if k.startswith("sessions/api/api-2026-08.md#")]
    assert len(log) == 2 and {buckets[k] for k in log} == {"2026-08"}
    # the decision log spans July and August too: one bucket for the whole file
    rows = [k for k in buckets if k.startswith("decisions/DECISIONS.md#")]
    assert len(rows) == 2 and {buckets[k] for k in rows} == {"2026-08"}
    assert buckets["status/api/api-cache-ttl.md"] == "2026-07"
    assert buckets["status/api/api-undated.md"] == UNDATED
    assert batch_keys(loaded) == ["2026-07", "2026-08", UNDATED]
    assert sum(batch_counts(loaded).values()) == len(ld.parsed.records)
    assert [r["batch"] for r in plan(loaded)] == ["2026-07", "2026-08", UNDATED]


def test_month_is_read_in_the_spec_zone(tmp_path: Path) -> None:
    from hlmemo.importers.common import ImportRecord

    rec = ImportRecord(
        system="markdown",
        path="a.md",
        file="a.md",
        sha256="0" * 64,
        title="t",
        body="b",
        kind_guess="fact",
        tags=[],
        evidenced_valid_from="2026-07-31T21:00:00Z",
    )
    # 2026-08-01 date-only evidence in UTC+3 is 07-31T21:00Z: the bucket must be August, not July
    assert file_buckets([rec], "Europe/Istanbul") == {"a.md": "2026-08"}
    assert file_buckets([rec], "UTC") == {"a.md": "2026-07"}


# --------------------------------------------------------------------------- lint
def test_lint_passes_a_good_tree_and_previews_the_links(good: Path) -> None:
    spec = load_spec(good)
    res = lint(spec, load(spec))
    assert res.errors == [], res.errors
    assert res.summary["kinds"] == {"fact": 3, "lesson": 1, "episode": 2}
    assert {(x["newer"], x["older"]) for x in res.links} == {("D-002", "D-001")}


def test_lint_catches_the_preamble_trap(good: Path) -> None:
    root = good.parent / "curated"
    (root / "sessions/api/api-2026-08.md").write_text(
        "---\ndate: 2026-08-02\n---\n# API sessions\n\n" + SESSIONS
    )
    (root / "decisions/DECISIONS.md").write_text("# Decisions\n" + DECISIONS)
    spec = load_spec(good)
    errs = "\n".join(lint(spec, load(spec)).errors)
    assert "sessions/api/api-2026-08.md: text above the first dated heading" in errs
    assert "decisions/DECISIONS.md: text above the first decision row" in errs
    assert "a preamble item" in errs  # the importer really makes the extra item


def test_lint_rules_on_lessons_tags_secrets_names_and_dates(good: Path) -> None:
    root = good.parent / "curated"
    _tree(
        root,
        {
            "lessons/api/api-bad-lesson.md": LESSON.replace("## Fix", "## Repair")
            .replace("tags: [api, active, python@3.12]", "tags: [api, active, historical, python@3.12]")
            .replace("api-load", "api-bad"),
            "status/api/api-off-list.md": FACT.replace("tags: [api]", "tags: [api, marketing]"),
            "status/api/api-key.md": FACT + "The key is " + _token("gh" + "p_", 36) + " today.\n",
            "status/other/api-cache-ttl.md": FACT,
            "status/api/api-estimated.md": FACT.replace("tags: [api]", "tags: [api, date-estimated]"),
        },
    )
    spec = load_spec(good)
    errs = "\n".join(lint(spec, load(spec)).errors)
    assert "api-bad-lesson.md: lesson without a `## Fix` section" in errs
    assert "active + historical is a contradiction" in errs
    assert "tag 'marketing' is not in the spec's closed tag list" in errs
    assert "api-key.md: token-shaped string (rule github-token) at line 9" in errs
    assert "file name 'api-cache-ttl.md' is not unique" in errs
    assert "`date-estimated` tag without the marker line 'Date estimated'" in errs


def test_lint_warns_on_yerine_next_to_a_decision_id(good: Path) -> None:
    root = good.parent / "curated"
    (root / "decisions/DECISIONS.md").write_text(
        DECISIONS + "D-003 | 2026-08-09 | ACCEPTED | Cache layer C, D-002 yerine yalnız okuma yolunda.\n"
    )
    spec = load_spec(good)
    warns = "\n".join(lint(spec, load(spec)).warnings)
    assert "'yerine' next to a D-id reads as a full reversal" in warns


# --------------------------------------------------------------------------- seal
def test_seal_write_verify_tamper_and_no_silent_reseal(good: Path) -> None:
    spec = load_spec(good)
    loaded = load(spec)
    with pytest.raises(sealmod.SealError, match="expected batch counts"):
        sealmod.write(spec, loaded, expect={"2026-07": 99})
    out = sealmod.write(spec, loaded, expect=batch_counts(loaded))
    assert out["total_items"] == 6 and spec.seal_path.stat().st_mode & 0o777 == 0o600
    assert sealmod.verify(spec, loaded).ok
    with pytest.raises(sealmod.SealError, match="exists"):
        sealmod.write(spec, loaded)
    p = spec.curated_dir / "status/api/api-cache-ttl.md"
    p.write_text(p.read_text() + "One more sentence.\n")
    v = sealmod.verify(spec, load(spec))
    assert not v.ok and any("tree differs" in x for x in v.problems)
    assert any("changed 0:status/api/api-cache-ttl.md" in x for x in v.problems)
    assert sealmod.write(spec, load(spec), force=True)["tree_sha256"] != out["tree_sha256"]


# --------------------------------------------------------------------------- run guard
class FakeImporter:
    """Answers per (batch, dry_run) with scripted counts; records the call order."""

    def __init__(
        self, dry: dict[str, dict[str, int]] | None = None, apply: dict[str, dict[str, Any]] | None = None
    ):
        self.dry, self.apply, self.calls = dry or {}, apply or {}, []

    async def __call__(
        self, call: Any, importer: str, parsed: Any, slug: str, dry_run: bool
    ) -> dict[str, Any]:
        n = len(parsed.records)
        key = sorted(
            {r.evidenced_valid_from[:7] if r.evidenced_valid_from else UNDATED for r in parsed.records}
        )[-1]
        self.calls.append((key, "dry" if dry_run else "apply", n))
        if dry_run:
            return {"counts": self.dry.get(key, {"new": n}), "token_estimate": {"tokens": 10}}
        rep = self.apply.get(key, {"counts": {"new": n}, "written": n, "failed": 0})
        return {"counts": rep["counts"], "writes": {"written": rep["written"], "failed": rep["failed"]}}


@contextlib.asynccontextmanager
async def fake_session(_target: Any):
    async def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("the fake importer never calls the server")

    yield call


def _run(spec: Any, loaded: Any, target: str, imp: FakeImporter, **kw: Any) -> list[dict[str, Any]]:
    return asyncio.run(runner.run(spec, loaded, target, session=fake_session, importer=imp, **kw))


def test_apply_runs_the_same_batch_dry_first(good: Path) -> None:
    spec = load_spec(good)
    loaded = load(spec)
    imp = FakeImporter()
    _run(spec, loaded, "local", imp, apply=True)
    assert [(k, m) for k, m, _ in imp.calls] == [
        ("2026-07", "dry"),
        ("2026-07", "apply"),
        ("2026-08", "dry"),
        ("2026-08", "apply"),
    ]


def test_a_changed_count_stops_before_the_apply(good: Path) -> None:
    spec = load_spec(good)
    loaded = load(spec)
    imp = FakeImporter(dry={"2026-08": {"new": 3, "changed": 1}})
    with pytest.raises(runner.HardStop, match="2026-08"):
        _run(spec, loaded, "local", imp, apply=True)
    assert ("2026-08", "apply") not in {(k, m) for k, m, _ in imp.calls}


def test_apply_failures_stop_the_run(good: Path) -> None:
    spec = load_spec(good)
    loaded = load(spec)
    imp = FakeImporter(apply={"2026-07": {"counts": {"new": 2}, "written": 1, "failed": 1}})
    with pytest.raises(runner.HardStop) as exc:
        _run(spec, loaded, "local", imp, apply=True)
    assert "write_failed" in str(exc.value) and len(exc.value.report) == 2
    assert all(k != "2026-08" for k, _m, _n in imp.calls)


def test_resume_allows_unchanged_and_verify_expects_it(good: Path) -> None:
    spec = load_spec(good)
    loaded = load(spec)
    partly = FakeImporter(dry={"2026-07": {"new": 1, "unchanged": 1}})
    with pytest.raises(runner.HardStop):
        _run(spec, loaded, "local", partly, apply=True)
    _run(
        spec,
        loaded,
        "local",
        FakeImporter(dry={"2026-07": {"new": 1, "unchanged": 1}}),
        apply=True,
        resume=True,
    )
    _run(
        spec,
        loaded,
        "local",
        FakeImporter(dry={"2026-07": {"unchanged": 2}, "2026-08": {"unchanged": 4}}),
        verify=True,
    )
    with pytest.raises(runner.HardStop):
        _run(spec, loaded, "local", FakeImporter(), verify=True)


def test_prod_needs_the_env_and_a_matching_seal(good: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = load_spec(good)
    loaded = load(spec)
    monkeypatch.delenv(runner.ALLOW_PROD_ENV, raising=False)
    with pytest.raises(runner.RunRefused, match=runner.ALLOW_PROD_ENV):
        _run(spec, loaded, "prod", FakeImporter())
    monkeypatch.setenv(runner.ALLOW_PROD_ENV, "1")
    with pytest.raises(runner.RunRefused, match="seal"):
        _run(spec, loaded, "prod", FakeImporter())
    sealmod.write(spec, loaded, expect=batch_counts(loaded))
    assert _run(spec, loaded, "prod", FakeImporter())
    p = spec.curated_dir / "status/api/api-cache-ttl.md"
    p.write_text(p.read_text() + "x\n")
    with pytest.raises(runner.RunRefused, match="does not match its seal"):
        _run(spec, load(spec), "prod", FakeImporter())


def test_unknown_batch_and_flag_combinations_are_refused(good: Path) -> None:
    spec = load_spec(good)
    loaded = load(spec)
    with pytest.raises(runner.RunRefused, match="unknown batch"):
        _run(spec, loaded, "local", FakeImporter(), batch="1999-01")
    with pytest.raises(runner.RunRefused):
        _run(spec, loaded, "local", FakeImporter(), apply=True, verify=True)
    with pytest.raises(runner.RunRefused):
        _run(spec, loaded, "local", FakeImporter(), resume=True)


def test_hard_stop_counts() -> None:
    assert runner.hard_stop({"counts": {"new": 3, "missing": 2}}, "dry") == {}
    assert runner.hard_stop({"counts": {"new": 3, "closed": 1}}, "dry") == {"closed": 1}
    assert runner.hard_stop({"counts": {"new": 3}, "written": 2, "failed": 0}, "apply") == {
        "written_vs_new": "2!=3"
    }
    assert runner.hard_stop({"counts": {"skipped": 1}}, "apply") == {"skipped": 1}


# --------------------------------------------------------------------------- recall
def test_recall_ranks_by_the_quoted_file() -> None:
    hits = [
        {"title": "Something else · status/api/x.md"},
        {"title": "API · cache TTL · status/api/api-cache-ttl.md"},
    ]
    assert rank_of(hits, {"status/api/api-cache-ttl.md"}) == 2
    assert rank_of(hits, {"lessons/api/nothing.md"}) is None

    async def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        assert tool == "memory.query" and args["project"] == "kit-test"
        return {"hits": hits, "evidence": "matched"}

    rows = [
        {
            "id": "T1",
            "question": "q1",
            "category": "fact",
            "quotes": [{"file": "status/api/api-cache-ttl.md"}],
        },
        {"id": "T2", "question": "q2", "category": "negative", "quotes": []},
    ]
    out = asyncio.run(recall(call, "kit-test", rows, k=5))
    assert (out["hit"], out["scored"], out["rows"][0]["rank"]) == (1, 1, 2)


# --------------------------------------------------------------------------- blind-check score
def _blindcheck() -> Any:
    path = REPO / "tools" / "migrate" / "blindcheck" / "blindcheck.py"
    spec = importlib.util.spec_from_file_location("blindcheck", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_blindcheck_score_takes_the_stricter_grade() -> None:
    bc = _blindcheck()
    key = {
        f"C{i}": {
            "qid": f"p:T{i}",
            "answerable": i < 9,
            "category": "fact" if i < 9 else "negative",
            "lang": "en",
        }
        for i in range(10)
    }
    g1 = {c: {"grade": "correct" if k["answerable"] else "abstention"} for c, k in key.items()}
    g2 = json.loads(json.dumps(g1))
    out = bc.score(key, g1, g2, 0.80)
    assert out["PASS"] and out["correct_rate"] == 1.0
    g2["C0"] = {"grade": "superseded_as_current"}  # one grader sees a stale value stated as current
    out = bc.score(key, g1, g2, 0.80)
    assert not out["PASS"] and out["superseded"] == 1 and out["disagree"] == 1
    g2 = json.loads(json.dumps(g1))
    g2["C9"] = {"grade": "fabricated"}
    assert not bc.score(key, g1, g2, 0.80)["PASS"]
