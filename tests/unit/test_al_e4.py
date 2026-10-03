"""E4 (lessons v2) of the ceiling harness: clustering determinism, the eligibility rule, the group /
recurrence / date recount of the deterministic check, the E4 pre-registration. Synthetic data only."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

AL_DIR = Path(__file__).resolve().parents[2] / "eval" / "active"
if str(AL_DIR) not in sys.path:
    sys.path.insert(0, str(AL_DIR))

import al  # noqa: E402
import al_common as C  # noqa: E402
import al_e4 as E  # noqa: E402
import al_grading as G  # noqa: E402
import al_grounding as GR  # noqa: E402
import al_prereg as R  # noqa: E402

SEL = {"min_cross_episodes": 3, "min_cross_groups": 2, "min_local_episodes": 4}


class Identity:
    def text(self, s: str) -> str:
        return s


def blobs(per: int = 6, dims: int = 12, seed: int = 7) -> tuple[np.ndarray, list[int]]:
    rng = np.random.default_rng(seed)
    centers = np.eye(dims)[:3] * 5.0
    xs, labels = [], []
    for k, c in enumerate(centers):
        for _ in range(per):
            xs.append(c + rng.normal(0, 0.15, dims))
            labels.append(k)
    return np.array(xs), labels


def as_sets(clusters: list[list[int]], names: list) -> set[frozenset]:
    return {frozenset(names[i] for i in c) for c in clusters}


# --------------------------------------------------------------------------- clustering
def test_cosine_distances_symmetric_with_zero_diagonal():
    x, _ = blobs()
    d = E.cosine_distances(x)
    assert np.allclose(d, d.T) and np.all(np.diag(d) == 0) and d.min() >= 0


def test_agglomerate_is_deterministic_and_independent_of_input_order():
    rng = np.random.default_rng(3)
    x = rng.normal(size=(25, 8))
    d = E.cosine_distances(x)
    first, second = E.agglomerate(d, 0.6), E.agglomerate(d, 0.6)
    assert first == second
    perm = rng.permutation(25)
    names = list(range(25))
    permuted = E.agglomerate(E.cosine_distances(x[perm]), 0.6)
    assert as_sets(permuted, [names[i] for i in perm]) == as_sets(first, names)


def test_agglomerate_recovers_planted_clusters_and_threshold_extremes():
    x, labels = blobs()
    d = E.cosine_distances(x)
    got = E.agglomerate(d, 0.2)
    assert as_sets(got, labels) == {frozenset({0}), frozenset({1}), frozenset({2})}
    assert len(got) == 3
    assert len(E.agglomerate(d, 0.0)) == len(labels)  # nothing merges at distance 0
    assert len(E.agglomerate(d, 2.0)) == 1


def test_tune_threshold_is_reproducible_and_finds_the_planted_structure():
    x, _ = blobs()
    d = E.cosine_distances(x)
    grid = E.grid_values({"start": 0.04, "stop": 0.4, "step": 0.01})
    assert grid[0] == 0.04 and grid[-1] == 0.4 and len(grid) == 37
    t1, table1 = E.tune_threshold(d, grid)
    t2, table2 = E.tune_threshold(d, grid)
    assert (t1, table1) == (t2, table2)
    assert len(E.agglomerate(d, t1)) == 3


def test_silhouette_undefined_for_one_or_n_clusters():
    x, _ = blobs()
    d = E.cosine_distances(x)
    assert E.silhouette(d, [list(range(len(x)))]) is None
    assert E.silhouette(d, [[i] for i in range(len(x))]) is None


def test_centering_removes_the_shared_direction_deterministically():
    rng = np.random.default_rng(5)
    shared = np.ones(8) * 10.0  # an anisotropic corpus: every vector shares one big direction
    x = shared + rng.normal(0, 1, size=(30, 8))
    assert E.cosine_distances(x).max() < 0.1
    c1, c2 = E.center(x), E.center(x)
    assert np.array_equal(c1, c2) and np.allclose(c1.mean(axis=0), 0)
    assert E.cosine_distances(c1).max() > 1.0


def test_degenerate_clustering_is_refused_without_packets(e4_env):
    cfg = small_cfg()
    cfg["selection"]["E4"]["max_cluster_share"] = 0.3  # the 5-episode beta cluster is 50% of 10
    with pytest.raises(C.HarnessError, match="degenerate clustering"):
        E.build_e4(cfg, embed=fake_embed)
    assert E.clusters_path().is_file() and not (C.packets_dir(C.E4) / "manifest.json").exists()


def test_holdout_split_is_seeded_and_sized():
    ids = [f"ep{i:03d}" for i in range(50)]
    a = E.holdout_split(ids, 0.2, 11)
    assert a == E.holdout_split(list(reversed(ids)), 0.2, 11) and len(a) == 10
    assert a != E.holdout_split(ids, 0.2, 12)
    assert len(E.holdout_split(ids[:3], 0.2, 1)) == 2  # never fewer than 2 held-out episodes


@pytest.mark.parametrize(
    ("groups", "want"),
    [
        (["G1", "G2", "G2"], E.CROSS),
        (["G1", "G2"], None),  # too few episodes
        (["G1", "G1", "G1"], None),  # one group, too few for project-local
        (["G1", "G1", "G1", "G1"], E.LOCAL),
        (["G1", "G2", "G3", "G1", "G1"], E.CROSS),
        (["G1"], None),
    ],
)
def test_eligibility_rule(groups, want):
    assert E.eligibility(groups, SEL) == want


def test_group_count_counts_independence_groups_not_projects():
    group_of = {"a": "G1", "b": "G1", "c": "G2"}  # a and b: forks of one product
    assert E.group_count(["a", "b"], group_of) == 1
    assert E.group_count(["a", "b", "c", "zz"], group_of) == 2


# --------------------------------------------------------------------------- packets + check
def episode(eid: str, group: str, slug: str, quotes: list[str], d0: str, d1: str, topic: str) -> dict:
    return {
        "episode_id": eid,
        "slug": slug,
        "group": group,
        "source": "transcript",
        "title": f"{topic} mistake",
        "symptom": f"{topic} symptom",
        "fix": f"{topic} fix",
        "lesson": {"when": f"{topic} when", "do": f"{topic} do", "avoid": f"{topic} avoid"},
        "stack": ["python"],
        "versions": [],
        "evidence": [
            {"quote": q, "slots": ["symptom"], "speaker": "agent", "date": d0 if i == 0 else d1}
            for i, q in enumerate(quotes)
        ],
        "first_date": d0,
        "last_date": d1,
    }


EPS = [
    episode(
        "e1",
        "G1",
        "proj-a",
        ["The migration dropped the index silently.", "Re-run with the index kept."],
        "2026-01-02",
        "2026-01-03",
        "alpha",
    ),
    episode(
        "e2",
        "G1",
        "proj-b",
        ["Second fork lost the index on migrate too."],
        "2026-02-01",
        "2026-02-01",
        "alpha",
    ),
    episode(
        "e3",
        "G2",
        "proj-c",
        ["Index vanished after the schema migration ran.", "Added a check for the index."],
        "2026-03-05",
        "2026-03-09",
        "alpha",
    ),
]


def packet(kind: str = E.CROSS) -> dict:
    return E.e4_packet("E4-X01", kind, EPS, cluster=1, redactor=Identity())


def lesson(**over) -> dict:
    base = {
        "title": "Check indexes after migrations",
        "when": [
            {
                "text": "A migration runs",
                "evidence": [{"source": "e1", "quote": "The migration dropped the index"}],
            }
        ],
        "do": [
            {
                "text": "Verify the index",
                "evidence": [{"source": "e3", "quote": "Added a check for the index."}],
            }
        ],
        "avoid": [
            {
                "text": "Assume it survives",
                "evidence": [{"source": "e3", "quote": "Index vanished after the schema"}],
            }
        ],
        "not_verified_for": ["other databases"],
        "scope": {"stack": ["python"], "versions": []},
        "recurrence_count": 2,
        "group_count": 2,
        "first_seen": "2026-01-02",
        "last_seen": "2026-03-09",
    }
    base.update(over)
    return base


def check(les: dict, kind: str = E.CROSS) -> dict:
    pk = packet(kind)
    units = E.lesson_units(pk["packet_id"], {"abstain": False, "lesson": les})
    return E.check_lesson(units[0], pk)


def test_packet_user_message_is_stable_and_marks_the_summary_as_context():
    a, b = packet(), packet()
    assert a["user_sha256"] == b["user_sha256"]
    assert "CONTEXT ONLY, never quote" in a["user"] and "INDEPENDENCE GROUPS (2): G1, G2" in a["user"]
    assert a["context"]["first_seen"] == "2026-01-02" and a["context"]["last_seen"] == "2026-03-09"


def test_check_passes_a_grounded_cross_project_lesson():
    res = check(lesson())
    assert res["grounded_det"], res["grounding_flags"]
    assert res["recount"] == {"cited_episodes": 2, "groups": ["G1", "G2"], "group_count": 2}


def test_check_recounts_groups_cross_project_needs_two_independence_groups():
    les = lesson(
        do=[
            {
                "text": "Verify",
                "evidence": [{"source": "e2", "quote": "Second fork lost the index on migrate"}],
            }
        ],
        avoid=[{"text": "Assume", "evidence": [{"source": "e1", "quote": "Re-run with the index kept."}]}],
        last_seen="2026-02-01",
    )
    res = check(les)  # e1 + e2 are forks of one product: ONE group although two projects
    assert "under_evidenced" in res["grounding_flags"] and "group_count_mismatch" in res["grounding_flags"]
    assert res["recount"]["group_count"] == 1
    ok_local = check(lesson(**{**les, "group_count": 1}), kind=E.LOCAL)
    assert ok_local["grounded_det"], ok_local["grounding_flags"]


def test_check_flags_merged_lines_unknown_sources_counts_dates_and_versions():
    merged = "The migration dropped the index silently. Re-run with the index kept."
    res = check(lesson(when=[{"text": "x", "evidence": [{"source": "e1", "quote": merged}]}]))
    assert "quote_not_verbatim" in res["grounding_flags"]
    assert (
        "unknown_source"
        in check(lesson(when=[{"text": "x", "evidence": [{"source": "e9", "quote": "whatever text here"}]}]))[
            "grounding_flags"
        ]
    )
    assert "recurrence_mismatch" in check(lesson(recurrence_count=3))["grounding_flags"]
    assert "dates_mismatch" in check(lesson(first_seen="2026-01-01"))["grounding_flags"]
    bad_version = lesson(
        scope={"stack": ["python"], "versions": [{"component": "postgres", "version": "17.9"}]}
    )
    assert "invented_version" in check(bad_version)["grounding_flags"]
    assert "invented_ref" in check(lesson(title="Fixed in D-9999"))["grounding_flags"]


def test_abstention_has_no_units_and_check_output_dispatches_e4():
    pk = packet()
    assert E.lesson_units("E4-X01", {"abstain": True, "abstain_reason": "no lesson", "lesson": None}) == []
    out = GR.check_output(pk, {"abstain": False, "abstain_reason": "", "lesson": lesson()})
    assert len(out) == 1 and out[0]["check"]["grounded_det"]


def test_schema_accepts_lesson_and_no_lesson_and_rejects_inconsistent():
    import jsonschema

    schema = json.loads((AL_DIR / "prompts" / "e4.schema.json").read_text())
    jsonschema.validate({"abstain": False, "abstain_reason": "", "lesson": lesson()}, schema)
    jsonschema.validate({"abstain": True, "abstain_reason": "only looks alike", "lesson": None}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"abstain": False, "abstain_reason": "", "lesson": None}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"abstain": False, "abstain_reason": "", "lesson": lesson(when=[])}, schema)


# --------------------------------------------------------------------------- build + prereg
def fake_embed(texts):
    rows = []
    for t in texts:
        k = 0 if "alpha" in t else 1 if "beta" in t else 2
        v = np.zeros(8)
        v[k] = 5.0
        noise = np.frombuffer(hashlib.sha256(t.encode()).digest()[:8], dtype=np.uint8) / 2550.0
        rows.append(v + noise)
    return np.array(rows)


def synthetic_episodes() -> list[dict]:
    eps = []
    for i in range(4):
        eps.append(
            episode(
                f"a{i}",
                "G1" if i < 2 else "G2",
                f"p{i}",
                [f"alpha evidence line number {i}"],
                "2026-01-0" + str(i + 1),
                "2026-01-0" + str(i + 1),
                "alpha",
            )
        )
    for i in range(5):
        eps.append(
            episode(
                f"b{i}", "G3", "p9", [f"beta evidence line number {i}"], "2026-02-01", "2026-02-02", "beta"
            )
        )
    eps.append(
        episode("c0", "G4", "p8", ["gamma evidence line number 0"], "2026-03-01", "2026-03-01", "gamma")
    )
    return eps


@pytest.fixture
def e4_env(monkeypatch, tmp_path):
    priv = tmp_path / "e4"
    monkeypatch.setenv(C.PRIVATE_ENV, str(priv))
    monkeypatch.setattr(E, "_redactor", Identity)
    monkeypatch.setattr(R, "_claude_version", lambda cli: "test-cli 0")
    priv.mkdir()
    with (priv / "episodes.jsonl").open("w") as fh:
        for e in synthetic_episodes():
            fh.write(json.dumps(e) + "\n")
    return priv


def small_cfg() -> dict:
    cfg = C.load_config_for(C.E4)
    cfg["selection"]["E4"]["holdout_frac"] = 0.6  # 10 synthetic episodes: a defined silhouette needs > 2
    return cfg


def test_build_e4_is_reproducible_and_applies_the_eligibility_rule(e4_env):
    cfg = small_cfg()
    packets = E.build_e4(cfg, embed=fake_embed)
    first = E.clusters_path().read_text()
    pk_sha = [p["user_sha256"] for p in packets]
    packets2 = E.build_e4(cfg, embed=fake_embed)
    assert E.clusters_path().read_text() == first and [p["user_sha256"] for p in packets2] == pk_sha
    rec = json.loads(first)
    kinds = {tuple(sorted(r["episodes"])): r["eligibility"] for r in rec["clusters"]}
    assert kinds[("a0", "a1", "a2", "a3")] == E.CROSS
    assert kinds[("b0", "b1", "b2", "b3", "b4")] == E.LOCAL
    assert kinds[("c0",)] is None
    assert rec["summary"]["threshold"] in E.grid_values(cfg["selection"]["E4"]["grid"])
    assert [p["packet_id"] for p in packets] == ["E4-X01", "E4-L01"]


def test_e4_prereg_roundtrip_and_tamper_detection(e4_env):
    E.build_e4(small_cfg(), embed=fake_embed)
    path, digest = R.write(suite=C.E4_SUITE)
    assert path.name == "PREREG-E4.md" and (e4_env / "PREREG-E4.sha256").read_text().startswith(digest)
    assert not (e4_env / "PREREG.md").exists()  # the E0-E3 registration is never touched
    record, again = R.verify(C.E4)
    assert again == digest and record["clustering"]["cross_project"] == 1
    E.clusters_path().write_text(E.clusters_path().read_text().replace('"G1"', '"G7"'))
    with pytest.raises(C.HarnessError, match="e4 episodes/clusters"):
        R.verify(C.E4)


def test_e4_suite_never_uses_the_main_private_dir(monkeypatch):
    monkeypatch.setenv(C.E4_PRIVATE_ENV, str(C.ROOT / "docs" / "private" / "active-librarian"))
    with pytest.raises(C.HarnessError):
        C.use_e4_private()


def test_e4_cannot_be_mixed_with_other_experiments():
    with pytest.raises(C.HarnessError):
        al._exps("E1,E4", C.ARM_EXPERIMENTS)
    assert al._exps("E4", C.ARM_EXPERIMENTS) == ["E4"]
    assert "E4" not in al._exps("all", C.EXPERIMENTS)


# --------------------------------------------------------------------------- bars
def test_e4_labels_stricter_rules_and_bars():
    assert G.stricter([True, False], "overgeneralized") is True
    assert G.stricter([True, False], "harmful") is True
    assert G.stricter([True, False], "useful") is False
    bars = C.load_config_for(C.E4)["bars"]
    good = {
        "units": 10,
        "correct": 0.9,
        "grounded": 0.95,
        "useful": 0.5,
        "harmful": 0,
        "overgeneralized_rate": 0.1,
    }
    assert G.passes(good, bars, C.E4) == (True, [])
    ok, fails = G.passes({**good, "overgeneralized_rate": 0.2, "harmful": 1}, bars, C.E4)
    assert not ok and any("overgeneralized" in f for f in fails) and any("harmful" in f for f in fails)


# --------------------------------------------------------------------------- E4B (status + model era)
@pytest.mark.parametrize(
    ("members", "want"),
    [
        ([{"a"}, {"b"}], "P1"),  # every member is an episode of one imported packet
        ([{"new1", "a"}, {"b"}], "P1"),  # a new episode merged into an old one still maps to it
        ([{"a"}, {"new2"}], None),  # one member is new information
        ([{"a"}, {"c"}], None),  # old episodes of two different packets: a new grouping
        ([], None),
    ],
)
def test_covered_by_matches_member_episode_ids(members, want):
    assert E.covered_by(members, {"P1": ["a", "b"], "P2": ["c"]}) == want


def src(era: str, status: str, d0: str, d1: str) -> dict:
    return {"model_era": era, "status": status, "valid_from": d0, "last_seen": d1}


def test_status_needs_its_evidence():
    res = src("m-old", "resolved", "2026-01-01", "2026-01-02")
    unk = src("m-old", "unknown", "2026-01-05", "2026-01-05")
    cur = src("m-now", "unknown", "2026-02-01", "2026-02-01")
    assert E.status_supported("unknown", [unk], "m-now")
    assert E.status_supported("resolved", [res], "m-now") and not E.status_supported(
        "resolved", [res, unk], "m-now"
    )
    assert E.status_supported("historical", [res, unk], "m-now") and not E.status_supported(
        "historical", [cur], "m-now"
    )
    assert E.status_supported("active", [res, cur], "m-now")  # recurred after a verified fix
    assert not E.status_supported("active", [unk, cur], "m-now")  # never active without a resolved one
    assert not E.status_supported("resolved", [], "m-now")


def e4b_packet() -> dict:
    eps = [
        {**EPS[0], "status": "resolved", "model_era": "m-old"},
        {**EPS[1], "status": "unknown", "model_era": "m-old"},
        {**EPS[2], "status": "unknown", "model_era": "m-now"},
    ]
    return E.e4_packet(
        "E4B-X01", E.CROSS, eps, cluster=1, redactor=Identity(), exp=C.E4B, current_era="m-now"
    )


def test_e4b_check_recounts_eras_and_checks_status():
    pk = e4b_packet()
    assert (
        "CURRENT AGENT MODEL ERA: m-now" in pk["user"] and "status resolved · model era m-old" in pk["user"]
    )
    good = lesson(model_era=["m-now", "m-old"], status="active")  # e1 resolved, e3 starts later: recurred
    unit = E.lesson_units("E4B-X01", {"abstain": False, "lesson": good})[0]
    res = E.check_lesson(unit, pk)
    assert res["grounded_det"], res["grounding_flags"]
    assert res["recount"]["eras"] == ["m-now", "m-old"]
    bad = E.check_lesson({**unit, "content": {**good, "model_era": ["m-old"], "status": "resolved"}}, pk)
    assert {"era_mismatch", "status_unsupported"} <= set(bad["grounding_flags"])
    hist = E.check_lesson({**unit, "content": {**good, "status": "historical"}}, pk)
    assert "status_unsupported" in hist["grounding_flags"]  # e3 is of the current era


def test_e4b_schema_requires_status_and_era():
    import jsonschema

    schema = json.loads((AL_DIR / "prompts" / "e4b.schema.json").read_text())
    jsonschema.validate(
        {"abstain": False, "abstain_reason": "", "lesson": lesson(model_era=["m"], status="unknown")}, schema
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"abstain": False, "abstain_reason": "", "lesson": lesson()}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"abstain": False, "abstain_reason": "", "lesson": lesson(model_era=["m"], status="maybe")},
            schema,
        )


@pytest.fixture
def e4b_env(monkeypatch, tmp_path):
    priv = tmp_path / "e4b"
    monkeypatch.setenv(C.PRIVATE_ENV, str(priv))
    monkeypatch.setattr(E, "_redactor", Identity)
    monkeypatch.setattr(R, "_claude_version", lambda cli: "test-cli 0")
    priv.mkdir()
    eps = synthetic_episodes()
    eps[0]["merged_ids"] = ["old-a0"]  # a new episode merged into an imported one
    with (priv / "episodes.jsonl").open("w") as fh:
        for e in eps:
            fh.write(json.dumps({**e, "status": "unknown", "model_era": "m-now"}) + "\n")
    covered = {"E4-X01": ["old-a0", "a1", "a2", "a3"]}  # the alpha cluster is already imported
    (priv / "covered.json").write_text(json.dumps({"packets": covered}))
    return priv


def test_e4b_build_uses_the_frozen_threshold_and_excludes_covered_clusters(e4b_env):
    cfg = C.load_config_for(C.E4B)
    assert cfg["selection"]["E4B"]["threshold"] == 1.0 and cfg["spend_cap_usd"] == "1.50"
    packets = E.build_e4(cfg, exp=C.E4B, embed=fake_embed)
    rec = json.loads(E.clusters_path().read_text())
    assert rec["grid"] == [] and rec["summary"]["threshold"] == 1.0
    kinds = {tuple(sorted(r["episodes"])): r for r in rec["clusters"]}
    alpha = kinds[("a0", "a1", "a2", "a3")]
    assert alpha["eligibility"] == E.COVERED and alpha["covered_by"] == "E4-X01"
    assert [p["packet_id"] for p in packets] == ["E4B-L01"] and packets[0]["exp"] == C.E4B
    assert packets[0]["context"]["current_era"] == "claude-opus-5-5"
    assert rec["summary"]["already_covered"] == 1


def test_e4b_prereg_is_separate_from_e4(e4b_env):
    E.build_e4(C.load_config_for(C.E4B), exp=C.E4B, embed=fake_embed)
    path, digest = R.write(suite=C.E4B_SUITE)
    assert path.name == "PREREG-E4b.md" and not (e4b_env / "PREREG-E4.md").exists()
    record, again = R.verify(C.E4B)
    assert again == digest and record["inputs"]["e4"]["covered_sha256"]
    (e4b_env / "covered.json").write_text(json.dumps({"packets": {}}))
    with pytest.raises(C.HarnessError, match="e4 episodes/clusters"):
        R.verify(C.E4B)


def test_lesson_suites_never_share_a_private_dir(monkeypatch, tmp_path):
    monkeypatch.setenv(C.E4_PRIVATE_ENV, str(tmp_path / "same"))
    monkeypatch.setenv(C.E4B_PRIVATE_ENV, str(tmp_path / "same"))
    with pytest.raises(C.HarnessError):
        C.use_lesson_private(C.E4B)
    monkeypatch.setenv(C.E4B_PRIVATE_ENV, str(tmp_path / "b"))
    assert C.use_lesson_private(C.E4B) == (tmp_path / "b").resolve()
    with pytest.raises(C.HarnessError):
        al._exps("E4,E4B", C.ARM_EXPERIMENTS)
