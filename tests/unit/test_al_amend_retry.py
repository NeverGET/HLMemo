"""Active-librarian harness: pre-registration amendments (spend cap) and retry-by-failure-reason.
Synthetic fixtures only: no network, no model, no real private directory."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path

import httpx
import pytest

AL_DIR = Path(__file__).resolve().parents[2] / "eval" / "active"
if str(AL_DIR) not in sys.path:
    sys.path.insert(0, str(AL_DIR))

import al  # noqa: E402
import al_arms as A  # noqa: E402
import al_common as C  # noqa: E402
import al_packets as P  # noqa: E402
import al_prereg as R  # noqa: E402
from test_al_scorer import _chat, _good_e1_output, _write_all_packets  # noqa: E402


@pytest.fixture
def reg(monkeypatch, tmp_path):
    """A private dir and a COPY of config.json, so the real one is never edited."""
    priv = tmp_path / "priv"
    cfg = tmp_path / "config.json"
    shutil.copy(C.CONFIG_PATH, cfg)
    monkeypatch.setenv(C.PRIVATE_ENV, str(priv))
    monkeypatch.setattr(C, "CONFIG_PATH", cfg)
    monkeypatch.setattr(R, "_claude_version", lambda cli: "test-cli 0")
    _write_all_packets()
    R.write()
    return priv, cfg


def test_amendment_chain_ok(reg):
    priv, cfg = reg
    _, digest = R.verify("E1")
    path, own = R.amend("spend_cap_usd", "12.00", "owner authorised the remaining test budget")
    assert path.name == "AMENDMENT-1.md" and (priv / "AMENDMENT-1.sha256").read_text().startswith(own)
    text = path.read_text()
    assert digest in text and "4.00 -> 12.00" in text and C.sha256_file(cfg) in text
    assert C.load_config()["spend_cap_usd"] == "12.00"
    record, d2 = R.verify("E1")
    assert d2 == digest and [a["new"] for a in record["_amendments"]] == ["12.00"]
    R.amend("spend_cap_usd", "15.5", "second raise")  # a chain of two
    record, _ = R.verify("E3")
    assert [a["n"] for a in record["_amendments"]] == [1, 2]
    with pytest.raises(C.HarnessError, match="below"):
        R.amend("spend_cap_usd", "10", "lower")


def test_tampered_amendment_is_refused(reg):
    priv, _ = reg
    R.amend("spend_cap_usd", "12.00", "ok")
    f = priv / "AMENDMENT-1.md"
    f.write_text(f.read_text().replace("12.00", "99.00"))
    with pytest.raises(C.HarnessError, match="AMENDMENT-1.md does not match"):
        R.verify("E1")
    # a re-hashed forgery that no longer reproduces the config hash is refused too
    forged = f.read_text()
    C.write_text(priv / "AMENDMENT-1.sha256", f"{C.sha256_text(forged)}  AMENDMENT-1.md\n")
    with pytest.raises(C.HarnessError, match="inputs changed.*config_sha256"):
        R.verify("E1")


def test_amending_a_non_allow_listed_field_is_refused(reg):
    priv, cfg = reg
    before = cfg.read_text()
    with pytest.raises(C.HarnessError, match="only spend_cap_usd"):
        R.amend("bars", "1", "move the goalposts")
    assert al.main(["amend", "--field", "max_tokens", "--value", "1", "--reason", "x"]) == 2
    assert cfg.read_text() == before and not list(priv.glob("AMENDMENT-*"))


def test_config_changed_without_amendment_is_refused(reg):
    _, cfg = reg
    cfg.write_text(cfg.read_text().replace('"4.00"', '"40.00"'))
    with pytest.raises(C.HarnessError, match="config_sha256"):
        R.verify("E1")


def test_amendment_does_not_excuse_other_config_changes(reg):
    _, cfg = reg
    R.amend("spend_cap_usd", "12.00", "ok")
    cfg.write_text(cfg.read_text().replace('"grounded_min": 0.95', '"grounded_min": 0.5'))
    with pytest.raises(C.HarnessError, match="config_sha256"):
        R.verify("E1")


def _seed(label_dir, statuses):
    for pid, st in statuses.items():
        C.write_json(label_dir / f"{pid}.json", {"packet_id": pid, "status": st})


def test_retry_reason_selects_only_matching_statuses(reg):
    packets = [{"packet_id": f"E1-00{i}"} for i in range(1, 7)]
    _seed(
        A.out_dir("E1", "gemini-r1"),
        {
            "E1-001": "ok",
            "E1-002": "ProviderUnavailable",
            "E1-003": "SchemaFail",
            "E1-004": "budget_stop",
            "E1-005": "ProviderUnavailable",
        },  # E1-006: never attempted (the cap stopped the run before it)
    )
    sel = A.select_retry("E1", "gemini-r1", packets, ["ProviderUnavailable"])
    assert sel == {"ProviderUnavailable": ["E1-002", "E1-005"]}
    sel = A.select_retry("E1", "gemini-r1", packets, ["budget_stop", "ProviderUnavailable"])
    assert sel["budget_stop"] == ["E1-004", "E1-006"]
    assert sel["ProviderUnavailable"] == ["E1-002", "E1-005"]
    assert A.select_retry("E1", "gemini-r1", packets, ["ok"]) == {"ok": []}  # ok is never retried


def test_retry_reason_reruns_only_selected_and_keeps_history(reg, monkeypatch, capsys):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    cfg = json.loads(json.dumps(C.load_config()))
    cfg["arms"]["gemini"]["env_file"] = "no-such.env"
    pk = P.load_packets("E1")[0]
    pid = pk["packet_id"]
    d = A.out_dir("E1", "gemini-r1")
    sent = []

    def handler(request):
        sent.append(1)
        return httpx.Response(200, json=_chat(json.dumps(_good_e1_output())))

    def rerun():
        return asyncio.run(
            A.run_gemini(
                "E1",
                [pk],
                cfg,
                run=1,
                prereg_sha="x",
                retry_reasons=["ProviderUnavailable"],
                transport=httpx.MockTransport(handler),
            )
        )

    # SchemaFail is not the requested reason: nothing is sent, the record stays as it was
    _seed(d, {pid: "SchemaFail"})
    rerun()
    assert sent == [] and json.loads((d / f"{pid}.json").read_text())["status"] == "SchemaFail"
    _seed(d, {pid: "ProviderUnavailable"})
    C.write_json(d / f"{pid}.raw.json", {"attempts": ["old"]})
    res = rerun()
    assert res["ok"] == 1 and len(sent) == 1
    assert "ProviderUnavailable: 1 packet(s) E1-001" in capsys.readouterr().out
    assert json.loads((d / f"{pid}.json").read_text())["status"] == "ok"
    assert json.loads((d / "history" / f"{pid}.attempt1.json").read_text())["status"] == "ProviderUnavailable"
    assert (d / "history" / f"{pid}.attempt1.raw.json").is_file()
