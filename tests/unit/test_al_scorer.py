"""Active-librarian ceiling harness (eval/active): blind kit, pre-registered scorer, pre-registration
and the arm runners (fakes only: no network, no model)."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

AL_DIR = Path(__file__).resolve().parents[2] / "eval" / "active"
if str(AL_DIR) not in sys.path:
    sys.path.insert(0, str(AL_DIR))

import al  # noqa: E402
import al_arms as A  # noqa: E402
import al_common as C  # noqa: E402
import al_grading as GR  # noqa: E402
import al_grounding as G  # noqa: E402
import al_packets as P  # noqa: E402
import al_prereg as R  # noqa: E402

T0 = datetime(2026, 9, 1, tzinfo=UTC)
T1 = datetime(2026, 9, 20, tzinfo=UTC)
DECISION = "Retries use exponential backoff capped at 8 seconds."
BODY = f"Session\n\n## Decisions\n- {DECISION}"
CFG = C.load_config()
BARS = CFG["bars"]


@pytest.fixture
def priv(monkeypatch, tmp_path):
    monkeypatch.setenv(C.PRIVATE_ENV, str(tmp_path / "priv"))
    monkeypatch.setattr(R, "_claude_version", lambda cli: "test-cli 0")
    return tmp_path / "priv"


# --------------------------------------------------------------------------- stricter / metrics / verdict
def test_stricter_label_on_a_split():
    assert GR.stricter([True, False], "correct") is False
    assert GR.stricter([True, True], "useful") is True
    assert GR.stricter([False, True], "harmful_stale") is True
    assert GR.stricter([False, False], "harmful_stale") is False
    assert GR.stricter([True, None], "correct") is None
    assert GR.to_bool("yes") is True and GR.to_bool("0") is False and GR.to_bool("maybe") is None


def _key(units):
    return {"units": {code: m for code, m in units}, "coverage": {}}


def _labels(rows):
    return {code: {"code": code, **dict(zip(GR.LABELS, vals, strict=True))} for code, vals in rows.items()}


def test_final_labels_grounded_needs_the_deterministic_check():
    key = _key([("A1", {"arm": "gemini", "run": "gemini-r1", "hiding": False, "grounded_det": False})])
    a = _labels({"A1": (True, True, True, False)})
    final, missing = GR.final_labels(key, [a, a], GR.LABELS)
    assert missing == [] and final["A1"]["grounded"] is False and final["A1"]["correct"] is True


def _unit(arm, run, hiding=False, det=True):
    return {"arm": arm, "run": run, "hiding": hiding, "grounded_det": det}


def _metrics_for(n_good, n_bad, *, hiding_harm=0, arm="gemini"):
    units = [(f"{arm}{i}", _unit(arm, f"{arm}-r1")) for i in range(n_good + n_bad)]
    units += [(f"{arm}h{i}", _unit(arm, f"{arm}-r1", hiding=True)) for i in range(hiding_harm)]
    key = _key(units)
    rows = {c: (True, True, True, False) for c, _ in units[:n_good]}
    rows |= {c: (False, False, False, False) for c, _ in units[n_good : n_good + n_bad]}
    rows |= {c: (True, True, True, True) for c, _ in units[n_good + n_bad :]}
    final, _ = GR.final_labels(key, [_labels(rows)] * 2, GR.LABELS)
    return GR.metrics(list(key["units"]), key, final)


def test_verdict_go_gemini_opus_only_and_no_go():
    good = _metrics_for(20, 0)
    weak = _metrics_for(15, 5)
    assert GR.verdict("E1", good, good, BARS)["decision"] == "GO-Gemini"
    assert GR.verdict("E1", weak, good, BARS)["decision"] == "GO-Opus-only (owner call)"
    assert GR.verdict("E1", weak, weak, BARS)["decision"] == "NO-GO"
    hidden_harm = _metrics_for(40, 0, hiding_harm=1)
    v = GR.verdict("E1", hidden_harm, good, BARS)
    assert v["decision"] == "GO-Opus-only (owner call)"
    assert any("hiding" in f for f in v["gemini_fails"])


def test_verdict_opus_margin_and_missing_opus():
    g = _metrics_for(19, 1)  # .95: passes every bar
    o = _metrics_for(20, 0)
    assert GR.verdict("E1", g, o, BARS)["decision"] == "GO-Gemini"
    # Gemini passes the bars but Opus is more than .10 better on useful
    g2 = dict(g, useful=0.62)
    o2 = dict(o, useful=0.80)
    assert GR.verdict("E1", g2, o2, BARS)["decision"] == "GO-Opus-only (owner call)"
    assert GR.verdict("E1", g, GR.metrics([], _key([]), {}), BARS)["decision"].startswith("INCOMPLETE")
    assert GR.passes(g, BARS, "E2", None)[0] is False  # E2 also needs coverage


# --------------------------------------------------------------------------- packets / prereg fixtures
def _e1_packet():
    cand = {
        "version_id": 20,
        "kind": "fact",
        "project_id": 1,
        "title": "Retry",
        "body": "Retries use a fixed delay.",
        "valid_from": T0,
    }
    note = {"handle": "v10", "project_id": 1, "title": "Session s1", "body": BODY, "valid_from": T1}
    return P.e1_packet("E1-001", note, [(cand, False)], slugs={1: "alpha"}, candidate_chars=1500)


def _write_all_packets():
    P._write_packets("E1", [_e1_packet()], {})
    e2 = P.e2_packet(
        "E2-001",
        "alpha",
        card=None,
        card_stale=False,
        memory_map="(map)",
        notes=[{"version_id": 11, "title": "S", "body": BODY, "valid_from": T1}],
        items=[],
        note_chars=2500,
        item_chars=700,
    )
    P._write_packets("E2", [e2], {})
    rows = [
        {
            "version_id": 1,
            "kind": "lesson",
            "slug": "a",
            "title": "x",
            "body": "Pin versions always.",
            "valid_from": T0,
        }
    ]
    P._write_packets("E3", [P.e3_packet("E3-001", rows, lesson_chars=1200)], {})
    d = C.packets_dir("E0")
    unit = {
        "view": {"kind": "link", "relation": "duplicate", "subjects": []},
        "hidden": {"question_id": "q", "project": "alpha", "stratum": "curated", "hiding": False},
    }
    C.write_json(
        d / "units.json",
        {"exp": "E0", "units": [unit, {**unit, "hidden": {**unit["hidden"], "stratum": "imported"}}]},
    )
    C.write_json(d / "manifest.json", {"exp": "E0", "file": "units.json"})
    _write_mustknow(
        [{"id": f"m{i}", "fact": f"f{i}", "source": "v11", "quote": DECISION} for i in range(1, 9)]
    )


def _write_mustknow(facts):
    C.write_json(C.pdir("mustknow") / "E2-001.json", {"packet_id": "E2-001", "facts": facts})


def test_prereg_requires_ready_mustknow(priv):
    _write_all_packets()
    _write_mustknow([{"id": "m1", "fact": "f", "source": "v11", "quote": DECISION}])
    with pytest.raises(C.HarnessError, match="1 facts"):
        R.write()
    facts = [{"id": f"m{i}", "fact": "f", "source": "v11", "quote": DECISION} for i in range(1, 9)]
    facts[3]["quote"] = "a quote that is nowhere in the source"
    _write_mustknow(facts)
    with pytest.raises(C.HarnessError, match="not verbatim"):
        R.write()
    (priv / "mustknow" / "E2-001.json").unlink()
    with pytest.raises(C.HarnessError, match="missing"):
        R.write()


def test_prereg_write_verify_and_tamper(priv):
    _write_all_packets()
    path, digest = R.write()
    assert path.is_file() and (priv / "PREREG.sha256").read_text().startswith(digest)
    record, d2 = R.verify("E1")
    assert d2 == digest and record["bars"] == BARS
    R.verify("E2")  # the must-know file was registered
    f = priv / "packets" / "E1" / "E1-001.json"
    pk = json.loads(f.read_text())
    pk["user"] += " tampered"
    f.write_text(json.dumps(pk))
    with pytest.raises(C.HarnessError, match="packets.E1"):
        R.verify("E1")
    R.verify("E3")  # other experiments are unaffected
    (priv / "PREREG.md").write_text((priv / "PREREG.md").read_text() + "\nedited")
    with pytest.raises(C.HarnessError, match="edited after registration"):
        R.verify("E3")


def test_prereg_refuses_after_outputs_and_run_refuses_without_prereg(priv):
    _write_all_packets()
    assert al.main(["run", "--exp", "E1", "--arm", "opus"]) == 2  # no pre-registration yet
    R.write()
    C.write_json(C.pdir("outputs", "E1", "opus-r1") / "E1-001.json", {"status": "ok"})
    with pytest.raises(C.HarnessError, match="must precede"):
        R.write(force=True)


# --------------------------------------------------------------------------- arms (fakes)
def _good_e1_output():
    return {
        "abstain": False,
        "facts": [
            {
                "title": "Retry policy",
                "claims": [
                    {"text": "Backoff capped at 8 s.", "evidence": [{"source": "v10", "quote": DECISION}]}
                ],
                "as_of": "2026-09-20",
                "open_unknown": [],
            }
        ],
    }


def test_parse_stream_and_opus_runner_saves_raw_output(priv):
    _write_all_packets()
    pk = P.load_packets("E1")[0]
    good = json.dumps(_good_e1_output())
    stream = [
        {"type": "system", "subtype": "init", "model": "test-model"},
        {
            "type": "result",
            "result": "```json\n" + good + "\n```",
            "is_error": False,
            "usage": {"output_tokens": 9},
        },
    ]
    raw = ("\n".join(json.dumps(e) for e in stream) + "\n").encode()
    calls = []

    def fake(cmd, user, timeout):
        calls.append((cmd, user))
        return (b"not json\n" if len(calls) == 1 else raw), 0

    res = A.run_opus("E1", [pk], CFG, run=1, prereg_sha="abc", runner=fake)
    assert res == {"run": "opus-r1", "ok": 1, "failed": 0}
    cmd, user = calls[0]
    assert user == pk["user"] and cmd[cmd.index("--system-prompt") + 1] == A.task_spec("E1", CFG).system
    assert "--output-format" in cmd and "stream-json" in cmd and cmd[cmd.index("--tools") + 1] == ""
    out = priv / "outputs" / "E1" / "opus-r1"
    assert (out / "E1-001.attempt1.stream.jsonl").read_bytes() == b"not json\n"
    assert (out / "E1-001.attempt2.stream.jsonl").read_bytes() == raw
    rec = json.loads((out / "E1-001.json").read_text())
    assert rec["status"] == "ok" and rec["model"] == "test-model" and rec["output"] == _good_e1_output()


def _chat(content, prompt=1000, completion=200):
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    }


def test_gemini_runner_books_spend_and_stops_at_the_cap(priv, monkeypatch):
    _write_all_packets()
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    cfg = json.loads(json.dumps(CFG))
    cfg["arms"]["gemini"]["env_file"] = "no-such.env"
    pk = P.load_packets("E1")[0]
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=_chat(json.dumps(_good_e1_output())))

    res = asyncio.run(
        A.run_gemini("E1", [pk], cfg, run=1, prereg_sha="abc", transport=httpx.MockTransport(handler))
    )
    assert res["ok"] == 1 and len(sent) == 1
    assert sent[0]["messages"][1]["content"] == pk["user"]  # the packet is sent byte-identical
    assert sent[0]["max_tokens"] == cfg["max_tokens"]["E1"]
    spent = A.spent_usd()
    assert spent > 0
    rec = json.loads((priv / "outputs" / "E1" / "gemini-r1" / "E1-001.json").read_text())
    assert rec["status"] == "ok" and Decimal(rec["cost_usd"]) == spent
    raw = json.loads((priv / "outputs" / "E1" / "gemini-r1" / "E1-001.raw.json").read_text())
    assert json.loads(raw["attempts"][0]["content"]) == _good_e1_output()
    # the cap: a second run with a cap at what was already spent sends nothing
    cfg["spend_cap_usd"] = str(spent + Decimal("0.0001"))
    res2 = asyncio.run(
        A.run_gemini("E1", [pk], cfg, run=2, prereg_sha="abc", transport=httpx.MockTransport(handler))
    )
    assert res2["stopped"] is True and len(sent) == 1
    cfg["spend_cap_usd"] = str(spent)
    with pytest.raises(C.HarnessError, match="spend cap"):
        asyncio.run(
            A.run_gemini("E1", [pk], cfg, run=2, prereg_sha="abc", transport=httpx.MockTransport(handler))
        )


# --------------------------------------------------------------------------- kit + score end to end
def _check_file(exp, run, units):
    C.write_json(
        C.pdir("checks", exp) / f"{run}.json", {"exp": exp, "run": run, "packets": units, "totals": {}}
    )


def test_kit_is_blind_and_score_end_to_end(priv):
    _write_all_packets()
    R.write()
    record, digest = R.verify("E1")
    record["_sha"] = digest
    pk = P.load_packets("E1")[0]
    for run in GR.run_labels(record):
        units = G.check_output(pk, _good_e1_output())
        _check_file("E1", run, {"E1-001": {"status": "ok", "units": units}})
    info = GR.build_kit("E1", record, [pk])
    assert info["units"] == 3 and info["runs"] == ["gemini-r1", "gemini-r2", "opus-r1"]
    gdir = priv / "grading" / "E1"
    key = json.loads((priv / "grading-key" / "E1.key.json").read_text())
    assert not any(p.name.endswith(".key.json") for p in gdir.rglob("*"))  # the key is outside the kit
    rows_a = [json.loads(x) for x in (gdir / "reader-A" / "units.jsonl").read_text().splitlines()]
    assert {r["code"] for r in rows_a} == set(key["units"])
    assert not any(k in r for r in rows_a for k in ("arm", "run", "uid"))
    assert "gemini" not in (gdir / "reader-A" / "units.jsonl").read_text()
    assert (gdir / "context" / "E1-001.txt").read_text().startswith(pk["user"])
    assert (gdir / "READER-INSTRUCTIONS.md").is_file()

    def label(reader, fn):
        lines = [json.dumps({"code": code, **fn(code)}) for code in key["units"]]
        (gdir / f"reader-{reader}" / "labels.jsonl").write_text("\n".join(lines) + "\n")

    with pytest.raises(C.HarnessError, match="lack a label"):
        GR.score("E1", record)
    label("A", lambda c: {"correct": True, "grounded": True, "useful": True, "harmful_stale": False})
    opus_code = next(c for c, m in key["units"].items() if m["arm"] == "opus")
    label(
        "B", lambda c: {"correct": c != opus_code, "grounded": True, "useful": True, "harmful_stale": False}
    )
    res = GR.score("E1", record)
    assert res["by_group"]["gemini"]["units"] == 2 and res["by_group"]["gemini"]["correct"] == 1.0
    assert res["by_group"]["opus"]["correct"] == 0.0  # the stricter label on the split
    assert res["verdict"]["decision"] == "GO-Gemini"
    split = GR.build_split("E1", record)
    assert split["split_units"] == 1
    rows_c = [json.loads(x) for x in (gdir / "reader-C" / "units.jsonl").read_text().splitlines()]
    assert [r["code"] for r in rows_c] == [opus_code]
    assert "Verdict: GO-Gemini" in GR.render_score(res)


def test_e2_kit_has_whole_cards_and_coverage_sheet(priv):
    _write_all_packets()
    R.write()
    record, digest = R.verify("E2")
    record["_sha"] = digest
    pk = P.load_packets("E2")[0]
    card = {
        "now": [{"text": "Backoff is capped.", "evidence": [{"source": "v11", "quote": DECISION}]}],
        "decisions": [],
        "open_unknown": ["nothing open"],
        "as_of": "2026-09-20",
    }
    for run in GR.run_labels(record):
        units = G.check_output(pk, {"abstain": False, "card": card, "merges": []})
        _check_file("E2", run, {"E2-001": {"status": "ok", "units": units}})
    info = GR.build_kit("E2", record, [pk])
    assert info["units"] == 3 and info["coverage_cards"] == 3
    rows = [
        json.loads(x) for x in (priv / "grading" / "E2" / "reader-B" / "units.jsonl").read_text().splitlines()
    ]
    assert all(r["content"]["whole_card"]["as_of"] == "2026-09-20" for r in rows)
    cov = [
        json.loads(x)
        for x in (priv / "grading" / "E2" / "reader-B" / "coverage.jsonl").read_text().splitlines()
    ]
    assert len(cov) == 3 and all(len(c["must_know"]) == 8 and "Backoff is capped." in c["card"] for c in cov)
    assert all(set(c["covered"]) == {f"m{i}" for i in range(1, 9)} for c in cov)


def test_e0_score_decisions(priv):
    key = {
        "units": {
            **{f"c{i}": {"stratum": "curated", "hiding": False} for i in range(10)},
            "i1": {"stratum": "imported", "hiding": True},
        },
        "coverage": {},
    }
    final = {c: {"correct": True, "harmful_stale": False} for c in key["units"]}
    assert GR._score_e0(key, final, BARS)["verdict"]["decision"].startswith("AL2 promotion")
    final["c0"]["harmful_stale"] = True
    assert GR._score_e0(key, final, BARS)["verdict"]["decision"].startswith("between")
    for c in ("c1", "c2", "c3"):
        final[c]["correct"] = False
    assert GR._score_e0(key, final, BARS)["verdict"]["decision"] == "AL2 stays observer"


def test_coverage_uses_the_stricter_reader():
    key = {"units": {}, "coverage": {"K1": {"run": "gemini-r1", "arm": "gemini", "facts": 4}}}
    a = {"K1": {"m1": True, "m2": True, "m3": True, "m4": False}}
    b = {"K1": {"m1": True, "m2": False, "m3": True, "m4": False}}
    cov = GR.coverage(key, [a, b], {"gemini-r1"})
    assert cov == {"cards": 1, "coverage": 0.5, "missing_labels": 0}
