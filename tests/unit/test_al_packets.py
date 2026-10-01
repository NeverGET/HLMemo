"""Active-librarian ceiling harness (eval/active): packet builders and guards, on synthetic rows."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

AL_DIR = Path(__file__).resolve().parents[2] / "eval" / "active"
if str(AL_DIR) not in sys.path:
    sys.path.insert(0, str(AL_DIR))

import al_common as C  # noqa: E402
import al_packets as P  # noqa: E402

T0 = datetime(2026, 9, 1, tzinfo=UTC)
T1 = datetime(2026, 9, 20, tzinfo=UTC)
SECRET = "sk-or-v1-" + "a" * 40

NOTE_BODY = (
    "Session 2026-09-20\n\nAUTO-CAPTURED session note; not reviewed.\n\nWe changed the retry policy.\n\n"
    "## Uncertain / unverified\n- The new cache may be slower under load.\n"
    "- Whether the nightly job still runs.\n\n"
    "## Decisions\n- Retries use exponential backoff capped at 8 seconds.\n"
    "- The worker reads its key from api_key = " + SECRET + " only.\n"
    "- A long decision that wraps\n  onto a second line."
)


def _note(**kw):
    row = {"handle": "v10", "project_id": 1, "title": "Session s1", "body": NOTE_BODY, "valid_from": T1}
    row.update(kw)
    return row


def _cand(vid, kind="fact", project_id=1, body="Retries use a fixed delay of 2 seconds."):
    return {
        "version_id": vid,
        "kind": kind,
        "project_id": project_id,
        "title": f"t{vid}",
        "body": body,
        "valid_from": T0,
    }


def test_split_note_sections():
    s = P.split_note(NOTE_BODY)
    assert s.decisions[0] == "Retries use exponential backoff capped at 8 seconds."
    assert s.decisions[2] == "A long decision that wraps onto a second line."
    assert s.uncertain == ["The new cache may be slower under load.", "Whether the nightly job still runs."]
    assert "We changed the retry policy." in s.notes and "Decisions" not in s.notes


def test_split_note_without_sections():
    s = P.split_note("Session x\n\njust notes\n## Other\n- a bullet")
    assert s.decisions == [] and s.uncertain == []
    assert "- a bullet" in s.notes


def test_e1_packet_shape_redaction_and_doc_drop():
    cands = [(_cand(20), False), (_cand(21, kind="doc_chunk"), False), (_cand(22, project_id=2), True)]
    pk = P.e1_packet("E1-001", _note(), cands, slugs={1: "alpha", 2: "beta"}, candidate_chars=1500)
    assert pk is not None
    handles = [s["handle"] for s in pk["sources"]]
    assert handles == ["v10", "v20", "v22"]  # the reserved document candidate is dropped
    assert pk["sources"][0]["role"] == "subject" and pk["sources"][2]["cross_project"] is True
    assert SECRET not in pk["user"] and SECRET not in pk["sources"][0]["text"]
    assert "REDACTED" in pk["sources"][0]["text"]
    assert "DECISION LINES of v10" in pk["user"]
    assert "- Retries use exponential backoff capped at 8 seconds." in pk["user"]
    assert pk["user_sha256"] == C.sha256_text(pk["user"])
    again = P.e1_packet("E1-001", _note(), cands, slugs={1: "alpha", 2: "beta"}, candidate_chars=1500)
    assert again["user_sha256"] == pk["user_sha256"]  # deterministic


def test_e1_packet_none_without_decisions_and_later_notes_hidden():
    assert (
        P.e1_packet("E1-1", _note(body="Session\n\nnothing decided"), [], slugs={1: "a"}, candidate_chars=10)
        is None
    )
    later = [
        {"handle": "v99", "valid_from": T1, "body": "## Decisions\n- Retries were reverted to a fixed delay."}
    ]
    pk = P.e1_packet("E1-1", _note(), [], slugs={1: "a"}, candidate_chars=10, later_notes=later)
    assert pk["grader_context"]["later_notes"][0]["decisions"] == ["Retries were reverted to a fixed delay."]
    assert "reverted" not in pk["user"]  # readers only, never the model


def test_e1_candidate_text_is_cut_after_redaction():
    long = "word " * 1000
    pk = P.e1_packet("E1-1", _note(), [(_cand(20, body=long), False)], slugs={1: "a"}, candidate_chars=100)
    assert pk["sources"][1]["text"].endswith(" …") and len(pk["sources"][1]["text"]) == 102


def test_dry_run_note_body_matches_close_layout():
    note = P.dry_run_note(
        {
            "project": "alpha",
            "notes": "Session x",
            "decisions": ["Use A."],
            "occurred_at": "2026-09-30T10:00:00Z",
        },
        3,
    )
    assert note["handle"] == "x3" and note["body"].endswith("\n\n## Decisions\n- Use A.")
    assert P.split_note(note["body"]).decisions == ["Use A."]


def test_e2_packet_card_is_context_only():
    card = {"version_id": 5, "body": "# Alpha\nOld card text", "valid_from": T0, "skeleton": False}
    notes = [{"version_id": 11, "title": "Session", "body": NOTE_BODY, "valid_from": T1}]
    items = [_cand(20), _cand(21, kind="lesson")]
    pk = P.e2_packet(
        "E2-001",
        "alpha",
        card=card,
        card_stale=True,
        memory_map="docs/\n  a.md v1",
        notes=notes,
        items=items,
        note_chars=2500,
        item_chars=700,
    )
    assert [s["handle"] for s in pk["sources"]] == ["v11", "v20", "v21"]
    assert pk["context"]["previous_card_handle"] == "v5" and "v5" not in {s["handle"] for s in pk["sources"]}
    assert "Old card text" in pk["user"] and "FLAGGED STALE" in pk["user"]
    assert pk["context"]["newest_source"] == "2026-09-20"


def test_select_e2_projects_threshold():
    assert P.select_e2_projects([(1, "b", 20), (2, "a", 19), (3, "c", 50)], 20) == [(1, "b"), (3, "c")]


def test_e3_packet_counts_projects():
    rows = [
        {
            "version_id": 1,
            "kind": "lesson",
            "slug": "a",
            "title": "x",
            "body": "Always pin versions.",
            "valid_from": T0,
        },
        {
            "version_id": 2,
            "kind": "lesson",
            "slug": "b",
            "title": "y",
            "body": "Pin versions in CI.",
            "valid_from": T0,
        },
        {
            "version_id": 3,
            "kind": "experience",
            "slug": "b",
            "title": "z",
            "body": "Pin it.",
            "valid_from": T0,
        },
    ]
    pk = P.e3_packet("E3-001", rows, lesson_chars=1200)
    assert pk["context"]["projects"] == ["a", "b"]
    assert pk["meta"]["lessons_per_project"] == {"a": 1, "b": 2}
    assert "PROJECTS (2): a, b" in pk["user"]


def test_stratified_sample_deterministic_and_balanced():
    rows = [{"id": i, "stratum": "curated", "project": "big"} for i in range(50)]
    rows += [{"id": 100 + i, "stratum": "curated", "project": f"p{i % 3}"} for i in range(9)]
    rows += [{"id": 200 + i, "stratum": "imported", "project": "h"} for i in range(5)]
    a = P.stratified_sample(rows, key="stratum", per_stratum=8, total=12, seed=7, sub_key="project")
    b = P.stratified_sample(rows, key="stratum", per_stratum=8, total=12, seed=7, sub_key="project")
    assert [r["id"] for r in a] == [r["id"] for r in b]
    assert len(a) == 12
    cur = [r for r in a if r["stratum"] == "curated"]
    assert {r["project"] for r in cur} == {"big", "p0", "p1", "p2"}  # round-robin, not all "big"
    assert sum(r["stratum"] == "imported" for r in a) == 5  # all of the small stratum
    assert len(cur) == 7  # quotas 8 + 5 over the total 12: the larger quota is trimmed
    filled = P.stratified_sample(rows, key="stratum", per_stratum=3, total=12, seed=7, sub_key="project")
    assert len(filled) == 12  # 3 + 3, then the leftovers fill the total


def test_e0_unit_hides_model_confidence_and_flags_hiding():
    proposal = {
        "question_id": "q1",
        "kind": "contradiction",
        "project": "alpha",
        "auto_class": False,
        "subjects": [
            {"clue": "v1", "kind": "fact", "projects": ["alpha"]},
            {"clue": "v2", "kind": "fact", "projects": ["alpha"]},
        ],
    }
    raw = {
        "relation": "contradicts",
        "supersedes": "new",
        "confidence": "high",
        "tier": "action",
        "reason": "v1 replaces v2",
        "quotes": {"new": "a", "old": "b"},
        "verification": {"agreed": True},
        "actions": [
            {"op": "link_insert", "rel": "supersedes", "props": {"scope": "whole"}},
            {"op": "version_close", "valid_to": "2026-09-01"},
        ],
    }
    subj = {
        1: {"body": "new " + SECRET, "kind": "fact", "valid_from": T1, "current": True},
        2: {"body": "old", "current": True},
    }
    u = P.e0_unit(proposal, raw, subj, stratum="curated")
    assert "confidence" not in u["view"] and "tier" not in u["view"] and "verification" not in u["view"]
    assert u["hidden"]["confidence"] == "high" and u["hidden"]["hiding"] is True
    assert SECRET not in str(u["view"])
    assert P.e0_hiding({"actions": [{"op": "link_insert", "rel": "supersedes", "scope": "part"}]}) is False
    assert P.e0_stratum({"project": "hlmemo"}, "hlmemo") == "imported"


def test_ceiling_dsn_refuses_other_databases(monkeypatch):
    monkeypatch.delenv(C.ANY_DB_ENV, raising=False)
    assert C.ceiling_dsn("postgresql://u:p@127.0.0.1:55432/hlm_al_ceiling").endswith("/hlm_al_ceiling")
    assert C.ceiling_dsn("host=x dbname=hlm_al_ceiling user=u")
    for bad in (
        "postgresql://u:p@127.0.0.1:5432/hlm",
        "postgresql://u:p@db/hlm_al_ceiling_x",
        "host=x dbname=hlm",
    ):
        with pytest.raises(C.HarnessError):
            C.ceiling_dsn(bad)


def test_ensure_private_refuses_outside(monkeypatch, tmp_path):
    monkeypatch.setenv(C.PRIVATE_ENV, str(tmp_path / "priv"))
    assert C.ensure_private(tmp_path / "priv" / "a" / "b.json").name == "b.json"
    with pytest.raises(C.HarnessError):
        C.ensure_private(tmp_path / "elsewhere.json")
    with pytest.raises(C.HarnessError):
        C.write_text(tmp_path / "priv" / ".." / "escape.txt", "x")


def test_private_dir_inside_repo_must_be_gitignored(monkeypatch):
    monkeypatch.setenv(C.PRIVATE_ENV, str(C.ROOT / "eval" / "active" / "not-ignored"))
    with pytest.raises(C.HarnessError):
        C.ensure_private(C.ROOT / "eval" / "active" / "not-ignored" / "x.json")
