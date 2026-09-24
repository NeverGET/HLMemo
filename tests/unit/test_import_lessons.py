"""e2e 2026-09-24 quick fixes #1 and §7.1 (report docs/status/E2E-PROD-REPORT.md): the auto-memory
``type`` nested under ``metadata:`` and one lesson item per independent rule of a lesson file.

The fixtures (``tests/fixtures/import/automemory``) copy the SHAPE of real Claude Code auto-memory
files (frontmatter ``name``/``description``/``metadata: {type: …}``; a feedback body with a lead
line, bullets, a ``**Why:**`` paragraph and a second list); their text is synthetic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from hlmemo.core.budget import Meter
from hlmemo.importers import automemory, common, lessons
from hlmemo.importers.cli import parse_source
from hlmemo.importers.common import ImportRecord, Section
from hlmemo.importers.plan import classify

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "import"
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def _parse(source: str, path: Path) -> list[ImportRecord]:
    return parse_source(source, [path], now=NOW, tz=UTC).records


# --------------------------------------------------------------------------- fix 1: metadata.type
def test_frontmatter_reads_one_nested_block() -> None:
    meta, body = common.parse_frontmatter(
        "---\nname: A\ndescription: d\nmetadata:\n  type: feedback\n  origin: x\nafter: 2\n---\nbody\n"
    )
    assert meta == {
        "name": "A",
        "description": "d",
        "metadata": {"type": "feedback", "origin": "x"},
        "after": 2,
    }
    assert body == "body\n"
    # an empty key without indented lines stays "", deeper or mixed indentation is ignored
    meta, _ = common.parse_frontmatter("---\nmetadata:\ntype: project\n---\nx\n")
    assert meta == {"metadata": "", "type": "project"}
    meta, _ = common.parse_frontmatter("---\nmetadata:\n  type: user\n    deep: 1\n---\nx\n")
    assert meta == {"metadata": {"type": "user"}}
    # an indented line with no open block is not a top-level key
    assert common.parse_frontmatter("---\n  type: feedback\nname: n\n---\nx\n")[0] == {"name": "n"}


def test_automemory_type_top_level_or_nested() -> None:
    kind = automemory.kind_for
    assert kind("f.md", {"type": "feedback"}, "") == "lesson"  # older files
    assert kind("f.md", {"metadata": {"type": "feedback"}}, "") == "lesson"  # current Claude Code
    assert kind("f.md", {"metadata": {"type": " Feedback "}}, "") == "lesson"
    for t in ("user", "project", "reference"):
        assert kind("f.md", {"metadata": {"type": t}}, "") == "fact", t
    assert kind("MEMORY.md", {}, "") == "fact" and kind("f.md", {"metadata": "x"}, "") == "fact"
    assert automemory.memory_type({"type": "", "metadata": {"type": "user"}}) == "user"


def test_fixture_directory_kinds() -> None:
    recs = {r.key: r for r in _parse("automemory", FIXTURE / "automemory")}
    kinds = {k.split("#", 1)[0]: r.kind_guess for k, r in recs.items()}
    assert kinds == {
        "automemory:MEMORY.md": "fact",
        "automemory:feedback_deploy_footguns.md": "lesson",
        "automemory:feedback_review_style.md": "lesson",
        "automemory:feedback_testing.md": "lesson",
        "automemory:project_state.md": "fact",
        "automemory:reference_dashboards.md": "fact",
        "automemory:user_profile.md": "fact",
    }
    # a single-rule feedback file stays ONE item with its text (and frontmatter) unchanged
    single = recs["automemory:feedback_review_style.md"]
    assert single.body == (FIXTURE / "automemory" / "feedback_review_style.md").read_text()


# --------------------------------------------------------------------------- fix 2: splitting
def test_multi_rule_feedback_file_becomes_one_lesson_per_rule() -> None:
    recs = [r for r in _parse("automemory", FIXTURE / "automemory") if "footguns" in r.key]
    anchors = [r.key.split("#", 1)[1] for r in recs]
    assert anchors == [
        "always-pin-the-base-image-by-digest-a-floating-t",
        "keep-the-maintenance-page-on-a-separate-host-bec",
        "never-restart-the-database-container-while-a-mig",
        "pass-n-to-every-ssh-call-inside-a-deploy-heredoc",
        "the-cache-was-warmed-before-the-new-release-took",
    ]
    by = dict(zip(anchors, recs, strict=True))
    assert all(r.kind_guess == "lesson" and r.file == "feedback_deploy_footguns.md" for r in recs)
    # labelled rule → the register_lesson layout; the shared context (description, **Why:**, the
    # rule's own list intro) is copied into every rule, another list's intro is not
    labelled = by["the-cache-was-warmed-before-the-new-release-took"].body
    assert labelled.startswith("## Mistake\nthe cache was warmed before the new release took traffic")
    assert "\n\n## Fix\nwarm the cache only after the health check" in labelled
    ctx = labelled.split("## Context\n", 1)[1]
    assert "recurring deployment mistakes" in ctx and "**Why:** each of these cost" in ctx
    assert "Deployment footguns seen on the fixture stack:" in ctx and "Found later:" not in ctx
    # unlabelled rule → its text kept verbatim, then the context
    kept = by["always-pin-the-base-image-by-digest-a-floating-t"].body
    assert kept.startswith("Always pin the base image by digest; a floating tag pulled a different libc")
    assert "Found later:" in kept and "Deployment footguns seen" not in kept
    assert "## Mistake" not in kept
    # the title carries the document title and the rule; no frontmatter in a rule body
    assert by["pass-n-to-every-ssh-call-inside-a-deploy-heredoc"].title.startswith(
        "Deploy footguns › Pass `-n` to every `ssh` call"
    )
    assert not any(r.body.startswith("---") for r in recs)


def test_serena_lesson_bullets_split() -> None:
    recs = [r for r in _parse("serena", FIXTURE / "serena" / "memories") if r.file == "lessons_learned.md"]
    assert [(r.key, r.kind_guess) for r in recs] == [
        ("serena:lessons_learned.md#a-heredoc-that-starts-ssh-must-use-ssh-n-or-it-s", "lesson"),
        ("serena:lessons_learned.md#never-run-the-test-suite-against-the-development", "lesson"),
    ]
    # no shared context (only the H1, which is the title): the body is the rule itself
    assert (
        recs[1].body
        == "Never run the test suite against the development database: it truncates every table.\n"
    )


HEADINGS = """# Deploy lessons

Collected after the 2026 incidents.

## Migrations

Never restart the database while a migration runs.

### Why
The half-applied schema needed a manual restore.

### How to apply
Every deploy that ships an alembic revision.

## Proxy
- Keep the maintenance page on a separate host from the app.
- Reload the proxy with SIGHUP instead of a container restart.

## Why
All of these cost a failed release.
"""


def test_heading_sections_and_label_headings() -> None:
    parts = lessons.split(HEADINGS, {"description": "deploy lessons"}, "Deploy lessons")
    assert parts is not None
    assert [p.anchor for p in parts] == [
        "migrations",
        "proxy~keep-the-maintenance-page-on-a-separate-host-fro",
        "proxy~reload-the-proxy-with-sighup-instead-of-a-contai",
    ]
    mig = parts[0]
    # label headings (### Why / ### How to apply) belong to the rule: derivable → Mistake/Fix/Context
    assert mig.body.startswith("## Mistake\nThe half-applied schema needed a manual restore.")
    assert "## Fix\nNever restart the database while a migration runs." in mig.body
    assert "## Context\nEvery deploy that ships an alembic revision." in mig.body
    assert "Collected after the 2026 incidents." in mig.body and "deploy lessons" in mig.body
    assert mig.lead == "Deploy lessons › Migrations" and mig.kind == "lesson"
    # "## Why" at the rule level is a label, not a rule: it stays inside the Proxy section
    assert (
        parts[1].lead == "Deploy lessons › Proxy › Keep the maintenance page on a separate host from the app."
    )
    assert "All of these cost a failed release." in parts[1].body


def test_what_is_not_split() -> None:
    meta = {"description": "d"}
    single = "Do X always.\n\n**Why:** Y broke.\n**How to apply:** everywhere.\n"
    assert lessons.split(single, meta, "T") is None  # one rule with its reasons
    steps = "Deploy in this order:\n1. build the image first\n2. run the migration next\n"
    assert lessons.split(steps, meta, "T") is None  # numbered = one procedure
    short = "Check these before a release:\n- disk space\n- open certificates to renew\n"
    assert lessons.split(short, meta, "T") is None  # a list inside one rule
    one = "# T\n\n- Only one bullet rule in this whole file here.\n"
    assert lessons.split(one, meta, "T") is None
    fenced = "Intro:\n```\n- not a bullet in a code fence\n- nor this one inside the fence\n```\n"
    assert lessons.split(fenced, meta, "T") is None


def test_derive_labels() -> None:
    assert lessons.derive("Pin the digest. **Why:** a tag moved.") == ("a tag moved.", "Pin the digest.", "")
    assert lessons.derive("ssh swallowed stdin.\nFix: use ssh -n.") == (
        "ssh swallowed stdin.",
        "use ssh -n.",
        "",
    )
    assert lessons.derive("**Mistake:** m\n**Fix:** f\n**Update 2026-09-23:** newer detail\n**Note:** n") == (
        "m",
        "f",
        "**Update 2026-09-23:** newer detail\n\nn",
    )
    assert lessons.derive("Nothing labelled here at all.") is None
    assert lessons.derive("**Context:** only context") is None


def test_split_keys_are_stable_and_edits_are_one_revision() -> None:
    """Revision-safe keys: inserting a rule keeps every other key; editing one rule's tail is ONE
    revision; nothing else is touched."""
    base = (
        "Footguns:\n"
        "- Never restart the database while a migration runs, it corrupts the schema.\n"
        "- Pass -n to ssh inside heredocs or it reads the rest of the script.\n"
        "- Pin base images by digest because floating tags change underneath you.\n"
    )
    first = lessons.split(base, {}, "T")
    edited = base.replace("reads the rest of the script", "reads the rest of the script as input")
    edited = edited.replace(
        "Footguns:\n", "Footguns:\n- Keep the maintenance page on its own host, always.\n"
    )
    second = lessons.split(edited, {}, "T")
    assert first is not None and second is not None
    old = {s.anchor: s.body for s in first}
    new = {s.anchor: s.body for s in second}
    assert set(old) < set(new) and len(new) == len(old) + 1
    changed = [a for a in old if old[a] != new[a]]
    assert changed == ["pass-n-to-ssh-inside-heredocs-or-it-reads-the-re"]


def _rec(key: str, body: str, kind: str) -> ImportRecord:
    system, path = key.split(":", 1)
    return ImportRecord(system, path, path, common.sha256_text(body), path, body, kind, [system])


def test_kind_change_alone_is_a_revision() -> None:
    """Files imported as facts before the fix become lessons on the next run (same body)."""
    rec = _rec("automemory:feedback_x.md", "Rule text.\n", "lesson")
    manifest = [
        {"logical_id": 7, "version_id": 70, "kind": "fact", "source": rec.source(), "tags": ["automemory"]}
    ]
    parsed = common.ParseResult(records=[rec], scopes=[""])
    plan = classify("p", "automemory", parsed, manifest, Meter())
    assert [(e.action, e.logical_id) for e in plan.entries] == [("changed", 7)]
    manifest[0]["kind"] = "lesson"
    plan = classify("p", "automemory", parsed, manifest, Meter())
    assert [e.action for e in plan.entries] == ["unchanged"]


def test_split_sections_are_lesson_sections() -> None:
    parts = lessons.split(
        "- First rule has at least five words.\n- Second rule has five words too.\n", {}, ""
    )
    assert parts is not None and all(isinstance(p, Section) and p.kind == "lesson" for p in parts)
    assert parts[0].lead == "First rule has at least five words."  # no document title: the rule alone
