"""D-055 pre-Phase-2 retrieval fixes: DF term filtering, query-centred previews + top-3 extension
reserve, the title RRF list, and the per-config-dir install id in the device fingerprint."""

from __future__ import annotations

import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hlmemo.cli import client_config
from hlmemo.core.budget import Meter
from hlmemo.core.retrieval import (
    ELLIPSIS,
    EXT_RESERVE,
    PREVIEW_EXT,
    PREVIEW_TOK,
    TERM_MAX,
    CardInput,
    Fused,
    dedupe_and_order,
    pack_query,
    query_preview,
    rrf_fuse,
    select_terms,
    split_terms,
    term_matches,
)
from hlmemo.core.term_stats import ProjectStats, Vocabulary
from hlmemo.db.read_queries import Candidate, HitRow


@pytest.fixture(scope="module")
def meter() -> Meter:
    return Meter()


# --------------------------------------------------------------------------- (a) DF filter
def _vocab(n: int, **ndoc: int) -> Vocabulary:
    return Vocabulary.build(n, list(ndoc.items()))


def test_prefix_df_sums_prefix_lexemes_and_caps_at_n() -> None:
    v = _vocab(100, the=90, then=20, service=30, services=10, zeta=1)
    assert v.prefix_df("the") == 100  # 90 + 20 capped at n
    assert v.prefix_df("serv") == 40
    assert v.prefix_df("service") == 40
    assert v.prefix_df("services") == 10
    assert v.prefix_df("zeta") == 1
    assert v.prefix_df("absent") == 0
    assert Vocabulary(0).prefix_df("x") == 0


def test_common_terms_dropped_rare_and_identifiers_survive() -> None:
    v = _vocab(1000, the=900, how=400, deploy=30, svc=950, v2=990)
    terms = ["how", "the", "deploy", "svc-a1", "read_service.py", "v2"]
    kept = select_terms(terms, v.prefix_df, v.n)
    # 'svc-a1', 'read_service.py' and 'v2' are identifiers (digit / '_' / '.'): kept even when common.
    assert kept == ["deploy", "svc-a1", "read_service.py", "v2"]


def test_all_common_terms_fall_back_to_the_rarest_never_empty() -> None:
    v = _vocab(1000, the=900, how=400, was=700, does=500)
    assert select_terms(["the", "how", "was", "does"], v.prefix_df, v.n) == ["how", "does"]
    assert select_terms(["the"], v.prefix_df, v.n) == ["the"]
    # the title list uses fallback=0: an all-common title query is simply not run
    assert select_terms(["the", "how"], v.prefix_df, v.n, fallback=0) == []


def test_small_corpus_is_not_filtered() -> None:
    v = _vocab(50, the=50)
    assert select_terms(["the", "x1"], v.prefix_df, v.n) == ["the", "x1"]


def test_split_terms_filters_before_the_term_cap() -> None:
    commons = [f"common{c}" for c in "abcdefghijklmnopqrstuvwxyz"]  # 26 words, not identifiers
    stats = ProjectStats(_vocab(1000, **dict.fromkeys(commons, 900)), Vocabulary(0))
    query = " ".join(commons) + " rareword svc_x"
    qt = split_terms(query, stats)
    assert qt.lexical == ["rareword", "svc_x"]
    assert qt.identifiers == ["svc_x"]
    assert len(qt.terms) == TERM_MAX  # §4.3 terms are still the first TERM_MAX
    assert qt.preview_terms == ["rareword", "svc_x"]
    # no statistics → Phase-0 behaviour
    plain = split_terms(query)
    assert plain.lexical == plain.terms and len(plain.terms) == TERM_MAX


# --------------------------------------------------------------------------- (b) previews
FILLER = " ".join(f"filler{i} words about nothing in particular." for i in range(120))
TEXT = FILLER + " The retention window is 45 days for the Kafka topic orders_v2. " + FILLER


def test_term_matches_prefix_boundary_and_normalisation() -> None:
    text = "Çözüm: read_service.py kurulumu; the reader thereof; user_service ok"
    found = term_matches(text, ["cozum", "service", "read_service.py", "the"])
    names = sorted({t for _, t in found})
    assert names == [0, 1, 2, 3]
    assert (0, 0) in found  # 'Çözüm' at offset 0 matches the normalised term
    assert (text.index("service ok"), 1) in found  # word part after '_' (lexeme boundary)
    # 'the' matches 'the' and 'thereof' (prefix) but not inside 'reader'
    assert [c for c, t in found if t == 3] == [text.index("the "), text.index("thereof")]


def test_short_text_and_no_match_keep_phase0_preview(meter: Meter) -> None:
    assert query_preview(meter, "tiny chunk", ["zzz"], PREVIEW_TOK) == "tiny chunk"
    head, cut = meter.truncate(TEXT, PREVIEW_TOK)
    assert cut
    assert query_preview(meter, TEXT, ["nomatchanywhere"], PREVIEW_TOK) == head
    assert query_preview(meter, TEXT, [], PREVIEW_TOK) == head


def test_preview_is_centred_on_the_query_terms_and_deterministic(meter: Meter) -> None:
    terms = ["retention", "kafka", "orders_v2"]
    a = query_preview(meter, TEXT, terms, PREVIEW_TOK)
    b = query_preview(meter, TEXT, terms, PREVIEW_TOK)
    assert a == b
    assert a.startswith(ELLIPSIS)
    assert "retention window is 45 days" in a and "orders_v2" in a
    assert meter.count_text(a) <= PREVIEW_TOK + 1
    assert a[len(ELLIPSIS) :] in TEXT
    ext = query_preview(meter, TEXT, terms, PREVIEW_EXT)
    assert "orders_v2" in ext and meter.count_text(ext) <= PREVIEW_EXT + 1


def test_preview_prefers_the_window_with_most_distinct_terms(meter: Meter) -> None:
    text = "kafka " + FILLER + " kafka retention orders_v2 together here. " + FILLER
    p = query_preview(meter, text, ["kafka", "retention", "orders_v2"], PREVIEW_TOK)
    assert "kafka retention orders_v2" in p


def _row(chunk_id: int, version_id: int, text: str, ordinal: int = 0) -> HitRow:
    return HitRow(
        chunk_id,
        version_id,
        ordinal,
        text,
        version_id,
        "fact",
        f"docs/item{version_id}.md",
        ["t"],
        "all",
        datetime(2026, 1, 1, tzinfo=UTC),
    )


def _hits(n: int, text: str) -> list[Fused]:
    out = []
    for i in range(n):
        f = Fused(i + 1, i + 1, i + 1, 1.0 / (61 + i))
        f.row = _row(i + 1, i + 1, text)
        out.append(f)
    return out


def _envelope() -> dict:
    return {
        "project": "p",
        "as_of": {"valid_at": "x", "known_at": "x"},
        "device_class": "personal",
        "evidence": "matched",
        "indexing_pending": False,
    }


@pytest.mark.parametrize("budget", [256, 700, 1500, 3000, 8000])
def test_pack_budget_equality_and_top3_extension_fires(meter: Meter, budget: int) -> None:
    terms = ["retention", "kafka", "orders_v2"]
    card = CardInput(version_id=99, body="card " * 400)
    env = pack_query(meter, _envelope(), budget, card, _hits(200, TEXT), total=200, terms=terms)
    used = env["budget"]["used"]
    assert used == meter.count(env) and used <= budget  # metered text == wire text (G2)
    hits = env["hits"]
    assert env["omitted"] == 200 - len(hits)
    if budget >= 1500:
        # the reserve lets step (d) fire: the top-3 previews are the long, centred ones
        for h in hits[:3]:
            assert meter.count_text(h["preview"]) > PREVIEW_TOK + 8
            assert "orders_v2" in h["preview"]
        assert all(meter.count_text(h["preview"]) <= PREVIEW_TOK + 1 for h in hits[3:])
        # (e) the refill leaves less than one more hit of room
        assert budget - used < meter.count(hits[-1]) + 5
    # deterministic
    again = pack_query(meter, _envelope(), budget, card, _hits(200, TEXT), total=200, terms=terms)
    assert again == env


def test_reserve_constant() -> None:
    assert EXT_RESERVE == 3 * (PREVIEW_EXT - PREVIEW_TOK)


# --------------------------------------------------------------------------- (c) title list
def _cand(chunk_id: int, version_id: int) -> Candidate:
    return Candidate(chunk_id, version_id, version_id, "all", 0.0)


def test_title_rank_credits_every_retrieved_chunk_of_the_version() -> None:
    lexical = [_cand(11, 1), _cand(21, 2), _cand(12, 1)]
    vector = [_cand(21, 2), _cand(12, 1)]
    fused = {f.chunk_id: f for f in rrf_fuse(lexical, [], vector, [_cand(10, 1)])}
    assert fused[11].title_rank == 1 and fused[12].title_rank == 1
    assert 10 not in fused  # the version already had retrieved chunks
    assert fused[21].title_rank is None
    ordered = dedupe_and_order(list(fused.values()))
    # without the title list version 2 (chunk 21) leads; the title match lifts version 1 above it,
    # represented by its best non-title chunk (12: lexical 3 + vector 2)
    assert [f.chunk_id for f in ordered] == [12, 21]
    base = dedupe_and_order(rrf_fuse(lexical, [], vector))
    assert [f.chunk_id for f in base] == [21, 12]


def test_title_only_match_enters_with_the_first_chunk() -> None:
    fused = rrf_fuse([_cand(21, 2)], [], [], [_cand(30, 3)])
    by_id = {f.chunk_id: f for f in fused}
    assert set(by_id) == {21, 30}
    assert by_id[30].title_rank == 1 and by_id[30].lexical_rank is None
    assert by_id[30].score == pytest.approx(1 / 61)


# --------------------------------------------------------------------------- (e) fingerprint
def test_fingerprint_differs_per_config_dir_and_is_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client_config, "_machine_id", lambda: "machine-1")
    monkeypatch.setattr(client_config.getpass, "getuser", lambda: "same-user")
    monkeypatch.setenv("HLM_CONFIG_DIR", str(tmp_path / "a"))
    fp_a = client_config.device_fingerprint()
    assert client_config.device_fingerprint() == fp_a  # stable for the same config dir
    id_file = tmp_path / "a" / client_config.INSTALL_ID_FILE
    assert stat.S_IMODE(id_file.stat().st_mode) == 0o600
    assert len(id_file.read_text().strip()) == 32
    monkeypatch.setenv("HLM_CONFIG_DIR", str(tmp_path / "b"))
    fp_b = client_config.device_fingerprint()
    assert fp_b != fp_a and len(fp_b) == 64  # a second install of the same OS user: new device
    monkeypatch.setenv("HLM_CONFIG_DIR", str(tmp_path / "a"))
    assert client_config.device_fingerprint() == fp_a


def test_malformed_install_id_is_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HLM_CONFIG_DIR", str(tmp_path))
    (tmp_path / client_config.INSTALL_ID_FILE).write_text("not-an-id\n")
    value = client_config.install_id()
    assert value != "not-an-id" and client_config.install_id() == value


def test_unwritable_config_dir_still_yields_a_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setenv("HLM_CONFIG_DIR", str(blocker / "sub"))  # parent is a file: mkdir fails
    assert len(client_config.device_fingerprint()) == 64
