"""``memory.ask`` fixture world (D-136): a small synthetic project with scope traps, written through
the production write path and embedded with the pinned model.

Projects: ``ask-main`` (home: the reader has write), ``ask-other`` (the reader reads it: another
project's items must never enter an ``ask-main`` answer), ``ask-shared`` (NO grant for the reader)
and ``ask-off`` (read grant, but ``policy.librarian = off``).

Every trap item carries a unique marker (``SECRETS``) that must never appear in a Memory Map, a
provider request or an answer:

* a ``device:<reader>`` item in ask-main (the reader can read it through memory.query, but device-
  scoped text never leaves the host);
* a ``class:work`` item in ask-main (the reader is a personal device: not visible);
* an item co-owned by ask-main and ask-shared (no grant on ask-shared);
* an item co-owned by ask-main and ask-off (policy off);
* an item of ask-other.

The legitimate content imitates ``hlm import``: decision rows of ``docs/decisions/DECISIONS.md``
(one item per row, ``source.path`` with the row anchor), a multi-chunk ``docs/status/STATUS.md`` and
a runbook. D-004 supersedes a value of D-001 (1,6 s -> 1.2 s) for the numeral/currency checks.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import psycopg

from hlmemo.auth.context import AuthContext, Role
from hlmemo.core.embedder import Embedder
from hlmemo.core.write_service import default_deps, write
from hlmemo.worker.main import drain

MAIN, OTHER, SHARED, OFF = "ask-main", "ask-other", "ask-shared", "ask-off"
CLIENT = "pytest-ask/0"
SECRETS = {
    "device": "DEVICE-ONLY-SECRET-zq9",
    "work": "WORK-CLASS-SECRET-k2p",
    "shared": "COOWNED-SECRET-m7x",
    "off": "POLICY-OFF-SECRET-r4t",
    "other": "OTHER-PROJECT-SECRET-w1c",
}

DECISIONS = [
    (
        "D-001",
        "ACCEPTED: Use Postgres 17 with pgvector",
        "D-001 | 2026-09-01 | ACCEPTED | **Use Postgres 17 with pgvector** as the only store. The retrieval "
        "p95 target is 1,6 s on the VPS. SQLite was rejected because it has no concurrent writers.",
    ),
    (
        "D-002",
        "ACCEPTED: HLM_RESEARCH_ENABLED flag",
        "D-002 | 2026-09-10 | ACCEPTED | **HLM_RESEARCH_ENABLED** turns the research librarian on. It "
        "defaults to true on the research branch; the release decides whether production enables it.",
    ),
    (
        "D-003",
        "ACCEPTED: Memory Map budget",
        "D-003 | 2026-09-12 | ACCEPTED | The Memory Map budget is about 6k tokens. Summaries take at most "
        "40 % of it and a large document is truncated by spreading, never by cutting its head.",
    ),
    (
        "D-004",
        "ACCEPTED: new retrieval latency target",
        "D-004 | 2026-09-20 | ACCEPTED | The retrieval p95 target is now 1.2 s (it was 1,6 s in D-001), "
        "measured by the G4 latency gate on the production VPS. The owner decided it after the R3 release.",
    ),
]

STATUS_BODY = (
    "# STATUS\n\n## Where we are\n\nRelease R3 is live in production. "
    + "The research librarian is being built on branch wf-memory-ask. " * 30
    + "\n\n## Next steps\n\nMeasure the Production-Ready gate on the dev replica. "
    + "Then run the VM rehearsal and take a fresh snapshot before the release. " * 30
    + "\n\n## Open issues\n\nThe trigram fix for G-L3 is parked until the research loop needs it. "
    + "Spend reconciliation is still open in the backlog. " * 30
)

RUNBOOK_BODY = (
    "# RUNBOOK\n\n## Backup\n\nRun `bash deploy/backup/backup.sh` nightly; it keeps 14 daily dumps.\n\n"
    "## Restore\n\nRestore with `bash deploy/backup/restore.sh <dump> --yes` after stopping the api.\n"
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _item(title: str, body: str, *, path: str | None = None, **kw: Any) -> dict[str, Any]:
    it: dict[str, Any] = {"kind": kw.pop("kind", "doc_chunk"), "title": title, "body": body}
    if path is not None:
        it["source"] = {"system": "markdown", "path": path, "sha256": _sha(body)}
    it.update(kw)
    return it


@dataclass(slots=True)
class AskWorld:
    ctx_loader: AuthContext
    ctx_reader: AuthContext
    projects: dict[str, int]
    reader_id: int
    versions: dict[str, int] = field(default_factory=dict)  # name -> version id
    secret_versions: dict[str, int] = field(default_factory=dict)


async def _identities(conn: psycopg.AsyncConnection) -> tuple[dict[str, int], dict[str, int]]:
    cur = await conn.execute(
        "INSERT INTO projects (slug, name, policy) VALUES (%s, 'Ask main', '{}'), (%s, 'Ask other', '{}'),"
        " (%s, 'Ask shared', '{}'), (%s, 'Ask off', '{\"librarian\": \"off\"}') RETURNING slug, project_id",
        (MAIN, OTHER, SHARED, OFF),
    )
    projects = dict(await cur.fetchall())
    cur = await conn.execute(
        """
        INSERT INTO devices (name, class, fingerprint, token_sha256, status, approved_at,
                             approved_by_device_id)
        VALUES ('ask-loader', 'work', 'fp-ask-loader', 'h-ask-loader', 'trusted', now(), 1),
               ('ask-reader', 'personal', 'fp-ask-reader', 'h-ask-reader', 'trusted', now(), 1)
        RETURNING name, device_id
        """
    )
    devices = dict(await cur.fetchall())
    grants = [(devices["ask-loader"], pid, "write") for pid in projects.values()]
    grants += [
        (devices["ask-reader"], projects[MAIN], "write"),
        (devices["ask-reader"], projects[OTHER], "read"),
        (devices["ask-reader"], projects[OFF], "read"),
    ]
    for did, pid, role in grants:
        await conn.execute(
            "INSERT INTO device_project_grants (device_id, project_id, role, granted_by_device_id)"
            " VALUES (%s, %s, %s, 1)",
            (did, pid, role),
        )
    await conn.commit()
    return projects, devices


def ctx_of(device_id: int, grants: dict[int, Role], device_class: str = "personal") -> AuthContext:
    return AuthContext(
        device_id=device_id,
        device_class=device_class,
        is_admin=False,
        token_generation=1,
        grants=grants,
        client=CLIENT,
    )


async def seed_world(connect: Any, embedder: Embedder) -> AskWorld:
    """Truncate, create the projects/devices, write every item and embed them (a few seconds)."""
    from tests.conftest import TRUNCATE_SQL

    async with await connect() as conn:
        await conn.execute(TRUNCATE_SQL)
        await conn.commit()
        projects, devices = await _identities(conn)
        loader = ctx_of(devices["ask-loader"], {pid: Role.WRITE for pid in projects.values()}, "work")
        reader = ctx_of(
            devices["ask-reader"],
            {projects[MAIN]: Role.WRITE, projects[OTHER]: Role.READ, projects[OFF]: Role.READ},
        )
        world = AskWorld(loader, reader, projects, devices["ask-reader"])
        deps = default_deps()

        async def put(project: str, name: str, item: dict[str, Any], secret: str | None = None) -> None:
            ack = await write(
                conn,
                loader,
                {"project": project, "request_id": str(uuid.uuid4()), "client": CLIENT, "items": [item]},
                deps=deps,
            )
            await conn.commit()
            vid = ack.versions[0].version_id
            world.versions[name] = vid
            if secret is not None:
                world.secret_versions[secret] = vid

        path = "docs/decisions/DECISIONS.md"
        for did, lead, body in DECISIONS:
            await put(MAIN, did, _item(f"{did} · {lead} · {path}", body, path=f"{path}#{did}", kind="fact"))
        await put(
            MAIN, "status", _item("STATUS · docs/status/STATUS.md", STATUS_BODY, path="docs/status/STATUS.md")
        )
        await put(
            MAIN, "runbook", _item("RUNBOOK · deploy/RUNBOOK.md", RUNBOOK_BODY, path="deploy/RUNBOOK.md")
        )
        dev_scope = f"device:{world.reader_id}"
        await put(
            MAIN,
            "device",
            _item(
                "Reader private note",
                f"{SECRETS['device']} lives in the reader's keychain.",
                device_scope=dev_scope,
            ),
            "device",
        )
        await put(
            MAIN,
            "work",
            _item(
                "Work laptop note", f"{SECRETS['work']} is the work VPN profile.", device_scope="class:work"
            ),
            "work",
        )
        await put(
            MAIN,
            "shared",
            _item(
                "Shared note",
                f"{SECRETS['shared']} belongs to the shared project.",
                project_ids=[MAIN, SHARED],
            ),
            "shared",
        )
        await put(
            MAIN,
            "off",
            _item(
                "Policy-off note", f"{SECRETS['off']} must never reach a provider.", project_ids=[MAIN, OFF]
            ),
            "off",
        )
        await put(
            OTHER, "other", _item("Other project note", f"{SECRETS['other']} is in another project."), "other"
        )
    await drain(connect, embedder)
    return world


# --------------------------------------------------------------------------- a JOB-routing fake model
def prose_text(obj: dict[str, Any]) -> str:
    """D-178: a JOB prose output in the JOB prose_text layout."""
    return (
        f"STATUS: {obj['status']}\nCONFIDENCE: {obj.get('confidence', 'low')}\n"
        f"SOURCES: {', '.join(obj.get('sources') or [])}\nRELATED: {', '.join(obj.get('related') or [])}\n"
        f"ANSWER:\n{obj.get('answer', '')}"
    )


def request_job(body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """``(job, INPUT payload)`` of a provider request body."""
    user = body["messages"][1]["content"]
    job = user.split("\n", 1)[0].removeprefix("JOB: ").strip()
    m = re.search(r"INPUT: (\{.*?\})(\n\n|$)", user, re.S)
    payload = json.loads(m.group(1)) if m else {}
    return job, payload


def sentence_with(text: str, needle: str) -> str | None:
    """The sentence of ``text`` that contains ``needle`` (verbatim), or None."""
    for s in re.split(r"(?<=[.!?])\s+", text):
        if needle in s:
            return s.strip()
    return None


@dataclass
class FakeResearcher:
    """Answers JOB requests like a well-behaved model, driven by ``facts`` (needles to quote)."""

    facts: list[str]
    queries: list[str] = field(default_factory=lambda: ["retrieval latency target", "p95 VPS"])
    sections: list[str] = field(default_factory=list)
    abstain: bool = False
    extra_primary: list[str] = field(default_factory=list)  # handles the model tries to cite anyway
    check_adds: list[str] = field(default_factory=list)  # facts only the completeness pass adds
    answer_prefix: str = ""  # prepended to the claims of the answer JOB only (an attribution to repair)
    #: D-156 cite mode: sentences the JOB write adds after the facts' own ({"text", "cite"?}; no cite
    #: = the first fact's excerpt)
    write_extra: list[dict[str, Any]] = field(default_factory=list)
    #: D-159 JOB select: None = the first excerpt holding each fact (in the facts' order); else
    #: exactly these ids
    select_ids: list[str] | None = None
    #: D-162 prose mode: sentences the JOB prose appends to the facts' own
    prose_extra: list[str] = field(default_factory=list)
    #: D-165 JOB attribute: the ids per sentence number; None = the excerpts whose text holds the
    #: sentence (without its final period)
    attribute_ids: dict[int, list[str]] | None = None
    #: D-170 JOB expand: the sentences it adds
    expand_add: list[str] = field(default_factory=list)
    jobs: list[str] = field(default_factory=list)

    def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        job, inp = request_job(body)
        self.jobs.append(job)
        if job in ("plan", "refine"):
            return {"queries": self.queries, "sections": self.sections}
        excerpts = inp.get("excerpts") or []
        if job == "select":
            return self._select(excerpts)
        if job == "write":
            return self._write(excerpts)
        if job == "prose":
            return self._prose(excerpts)
        if job == "prose_text":  # D-178: the same answer in the plain-text layout
            return prose_text(self._prose(excerpts))
        if job == "expand_text":
            return "ADD:\n" + "\n".join(self.expand_add)
        if job == "attribute":
            return self._attribute(inp.get("sentences") or [], excerpts)
        if job == "expand":
            return {"add": list(self.expand_add)}
        facts = self.facts + (self.check_adds if job == "check" else [])
        claims = []
        for needle in facts:
            for ex in excerpts:
                s = sentence_with(ex["text"], needle)
                if s:
                    text = (self.answer_prefix + s) if job == "answer" else s
                    claims.append({"text": text, "support": [{"id": ex["id"], "quote": s}, *self._extra()]})
                    break
        if self.abstain or not claims:
            return {
                "status": "insufficient_evidence",
                "answer": "",
                "claims": [],
                "related": [e["id"] for e in excerpts[:2]],
                "confidence": "low",
            }
        out = {
            "status": "answered",
            "answer": " ".join(c["text"] for c in claims),
            "claims": claims,
            "related": [e["id"] for e in excerpts if e["id"] != claims[0]["support"][0]["id"]][:3]
            + self.extra_primary,
            "confidence": "high",
        }
        if job == "check":
            out = {
                "sub_asks": [{"ask": "the value", "covered": True}],
                "missing": list(self.check_adds),
                **out,
            }
        return out

    def _select(self, excerpts: list[dict[str, Any]]) -> dict[str, Any]:
        """D-159 JOB select: the excerpts that state the facts, most important (first fact) first."""
        if self.select_ids is not None:
            return {"ids": list(self.select_ids)}
        ids: list[str] = []
        for needle in self.facts:
            hit = next((ex["id"] for ex in excerpts if sentence_with(ex["text"], needle)), None)
            if hit is not None and hit not in ids:
                ids.append(hit)
        return {"ids": ids}

    def _write(self, excerpts: list[dict[str, Any]]) -> dict[str, Any]:
        """D-156 JOB write: each fact's sentence, citing the excerpt it was copied from."""
        sentences = []
        for needle in self.facts:
            for ex in excerpts:
                s = sentence_with(ex["text"], needle)
                if s:
                    sentences.append({"text": s, "cite": [ex["id"], *self.extra_primary]})
                    break
        if self.abstain or not sentences:
            return {
                "status": "insufficient_evidence",
                "sentences": [],
                "related": [e["id"] for e in excerpts[:2]],
                "confidence": "low",
            }
        first = sentences[0]["cite"][0]
        sentences += [{"text": x["text"], "cite": x.get("cite", [first])} for x in self.write_extra]
        return {
            "status": "answered",
            "sentences": sentences,
            "related": [e["id"] for e in excerpts if e["id"] != first][:3] + self.extra_primary,
            "confidence": "high",
        }

    def _prose(self, excerpts: list[dict[str, Any]]) -> dict[str, Any]:
        """D-162 JOB prose: the facts' sentences as one answer, with the excerpts they came from."""
        sentences: list[str] = []
        sources: list[str] = []
        for needle in self.facts:
            for ex in excerpts:
                s = sentence_with(ex["text"], needle)
                if s:
                    sentences.append(s)
                    if ex["id"] not in sources:
                        sources.append(ex["id"])
                    break
        if self.abstain or not sentences:
            return {
                "status": "insufficient_evidence",
                "answer": "",
                "sources": [],
                "related": [e["id"] for e in excerpts[:2]],
                "confidence": "low",
            }
        return {
            "status": "answered",
            "answer": " ".join([*sentences, *self.prose_extra]),
            "sources": sources + self.extra_primary,
            "related": [e["id"] for e in excerpts if e["id"] not in sources][:3] + self.extra_primary,
            "confidence": "high",
        }

    def _attribute(self, sentences: list[dict[str, Any]], excerpts: list[dict[str, Any]]) -> dict[str, Any]:
        """D-165 JOB attribute: per numbered sentence, the excerpts that state it."""
        cites = []
        for s in sentences:
            if self.attribute_ids is not None:
                ids = self.attribute_ids.get(s["n"], [])
            else:
                ids = [e["id"] for e in excerpts if s["text"].rstrip(".") in e["text"]][:3]
            cites.append({"s": s["n"], "ids": ids})
        return {"cites": cites}

    def _extra(self) -> list[dict[str, str]]:
        """Support the model tries to add from handles it was never shown (must be discarded)."""
        return [
            {"id": h, "quote": "a quote from an excerpt that was never shown"} for h in self.extra_primary
        ]


__all__ = [
    "CLIENT",
    "MAIN",
    "OFF",
    "OTHER",
    "SECRETS",
    "SHARED",
    "AskWorld",
    "FakeResearcher",
    "ctx_of",
    "prose_text",
    "request_job",
    "seed_world",
    "sentence_with",
]
