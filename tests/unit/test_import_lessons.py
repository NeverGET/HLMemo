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
from hlmemo.importers.plan import classify, remap, report

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
    # unlabelled bullets: their SEQUENCE position; a bullet named by a bold label: that name
    assert anchors == ["maintenance-page", "rule-1", "rule-2", "rule-3", "rule-4"]
    by = dict(zip(anchors, recs, strict=True))
    assert all(r.kind_guess == "lesson" and r.file == "feedback_deploy_footguns.md" for r in recs)
    assert by["rule-1"].body.startswith("Never restart the database container while a migration runs")
    # labelled rule → the register_lesson layout; the shared context (description, **Why:**, the
    # rule's own list intro) is copied into every rule, another list's intro is not
    labelled = by["rule-3"].body
    assert labelled.startswith("## Mistake\nthe cache was warmed before the new release took traffic")
    assert "\n\n## Fix\nwarm the cache only after the health check" in labelled
    ctx = labelled.split("## Context\n", 1)[1]
    assert "recurring deployment mistakes" in ctx and "**Why:** each of these cost" in ctx
    assert "Deployment footguns seen on the fixture stack:" in ctx and "Found later:" not in ctx
    # unlabelled rule → its text kept verbatim, then the context
    kept = by["rule-4"].body
    assert kept.startswith("Always pin the base image by digest; a floating tag pulled a different libc")
    assert "Found later:" in kept and "Deployment footguns seen" not in kept
    assert "## Mistake" not in kept
    assert by["maintenance-page"].body.startswith("**Maintenance page:** keep it on a separate host")
    # the title carries the document title and the rule; no frontmatter in a rule body
    assert by["rule-2"].title.startswith("Deploy footguns › Pass `-n` to every `ssh` call")
    assert not any(r.body.startswith("---") for r in recs)


def test_serena_lesson_bullets_split() -> None:
    recs = [r for r in _parse("serena", FIXTURE / "serena" / "memories") if r.file == "lessons_learned.md"]
    assert [(r.key, r.kind_guess) for r in recs] == [
        ("serena:lessons_learned.md#rule-1", "lesson"),
        ("serena:lessons_learned.md#rule-2", "lesson"),
    ]
    # no shared context (only the H1, which is the title): the body is the rule itself
    assert (
        recs[0].body
        == "Never run the test suite against the development database: it truncates every table.\n"
    )


HEADINGS = """# Deploy lessons

Collected after the 2026 incidents.

## 1. Migrations

Never restart the database while a migration runs.

### Why
The half-applied schema needed a manual restore.

### How to apply
Every deploy that ships an alembic revision.

## 2. Proxy
- Keep the maintenance page on a separate host from the app.
- Reload the proxy with SIGHUP instead of a container restart.

## Why
All of these cost a failed release.
"""


def test_heading_sections_and_label_headings() -> None:
    parts = lessons.split(HEADINGS, {"description": "deploy lessons"}, "Deploy lessons")
    assert parts is not None
    # normalized headings are the keys (the numbering is dropped); bullets under a rule heading
    # stay in that rule (Sol 54 #4)
    assert [p.anchor for p in parts] == ["migrations", "proxy"]
    mig, proxy = parts
    # label headings (### Why / ### How to apply) belong to the rule: derivable → Mistake/Fix/Context
    assert mig.body.startswith("## Mistake\nThe half-applied schema needed a manual restore.")
    assert "## Fix\nNever restart the database while a migration runs." in mig.body
    assert "## Context\nEvery deploy that ships an alembic revision." in mig.body
    assert "Collected after the 2026 incidents." in mig.body and "deploy lessons" in mig.body
    assert mig.lead == "Deploy lessons › 1. Migrations" and mig.kind == "lesson"
    # "## Why" at the rule level is a label, not a rule: it stays inside the Proxy section
    assert "- Keep the maintenance page" in proxy.body and "- Reload the proxy" in proxy.body
    assert "All of these cost a failed release." in proxy.body
    renumbered = lessons.split(HEADINGS.replace("## 2. Proxy", "## 3) Proxy"), {}, "Deploy lessons")
    assert renumbered is not None and [p.anchor for p in renumbered] == ["migrations", "proxy"]


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


def test_procedure_steps_are_never_split() -> None:
    """Sol 54 #4: ordered steps of ONE procedure are one lesson, even as long bullets."""
    meta: dict[str, str] = {}
    intro = (
        "Restore the database in this order:\n"
        "- stop the api and the worker containers on the host\n"
        "- restore the latest dump into a fresh database volume\n"
    )
    assert lessons.split(intro, meta, "T") is None
    steps = (
        "Recovery steps:\n"
        "- stop every writer before touching the data\n"
        "- restore the dump into a new volume\n"
    )
    assert lessons.split(steps, meta, "T") is None
    led = (
        "When the disk fills up:\n"
        "- First stop the importer so that nothing else writes.\n"
        "- Then prune the old backups from the volume by hand.\n"
        "- Finally restart the importer and watch the free space.\n"
    )
    assert lessons.split(led, meta, "T") is None
    checklist = "- [ ] rotate the api token on the server\n- [ ] update the device list afterwards\n"
    assert lessons.split(checklist, meta, "T") is None
    turkish = "Geri yükleme adımları:\n- önce yazıcıları durdur ve bekle\n- sonra dökümü yeni birime yükle\n"
    assert lessons.split(turkish, meta, "T") is None
    # bullets under ONE rule heading are that rule's details
    one_rule = (
        "# Lessons\n\n## Never deploy on Fridays\n"
        "- the on-call rota is thin over the weekend\n- rollbacks wait until Monday morning\n"
    )
    assert lessons.split(one_rule, meta, "Lessons") is None


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
    assert lessons.name_label("**Stash is shared:** never use a bare stash") == "Stash is shared"
    assert lessons.name_label("**Stash** — never use a bare stash") == "Stash"
    assert lessons.name_label("**Never** run the suite against dev") is None  # emphasis, not a name
    assert lessons.name_label("**Mistake:** m **Fix:** f") is None  # a role label, not a name


BASE = (
    "Footguns:\n"
    "- Never restart the database while a migration runs, it corrupts the schema.\n"
    "- Pass -n to ssh inside heredocs or it reads the rest of the script.\n"
    "- **Base images:** pin them by digest because floating tags change underneath you.\n"
)


def test_wording_fix_keeps_the_key() -> None:
    """Sol 54 #4: a phrasing fix at the START of an unlabelled rule is the same key: one revision."""
    first = {s.anchor: s.body for s in lessons.split(BASE, {}, "T") or []}
    edited = BASE.replace("- Pass -n to ssh inside heredocs", "- Always pass -n to ssh in heredocs")
    second = {s.anchor: s.body for s in lessons.split(edited, {}, "T") or []}
    assert list(first) == list(second) == ["rule-1", "rule-2", "base-images"]
    assert [a for a in first if first[a] != second[a]] == ["rule-2"]


def _manifest(recs: list[ImportRecord]) -> list[dict]:
    return [
        {
            "logical_id": n,
            "version_id": 10 * n,
            "kind": r.kind_guess,
            "title": r.title,
            "body": r.body,
            "source": r.source(),
            "tags": list(r.tags),
        }
        for n, r in enumerate(recs, start=1)
    ]


#: a realistic shared description (~75 distinct words): copied into every split rule's context
SHARED = (
    "Operational footguns collected while running the fixture stack across three hosting moves, two "
    "database major upgrades and the migration from a single worker to a leased job queue; each item "
    "below cost at least one failed release or an evening of manual recovery work, and the owner asked "
    "that every coding agent reads them before touching deploy scripts, backups, container images, "
    "ssh automation or anything that runs against the production database of the stack"
)


def _recs(tmp_path: Path, text: str) -> list[ImportRecord]:
    d = tmp_path / "mem"
    d.mkdir(exist_ok=True)
    (d / "feedback_footguns.md").write_text(
        f"---\nname: Footguns\ndescription: {SHARED}\nmetadata:\n  type: feedback\n---\n" + text
    )
    return _parse("automemory", d)


def test_renamed_label_is_one_revision_via_remap(tmp_path: Path) -> None:
    """A renamed label changes the key: the W1.5 re-map (on the rule text, the long shared context
    cut) turns it into ONE revision of the old item, not a close + a new item."""
    old = _recs(tmp_path, BASE)
    renamed = _recs(tmp_path, BASE.replace("**Base images:**", "**Container base images:**"))
    manifest = _manifest(old)
    parsed = common.ParseResult(records=renamed, scopes=[""])
    plan = classify("p", "automemory", parsed, manifest, Meter())
    assert [(e.record.key.split("#")[1], e.action) for e in plan.entries] == [
        ("container-base-images", "new"),
        ("rule-1", "unchanged"),
        ("rule-2", "unchanged"),
    ]
    assert [m["logical_id"] for m in plan.missing] == [1]
    remap(plan, plan.missing)
    assert plan.remapped == [
        {
            "from": "automemory:feedback_footguns.md#base-images",
            "to": "automemory:feedback_footguns.md#container-base-images",
            "score": plan.remapped[0]["score"],
        }
    ]
    assert plan.closes == [] and plan.ambiguous == []
    assert [(e.action, e.logical_id) for e in plan.entries][0] == ("changed", 1)


def test_bare_item_is_replaced_by_its_split_rules() -> None:
    """Sol 54 #3: the old whole-file item (key K) of a file that now yields K#… sections only is
    ``replaced_by_split`` — closed in the same import, never 'missing', never re-mapped."""
    recs = _parse("automemory", FIXTURE / "automemory")
    footguns = [r for r in recs if r.file == "feedback_deploy_footguns.md"]
    old = {
        "logical_id": 5,
        "version_id": 50,
        "kind": "fact",
        "source": {"system": "automemory", "path": "feedback_deploy_footguns.md", "sha256": "0" * 64},
    }
    parsed = common.ParseResult(records=footguns, scopes=[""])
    plan = classify("p", "automemory", parsed, [old], Meter())
    assert plan.missing == [] and plan.replaced_items == [old]
    rep = report(plan, dry_run=True)
    assert rep["replaced_by_split"] == [
        {"key": "automemory:feedback_deploy_footguns.md", "by": sorted(r.key for r in footguns)}
    ]


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
    parts = lessons.split("- One rule has at least five words.\n- Another rule has five words too.\n", {}, "")
    assert parts is not None and all(isinstance(p, Section) and p.kind == "lesson" for p in parts)
    assert parts[0].lead == "One rule has at least five words."  # no document title: the rule alone


def test_two_renamed_labels_remap_one_to_one_despite_shared_context(tmp_path: Path) -> None:
    """Without cutting the shared ``## Context`` (30× the same words here), each old rule would be
    as similar to BOTH renamed rules: an ambiguous re-map leaves the old items open (duplicates)."""
    text = (
        "Footguns:\n"
        "- **Stash:** never use a bare git stash in a shared worktree, the stack is global.\n"
        "- **Ports:** never bind the test database to the default port on the build host.\n"
    )
    old = _recs(tmp_path, text)
    new = _recs(tmp_path, text.replace("**Stash:**", "**Git stash:**").replace("**Ports:**", "**DB ports:**"))
    plan = classify("p", "automemory", common.ParseResult(records=new, scopes=[""]), _manifest(old), Meter())
    remap(plan, plan.missing)
    assert plan.ambiguous == [] and plan.closes == []
    assert sorted((r["from"].split("#")[1], r["to"].split("#")[1]) for r in plan.remapped) == [
        ("ports", "db-ports"),
        ("stash", "git-stash"),
    ]
    from hlmemo.importers import plan as plan_mod

    whole = plan_mod.body_similarity(old[0].body, new[1].body)  # stash vs db-ports, context included
    assert whole >= plan_mod.REMAP_SAME_FILE  # the reason the rule text alone is compared
