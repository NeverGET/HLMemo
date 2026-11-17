"""tools/desk/desk.py: batch validation, answer folding, write guards, the server's host and token checks."""

from __future__ import annotations

import http.client
import json
import sys
import threading
from pathlib import Path

import pytest

DESK_DIR = Path(__file__).resolve().parents[2] / "tools" / "desk"
if str(DESK_DIR) not in sys.path:
    sys.path.insert(0, str(DESK_DIR))

import desk  # noqa: E402


def _batch(bid: str = "b1") -> dict:
    return {
        "batch": bid,
        "title": "Test batch",
        "questions": [
            {
                "id": "q1",
                "title": "One?",
                "place": "home",
                "options": ["yes", "no"],
                "refs": [{"label": "commit", "url": "https://github.com/x/y/commit/abc"}],
            },
            {"id": "q2", "title": "Two?", "place": "work", "kind": "approval", "group": "g"},
            {"id": "q3", "title": "Three?", "group": "g", "refs": [{"label": "item", "clue": "v12"}]},
        ],
    }


def _write(data: Path, doc: dict) -> None:
    (data / "questions" / f"{doc['batch']}.json").write_text(json.dumps(doc), encoding="utf-8")


def _ans(data: Path, payload: dict) -> dict:
    """Answer as the page does: with the hashes of the questions it shows."""
    batches = desk.load_batches(data)
    hashes = desk.batch_hashes(batches[payload["batch"]]) if payload["batch"] in batches else {}
    full = {"qhash": {q: hashes.get(q) for q in payload.get("qids", []) if isinstance(q, str)}, **payload}
    return desk.append_answer(data, batches, full, "home")


@pytest.fixture
def data(tmp_path: Path) -> Path:
    (tmp_path / "questions").mkdir()
    _write(tmp_path, _batch())
    return tmp_path


def test_valid_batch_has_no_problems() -> None:
    assert desk.validate_batch(_batch(), "b1") == []


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda b: b.update(batch="other"), "file name"),
        (lambda b: b["questions"][1].update(id="q1"), "repeated"),
        (lambda b: b["questions"][0].update(place="mars"), "place"),
        (lambda b: b["questions"][0]["refs"].append({"label": "x", "url": "http://plain"}), "https://"),
        (
            lambda b: b["questions"][0]["refs"].append({"label": "x", "url": "https://a", "path": "b"}),
            "exactly one",
        ),
        (lambda b: b["questions"][2]["refs"].append({"label": "x", "clue": "12"}), "clue"),
        (lambda b: b.update(questions=[]), "non-empty"),
    ],
)
def test_invalid_batches_are_reported(mutate, needle: str) -> None:
    b = _batch()
    mutate(b)
    assert any(needle in p for p in desk.validate_batch(b, "b1"))


@pytest.mark.parametrize("raw", [b"{not json", b"\xff\xfe{}"])
def test_unreadable_file_is_listed_with_problems_not_questions(data: Path, raw: bytes) -> None:
    (data / "questions" / "bad.json").write_bytes(raw)
    batches = desk.load_batches(data)
    assert batches["bad"]["problems"] and batches["bad"]["questions"] == []
    assert batches["b1"]["questions"]  # one bad file does not take the others down
    with pytest.raises(desk.DeskError):
        _ans(data, {"batch": "bad", "qids": ["q1"], "type": "later"})


def test_newest_answer_wins_and_reopen_clears(data: Path) -> None:
    _ans(data, {"batch": "b1", "qids": ["q1"], "type": "answer", "option": "yes"})
    _ans(data, {"batch": "b1", "qids": ["q2", "q3"], "type": "rule", "text": "R"})
    _ans(data, {"batch": "b1", "qids": ["q3"], "type": "reopen", "text": "ignored"})
    cur = desk.current_answers(desk.read_answers(data, "b1"))
    assert cur["q1"]["option"] == "yes"
    assert cur["q2"]["type"] == "rule" and cur["q2"]["text"] == "R" and cur["q2"]["option"] is None
    assert "q3" not in cur
    rows = {r["batch"]: r for r in desk.summary(data, desk.load_batches(data))}
    assert rows["b1"]["open"] == 1 and rows["b1"]["open_by_place"]["any"] == 1


def test_an_answer_to_an_earlier_wording_does_not_count(data: Path) -> None:
    _ans(data, {"batch": "b1", "qids": ["q1"], "type": "answer", "option": "yes"})
    b = _batch()
    b["questions"][0]["title"] = "A different question, same id"
    _write(data, b)
    doc = desk.load_batches(data)["b1"]
    view = {q["id"]: q for q in desk.batch_view(data, doc)["questions"]}
    assert view["q1"]["answer"] is None
    assert desk.summary(data, desk.load_batches(data))[0]["open"] == 3


def test_a_page_showing_old_text_gets_a_conflict(data: Path) -> None:
    stale = desk.batch_hashes(desk.load_batches(data)["b1"])
    b = _batch()
    b["questions"][0]["body"] = "rewritten"
    _write(data, b)
    payload = {"batch": "b1", "qids": ["q1"], "type": "later", "qhash": {"q1": stale["q1"]}}
    with pytest.raises(desk.DeskConflict):
        desk.append_answer(data, desk.load_batches(data), payload, "home")
    with pytest.raises(desk.DeskConflict):  # no hash at all is refused too
        desk.append_answer(data, desk.load_batches(data), {**payload, "qhash": None}, "home")


def test_torn_and_foreign_lines_do_not_hide_other_answers(data: Path) -> None:
    _ans(data, {"batch": "b1", "qids": ["q1"], "type": "dont_know"})
    with (data / "answers" / "b1.jsonl").open("ab") as fh:
        fh.write(b'5\n{"qids": "q2", "type": "approve"}\n\xff\xfe\n{"qids": ["q2"], "type": "appr')
    assert set(desk.current_answers(desk.read_answers(data, "b1"))) == {"q1"}


@pytest.mark.parametrize(
    "payload",
    [
        {"batch": "nope", "qids": ["q1"], "type": "later"},
        {"batch": "b1", "qids": ["zz"], "type": "later"},
        {"batch": "b1", "qids": ["q1", "q1"], "type": "later"},
        {"batch": "b1", "qids": ["q1"], "type": "delete"},
        {"batch": "b1", "qids": ["q1"], "type": "answer", "text": "  "},
        {"batch": "b1", "qids": ["q1"], "type": "rule"},
        {"batch": "b1", "qids": ["q1"], "type": "approve_conditional", "text": ""},
        {"batch": "b1", "qids": ["q1", "q3"], "type": "answer", "option": "yes"},  # q3 offers no options
        {"batch": "b1", "qids": ["q1"], "type": "answer", "text": "x" * 20_001},
    ],
)
def test_bad_answers_are_refused_and_nothing_is_written(data: Path, payload: dict) -> None:
    with pytest.raises(desk.DeskError):
        _ans(data, payload)
    assert not (data / "answers" / "b1.jsonl").exists()


def test_answer_file_is_private(data: Path) -> None:
    _ans(data, {"batch": "b1", "qids": ["q1"], "type": "later"})
    assert (data / "answers" / "b1.jsonl").stat().st_mode & 0o077 == 0


def test_file_viewer_shows_listed_text_files_only(data: Path) -> None:
    (data / "note.md").write_text("hello", encoding="utf-8")
    assert desk.safe_local_path(data, str(data / "note.md")).name == "note.md"
    decisions = "docs/decisions/DECISIONS.md"
    with pytest.raises(desk.DeskError, match="lists"):
        desk.safe_local_path(data, decisions, set())
    listed = {desk._resolve(decisions)}
    assert desk.safe_local_path(data, decisions, listed).name == "DECISIONS.md"
    bad = ["pyproject.toml", ".env", "docs/../.env", "/etc/passwd", str(data / ".." / "x"), "~/x", "a\x00b"]
    for raw in bad:
        with pytest.raises(desk.DeskError):
            desk.safe_local_path(data, raw, listed | {desk._resolve(raw.replace("\x00", ""))})
    for name in ("deploy.token.md", "prod.env.txt", "api_key.txt", "tokens.json", "x.sqlite"):
        (data / name).write_text("t", encoding="utf-8")
        with pytest.raises(desk.DeskError):
            desk.safe_local_path(data, str(data / name))


@pytest.fixture
def server(data: Path):
    d = desk.Desk(data, "home", 0)
    httpd = desk.http.server.ThreadingHTTPServer(("127.0.0.1", 0), desk.make_handler(d))
    port = httpd.server_address[1]
    d.port = port
    d.allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield d, port
    httpd.shutdown()
    httpd.server_close()


def _req(port: int, method: str, path: str, body: bytes | dict | None = None, headers: dict | None = None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    raw = json.dumps(body).encode() if isinstance(body, dict) else body
    c.request(method, path, body=raw, headers=headers or {})
    r = c.getresponse()
    return r.status, r.read(), dict(r.getheaders())


def test_server_guards(server, data: Path) -> None:
    d, port = server
    tok = {"X-Desk-Token": d.token}
    status, page, headers = _req(port, "GET", "/")
    assert status == 200 and d.token.encode() in page
    assert (
        headers["X-Frame-Options"] == "DENY"
        and "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    )
    assert _req(port, "GET", "/api/batches", headers={"Host": "evil.example:80"})[0] == 421
    view = json.loads(_req(port, "GET", "/api/batch/b1")[1])
    qh = {q["id"]: q["qhash"] for q in view["questions"]}
    ans = {"batch": "b1", "qids": ["q1"], "type": "later", "qhash": {"q1": qh["q1"]}}
    assert _req(port, "POST", "/api/answer", ans, {"Content-Type": "application/json"})[0] == 403
    assert _req(port, "POST", "/api/answer", ans, {"X-Desk-Token": "wrong"})[0] == 403
    assert _req(port, "POST", "/api/answer", ans, {"X-Desk-Token": "töken"})[0] == 403
    assert _req(port, "POST", "/api/answer", b"\xff\xfe", tok)[0] == 400
    assert _req(port, "POST", "/api/answer", b"[" * 100_000, tok)[0] == 400
    assert _req(port, "POST", "/api/answer", {**ans, "qhash": {"q1": "old"}}, tok)[0] == 409
    status, body, _ = _req(port, "POST", "/api/answer", ans, tok)
    assert status == 200 and json.loads(body)["record"]["place"] == "home"
    q1 = next(q for q in json.loads(_req(port, "GET", "/api/batch/b1")[1])["questions"] if q["id"] == "q1")
    assert q1["answer"]["type"] == "later"
    assert _req(port, "GET", "/api/file?path=.env", headers=tok)[0] == 400
    assert _req(port, "GET", "/api/file?path=%00", headers=tok)[0] == 400
    assert _req(port, "GET", "/api/file?path=~nouser/x", headers=tok)[0] == 400
    (data / "n.md").write_text("hi", encoding="utf-8")
    assert _req(port, "GET", f"/api/file?path={data / 'n.md'}")[0] == 403
    assert _req(port, "GET", f"/api/file?path={data / 'n.md'}", headers=tok)[0] == 200
