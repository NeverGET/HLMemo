"""Migration kit v1.1 (BACKLOG "Migration kit v1.1", the second migration's feedback): spec keys, lint
additions, the marker table and its drift test, roundtrip, containment, the personal-data scan, nlm-check,
card, the run hint, the read-only prod verify, local_stack env/--embed and the blind-check relay. No server is
contacted."""

from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import os
import random
import re
import stat
import string
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from hlmemo.cli.migrate import migrate_app
from hlmemo.core.explicit_supersession import MARKERS
from hlmemo.migrate import markers, runner
from hlmemo.migrate import scan as scanmod
from hlmemo.migrate import seal as sealmod
from hlmemo.migrate.batches import batch_counts, load
from hlmemo.migrate.containment import containment
from hlmemo.migrate.lint import lint
from hlmemo.migrate.nlmcheck import check as nlm_check
from hlmemo.migrate.nlmcheck import structural_reasons
from hlmemo.migrate.roundtrip import compare, roundtrip
from hlmemo.migrate.spec import SpecError, load_spec

REPO = Path(__file__).resolve().parents[2]

FACT = """---
title: "API · the cache TTL is 300 seconds"
date: 2026-07-10
tags: [api]
source_path: src/cache.py
---
The API cache TTL is 300 seconds. Why: at 60 seconds the cache stampeded.
"""

LESSON = """---
title: "API · Measure the cache under peak load before changing its TTL"
date: 2026-07-12
tags: [api, active, python@3.12]
source_path: notes/cache.md
---
# API · Measure the cache under peak load before changing its TTL

## Mistake
When: the TTL was lowered without a load test.

## Fix
Do: run the load test first. Avoid: changing the TTL on a guess.

## Context
Evidence: "stampede at peak" (notes/cache.md:3). Scope: python@3.12. Status: active.
"""

SESSIONS = """## 2026-07-30 API · first load test: p95 412 ms
The first load test at 50 rps gave p95 412 ms.

## 2026-08-02 API · second load test: p95 210 ms
The second load test gave p95 210 ms.
"""

DECISIONS = """D-001 | 2026-07-01 | ACCEPTED | Cache layer A for the API.
D-002 | 2026-08-05 | ACCEPTED | Cache layer B for the API — D-001'i geçersiz kılar.
"""


def _tree(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def _spec(tmp: Path, extra: str = "") -> Path:
    s = tmp / "migration.toml"
    s.write_text(
        'slug = "kit-test"\ntz = "Europe/Istanbul"\n'
        '[paths]\ncurated = "curated"\nprivate = "private"\n'
        '[tags]\nclosed = ["api"]\n'
        f"{extra}\n"
        '[local]\nserver_url = "http://127.0.0.1:8799/mcp"\ndevice = "mig-kit-test"\n'
        '[prod]\nserver_url = "https://memory.example.org/mcp"\ndevice = "dev-1"\n',
        encoding="utf-8",
    )
    return s


def _good(tmp: Path, extra: str = "", more: dict[str, str] | None = None) -> Path:
    _tree(
        tmp / "curated",
        {
            "status/api/api-cache-ttl.md": FACT,
            "lessons/api/api-load-test-first.md": LESSON,
            "sessions/api/api-2026-08.md": SESSIONS,
            "decisions/DECISIONS.md": DECISIONS,
            **(more or {}),
        },
    )
    return _spec(tmp, extra)


def _rand(alphabet: str, n: int, seed: int = 11) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice(alphabet) for _ in range(n))


# --------------------------------------------------------------------------- spec
def test_example_spec_still_loads_and_new_keys_parse(tmp_path: Path) -> None:
    s = load_spec(REPO / "tools" / "migrate" / "migration.example.toml")
    assert s.exclude == () and s.scopes == frozenset() and s.scan_allow == frozenset()
    p = _good(
        tmp_path,
        extra=(
            '[lint]\nexclude = ["packages/**", "*/NOTES.md"]\n'
            '[scan]\nallow = ["email:status/api/a.md:3"]\nallow_values = ["help@example.org"]\n'
            '[[scan.patterns]]\nid = "account-id"\nregex = "ACC-[0-9]{4}"\n'
        ),
    )
    s = load_spec(p)
    assert s.exclude == ("packages/**", "*/NOTES.md")
    assert s.excluded("status/api/NOTES.md") and s.excluded("packages/p1/x.md")
    assert not s.excluded("status/api/api-cache-ttl.md")
    assert s.scan_allow == {"email:status/api/a.md:3"} and s.scan_allow_values == {"help@example.org"}
    assert s.scan_patterns[0].id == "account-id" and s.scan_patterns[0].mask == "<ACCOUNT-ID>"


def test_spec_refuses_bad_new_keys(tmp_path: Path) -> None:
    for extra, msg in (
        ('[scan]\nallow = ["just-a-value"]', "rule"),
        ("[lint]\nexclude = [1]", "exclude"),
        ('[[scan.patterns]]\nid = "x"\nregex = "("', "bad regex"),
    ):
        with pytest.raises(SpecError, match=msg):
            load_spec(_good(tmp_path, extra=extra))
    p = tmp_path / "scopes.toml"
    p.write_text('slug = "kit-test"\n[paths]\ncurated = "c"\n[tags]\nscopes = ["python"]\n')
    with pytest.raises(SpecError, match="scopes"):
        load_spec(p)


# --------------------------------------------------------------------------- lint
def _lint(spec_path: Path) -> Any:
    spec = load_spec(spec_path)
    return lint(spec, load(spec))


def test_side_files_are_silent_and_excluded_files_are_never_imported(tmp_path: Path) -> None:
    more = {
        "status/api/sources.txt": "a list of sources\n",
        "status/api/NOTES.md": "curator notes, no frontmatter\n",
    }
    res = _lint(_good(tmp_path, more=more))
    assert not any("sources.txt" in x for x in res.errors + res.warnings)  # non-markdown: silent
    assert any("NOTES.md" in x for x in res.errors)  # a stray .md is an item without frontmatter

    res = _lint(_spec(tmp_path, extra='[lint]\nexclude = ["*/NOTES.md"]'))
    assert res.ok, res.errors
    spec = load_spec(tmp_path / "migration.toml")
    loaded = load(spec)
    assert not any("NOTES.md" in r.file for ld in loaded for r in ld.parsed.records)
    assert not any(f.endswith("NOTES.md") for ld in loaded for f in ld.parsed.files)
    out = sealmod.write(spec, loaded, expect=batch_counts(loaded))
    assert not any(f.endswith("NOTES.md") for f in out["files"])


def test_one_entry_episode_file_titled_by_its_file_name_is_flagged(tmp_path: Path) -> None:
    one = "## 2026-05-01 QIBLA · compass calibration fixed\nThe compass now calibrates on start.\n"
    res = _lint(_good(tmp_path, more={"sessions/qibla/qibla-2026-05.md": one}))
    assert any("qibla-2026-05.md" in w and "FILE NAME" in w for w in res.warnings)
    titled = '---\ntitle: "QIBLA · compass calibration fixed"\ndate: 2026-05-01\n---\n' + one
    (tmp_path / "curated/sessions/qibla/qibla-2026-05.md").write_text(titled)
    res = _lint(tmp_path / "migration.toml")
    assert not any("FILE NAME" in w for w in res.warnings)


def test_size_limits_title_wording_and_owner_decision_ids(tmp_path: Path) -> None:
    long_fact = FACT.replace("Why:", "Why: " + "detail " * 260)
    long_title = SESSIONS.replace("first load test: p95 412 ms", "first load test " + "x" * 80)
    more = {"status/api/api-long.md": long_fact.replace("cache TTL is 300", "long fact")}
    res = _lint(_good(tmp_path, more=more))
    assert any("api-long.md" in w and "> 1500" in w for w in res.warnings)
    (tmp_path / "curated/sessions/api/api-2026-08.md").write_text(long_title)
    res = _lint(tmp_path / "migration.toml")
    assert any("whole heading" in w for w in res.warnings)
    (tmp_path / "curated/status/api/api-cache-ttl.md").write_text(
        FACT + "As the owner decided in OD-07 and D-12.\n"
    )
    res = _lint(tmp_path / "migration.toml")
    assert any("api-cache-ttl.md" in w and "owner-decision ids" in w for w in res.warnings)
    assert not any("DECISIONS.md" in w and "owner-decision" in w for w in res.warnings)  # D-001 rows are fine


def test_scope_tags_outside_the_spec_list_are_errors(tmp_path: Path) -> None:
    res = _lint(_good(tmp_path, extra=""))
    assert res.ok
    p = _spec(tmp_path)
    p.write_text(p.read_text().replace('closed = ["api"]', 'closed = ["api"]\nscopes = ["python@3.11"]'))
    res = _lint(p)
    assert any("scope tag 'python@3.12'" in e for e in res.errors)


# --------------------------------------------------------------------------- markers
def test_every_marker_has_a_wording_and_a_row() -> None:
    assert {m.regex.pattern for m in MARKERS} == set(markers.WORDINGS)
    md = markers.markdown()
    assert md.startswith(
        "| language | relation | wording that triggers it | where it counts | the declaring item is |"
    )
    assert len([ln for ln in md.splitlines() if ln.startswith("| ") and "`" in ln]) == len(MARKERS)
    rows = {(r.relation, r.language): r for r in markers.rows()}
    assert rows[("instead_of", "English")].where == "decision rows; D-id right after"
    assert rows[("instead_of", "Turkish")].where == "decision rows; D-id right before"
    assert rows[("supersedes", "English")].where == "anywhere; target within 5 words"
    assert rows[("superseded_by", "English")].declaring.startswith("older")


def test_playbook_marker_block_equals_the_generated_table() -> None:
    playbook = (REPO / "docs" / "migration" / "PLAYBOOK.md").read_text(encoding="utf-8")
    m = re.search(r"<!-- markers:begin -->\n(.*?)<!-- markers:end -->", playbook, re.S)
    if m is None:
        pytest.skip("PLAYBOOK has no <!-- markers:begin --> block on this branch (the docs branch adds it)")
    assert m.group(1).strip() == markers.markdown().strip(), (
        "PLAYBOOK §9 marker table drifted from the code: paste the output of `hlm migrate markers`"
    )


def test_markers_cli_prints_the_table() -> None:
    res = CliRunner().invoke(migrate_app, ["markers"])
    assert res.exit_code == 0 and res.output.strip() == markers.markdown().strip()
    res = CliRunner().invoke(migrate_app, ["markers", "--format", "json"])
    assert res.exit_code == 0 and len(json.loads(res.output)) == len(MARKERS)


# --------------------------------------------------------------------------- roundtrip
class ExportServer:
    """hlm.export in manifest and full views over stored bodies keyed by source path."""

    def __init__(self, bodies: dict[str, str]) -> None:
        self.items = []
        for i, (key, body) in enumerate(sorted(bodies.items()), start=1):
            system, path = key.split(":", 1)
            self.items.append(
                {
                    "logical_id": i,
                    "version_id": i,
                    "kind": "fact",
                    "valid_to": None,
                    "source": {"system": system, "path": path},
                    "body": body,
                    "body_range": [0, len(body)],
                }
            )

    async def __call__(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        assert tool == "hlm.export"
        items = self.items
        if args.get("logical_ids"):
            items = [it for it in items if it["logical_id"] in args["logical_ids"]]
        if args.get("view") != "full":
            items = [{k: v for k, v in it.items() if k not in ("body", "body_range")} for it in items]
        return {"items": [dict(x) for x in items], "next_cursor": None, "as_of": {}}


def test_roundtrip_counts_equal_missing_and_mismatched(tmp_path: Path) -> None:
    spec = load_spec(_good(tmp_path))
    loaded = load(spec)
    recs = {r.key: r.body for ld in loaded for r in ld.parsed.records}
    stored = dict(recs)
    keys = sorted(stored)
    stored[keys[0]] = stored[keys[0]][:20]  # cut, as the legacy store did
    del stored[keys[1]]
    res = asyncio.run(roundtrip(ExportServer(stored), spec.slug, loaded))
    assert res.records == len(recs) and res.equal == len(recs) - 2
    assert len(res.missing) == 1 and len(res.mismatched) == 1 and not res.ok
    assert res.mismatched[0]["first_difference_at"] == 20
    res = asyncio.run(roundtrip(ExportServer(recs), spec.slug, loaded))
    assert res.ok and res.equal == len(recs) and res.non_ascii >= 1  # the Turkish decision row


def test_roundtrip_report_never_prints_a_token() -> None:
    tok = "ghp_" + _rand(string.ascii_letters + string.digits, 36)
    d = compare("markdown:status/x.md", f"before {tok} after", f"before {tok} AFTER")
    assert d is not None and tok not in json.dumps(d)


# --------------------------------------------------------------------------- containment
def test_containment_separates_bundled_from_new_text(tmp_path: Path) -> None:
    original = " ".join(f"word{i}" for i in range(200))
    (tmp_path / "orig").mkdir()
    (tmp_path / "orig" / "a.md").write_text(original)
    (tmp_path / "export").mkdir()
    (tmp_path / "export" / "bundle.md").write_text(
        "# Bundle\n" + " ".join(f"word{i}" for i in range(50, 150))
    )
    (tmp_path / "export" / "new.md").write_text(" ".join(f"fresh{i}" for i in range(60)))
    (tmp_path / "export" / "short.md").write_text("too short")
    rows, summary = containment(tmp_path / "export", [tmp_path / "orig"], 8)
    share = {r.file: r.share for r in rows}
    # the bundle's own heading adds one shingle the originals do not hold
    assert share["bundle.md"] > 0.95 and share["new.md"] == 0.0 and share["short.md"] is None
    assert summary["at_least_0.85"] == 1 and summary["below_0.50"] == 1 and summary["too_short"] == 1


# --------------------------------------------------------------------------- scan
def _tckn() -> str:
    rng = random.Random(3)
    d = [rng.randint(1, 9)] + [rng.randint(0, 9) for _ in range(8)]
    d.append(((sum(d[0:9:2]) * 7) - sum(d[1:8:2])) % 10)
    d.append(sum(d) % 10)
    return "".join(map(str, d))


def test_scan_finds_personal_data_masks_it_and_honours_allow(tmp_path: Path) -> None:
    mail = "ali.veli" + "@" + "example.com"
    hex40 = _rand("0123456789abcdef", 40)
    tckn = _tckn()
    tok = "ghp_" + _rand(string.ascii_letters + string.digits, 36, seed=5)
    secret_value = "Pa55-" + _rand(string.ascii_letters, 10, seed=9)
    body = (
        f"Contact {mail} for access.\n"
        f"Device {hex40} was registered.\n"
        "The VM answers on 10.20.30.40 and fd00:abcd::17 (private), and on 8.8.4.4.\n"
        f"National id {tckn}.\n"
        "The key is ~/.ssh/id_ed25519 and the push key AuthKey_ABC123.p8.\n"
        f"Token {tok} in a note.\n"
        f"The owner's value {secret_value} appears.\n"
        "Loopback 127.0.0.1 and a version 1.2.3 are fine.\n"
    )
    p = _good(tmp_path, more={"status/api/api-leaky.md": FACT + body})
    spec = load_spec(p)
    kv = tmp_path / "known.txt"
    kv.write_text(f"# values that must not appear\n{secret_value}\n")
    kv.chmod(0o600)
    hits, stats = scanmod.scan(spec, scanmod.load_known_values(kv))
    rules = {h.rule for h in hits}
    assert {"email", "hex40", "ipv4-private", "ipv6-private", "ipv4", "tr-national-id", "key-path"} <= rules
    assert "secret-github-token" in rules and "known" in rules
    dump = json.dumps([h.__dict__ for h in hits])
    for raw in (mail, hex40, tckn, tok, secret_value, "10.20.30.40", "fd00:abcd::17"):
        assert raw not in dump, raw
    assert not any(
        "127.0.0.1" in h.context and h.rule.startswith("ipv4") for h in hits if "Loopback" in h.context
    )
    line = next(h.line for h in hits if h.rule == "email")
    p.write_text(
        p.read_text()
        + f'[scan]\nallow = ["email:status/api/api-leaky.md:{line}"]\nallow_values = ["8.8.4.4"]\n'
    )
    hits2, stats2 = scanmod.scan(load_spec(p), ())
    assert "email" not in {h.rule for h in hits2} and "ipv4" not in {h.rule for h in hits2}
    assert stats2["allowed"] >= 2


def test_scan_refuses_a_known_values_file_others_can_read(tmp_path: Path) -> None:
    kv = tmp_path / "known.txt"
    kv.write_text("x\n")
    kv.chmod(0o644)
    with pytest.raises(PermissionError):
        scanmod.load_known_values(kv)


def test_scan_tool_exit_codes(tmp_path: Path) -> None:
    p = _good(tmp_path)
    tool = REPO / "tools" / "migrate" / "scan.py"
    env = {**os.environ, "PYTHONPATH": str(REPO / "src")}
    py = sys.executable
    clean = subprocess.run([py, str(tool), "--spec", str(p)], capture_output=True, text=True, env=env)
    assert clean.returncode == 0, clean.stderr + clean.stdout
    (tmp_path / "curated/status/api/api-cache-ttl.md").write_text(FACT + "mail " + "a" + "@" + "b.org\n")
    dirty = subprocess.run([py, str(tool), "--spec", str(p)], capture_output=True, text=True, env=env)
    assert dirty.returncode == 1 and "<EMAIL>" in dirty.stdout and "a@b.org" not in dirty.stdout


# --------------------------------------------------------------------------- nlm-check
def test_nlm_check_structural_signals() -> None:
    assert structural_reasons("A full sentence.\n") == []
    assert structural_reasons("- a list item without a period\n") == []
    assert "ends-mid-sentence" in structural_reasons("The prayer times come from the API and the")
    assert "open-fence" in structural_reasons("Text.\n```ts\nconst x = 1\n")
    assert "open-construct" in structural_reasons("Uses `SharedValue")


def test_nlm_check_finds_a_note_cut_before_an_angle_token(tmp_path: Path) -> None:
    notes = tmp_path / "export" / "notes"
    notes.mkdir(parents=True)
    head = "The animation hook keeps the offset in a reanimated shared value typed as SharedValue"
    (notes / "cut.md").write_text(f'---\ntitle: "STATUS: animations"\nnlm_note_id: n-1\n---\n{head}')
    (notes / "fine.md").write_text('---\ntitle: "LESSON: ok"\n---\nA complete note.\n')
    orig = tmp_path / "serena"
    orig.mkdir()
    (orig / "anim.md").write_text(head + "<number> and updates it on every frame. " + "More text. " * 30)
    flags, checked = nlm_check(tmp_path / "export", [orig])
    assert checked == 2 and [f.note_id for f in flags] == ["n-1"]
    assert "cut-before-angle" in flags[0].reasons and flags[0].original.endswith("anim.md")
    res = CliRunner().invoke(migrate_app, ["nlm-check", "--export", str(tmp_path / "export")])
    assert res.exit_code == 1 and "1 of 2 note(s) flagged" in res.output


def test_nlm_check_original_continues(tmp_path: Path) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()
    text = " ".join(f"alpha{i}" for i in range(80)) + "."
    (notes / "n.md").write_text(text)
    orig = tmp_path / "o.md"
    orig.write_text(text[:-1] + " " + " ".join(f"beta{i}" for i in range(60)) + ".")
    flags, _ = nlm_check(notes, [orig])
    assert flags and flags[0].reasons == ["original-continues"]


# --------------------------------------------------------------------------- card
def test_card_command_counts_with_the_server_meter(tmp_path: Path) -> None:
    short = tmp_path / "card.md"
    short.write_text("# Project\n\nA short card.\n")
    res = CliRunner().invoke(migrate_app, ["card", "--file", str(short)])
    assert res.exit_code == 0 and "o200k tokens" in res.output and ": ok" in res.output
    long = tmp_path / "long.md"
    long.write_text("# Project\n\n" + "word " * 600)
    res = CliRunner().invoke(migrate_app, ["card", "--file", str(long)])
    assert res.exit_code == 1 and ("too long" in res.output or "REFUSED" in res.output)


# --------------------------------------------------------------------------- run hint and prod reads
class _Engine:
    def __init__(self, counts: dict[str, int]) -> None:
        self.counts = counts

    async def classify(self, call: Any, importer: str, parsed: Any, slug: str) -> runner.Classified:
        return runner.Classified("p", [], {"counts": self.counts}, frozenset())

    async def open_keys(self, call: Any, slug: str) -> set[str]:
        return set()

    async def write(self, call: Any, c: Any) -> dict[str, Any]:
        raise AssertionError("never written")


@contextlib.asynccontextmanager
async def _session(_t: Any):
    async def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("no server")

    yield call


def test_unchanged_stop_names_verify(tmp_path: Path) -> None:
    spec = load_spec(_good(tmp_path))
    with pytest.raises(runner.HardStop, match=r"hlm migrate verify --target local"):
        asyncio.run(
            runner.run(
                spec, load(spec), "local", session=_session, engine=_Engine({"unchanged": 2}), apply=True
            )
        )


def test_prod_verify_needs_the_seal_but_not_the_write_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = load_spec(_good(tmp_path))
    loaded = load(spec)
    monkeypatch.delenv(runner.ALLOW_PROD_ENV, raising=False)
    with pytest.raises(runner.RunRefused, match="seal"):
        asyncio.run(runner.run(spec, loaded, "prod", session=_session, engine=_Engine({}), verify=True))
    sealmod.write(spec, loaded, expect=batch_counts(loaded))
    out = asyncio.run(
        runner.run(spec, loaded, "prod", session=_session, engine=_Engine({"unchanged": 1}), verify=True)
    )
    assert out and all(r["mode"] == "verify" for r in out)
    with pytest.raises(runner.RunRefused, match=runner.ALLOW_PROD_ENV):
        asyncio.run(runner.run(spec, loaded, "prod", session=_session, engine=_Engine({}), apply=True))


def test_prod_roundtrip_is_refused_without_a_seal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """roundtrip is a read: on prod it needs the seal like `verify`, refused before any network call."""
    monkeypatch.delenv(runner.ALLOW_PROD_ENV, raising=False)
    res = CliRunner().invoke(migrate_app, ["roundtrip", "--spec", str(_good(tmp_path)), "--target", "prod"])
    assert res.exit_code == 65 and "seal" in res.output


# --------------------------------------------------------------------------- local_stack
def test_local_stack_env_exports_the_project(tmp_path: Path) -> None:
    script = REPO / "tools" / "migrate" / "local_stack.sh"
    out = subprocess.run(
        ["bash", str(script), "env", "kit-env"],
        capture_output=True,
        text=True,
        env={**os.environ, "HLM_MIG_STATE": str(tmp_path / "state")},
    )
    assert out.returncode == 0, out.stderr
    assert "HLM_PROJECT=kit-env" in out.stdout and "HLM_DEVICE_NAME=mig-kit-env" in out.stdout
    bad = subprocess.run(
        ["bash", str(script), "up", "kit-env", "--frobnicate"],
        capture_output=True,
        text=True,
        env={**os.environ, "HLM_MIG_STATE": str(tmp_path / "state")},
    )
    assert bad.returncode == 2 and "--embed" in bad.stderr
    text = script.read_text()
    assert "hlmemo.worker.main" in text and "worker.pid" in text


# --------------------------------------------------------------------------- blind-check relay
def _blindcheck() -> Any:
    spec = importlib.util.spec_from_file_location(
        "blindcheck_v11", REPO / "tools" / "migrate" / "blindcheck" / "blindcheck.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_relay_uses_an_hlm_only_mcp_config_without_the_token(tmp_path: Path) -> None:
    bc = _blindcheck()
    cfg = bc.write_mcp_config(tmp_path, "https://memory.example.org")
    assert stat.S_IMODE(cfg.stat().st_mode) == 0o600
    data = json.loads(cfg.read_text())
    assert list(data["mcpServers"]) == ["hlm"]
    assert data["mcpServers"]["hlm"]["url"] == "https://memory.example.org/mcp"
    assert data["mcpServers"]["hlm"]["headers"]["Authorization"] == "Bearer ${HLM_DEVICE_TOKEN}"
    cmd = bc.relay_cmd("prompt", "sonnet", cfg)
    i = cmd.index("--mcp-config")
    assert "--restricted" in cmd and "--strict-mcp-config" in cmd and cmd[i + 1] == str(cfg)


def test_ask_exit_code_fails_a_broken_relay() -> None:
    bc = _blindcheck()
    assert bc.ask_exit_code(["no_call"] * 18) == 1  # K4: everything no_call used to exit 0
    assert bc.ask_exit_code(["ok", "ok", "relay_mismatch"]) == 1
    assert bc.ask_exit_code(["ok", "tool_error"]) == 0  # a server-side error is a finding, not a broken relay
    assert bc.ask_exit_code(["exists", "exists"]) == 0
    assert bc.ask_exit_code(["tool_error"]) == 1
