You are reviewing a release candidate of HLMemo (a self-hosted MCP memory server): five write-path validations PV-1..PV-5. The working directory is the candidate's git worktree; read any file you need (read-only). Branch `pv-validations`, commit 77d3404, against origin/main.

Read first: the threat model and severity rubric below (written before this review), then protocol §5.2 (docs/protocol/HLMEMO-PROTOCOL.md) for the spec, then the diff.

Reply in <= 40 lines: verdict (GO / GO-WITH-FIXES / NO-GO), then findings ranked HIGH/MEDIUM/LOW per the rubric, each with file:line and a concrete input that triggers it (for a HIGH, the shape of a reproducing test). Answer explicitly, each in one line:
(a) Can a refused write leave any trace (item, event, request/idempotency row, log line) holding the refused text?
(b) Can a secret value appear in the error message, details, logs or an exception string?
(c) Is any of the five checks reachable from replay (db/replay.py), or can historic events stop replaying byte-identically?
(d) Which supported field bypasses which check with ordinary input?
(e) Is the closed error-code list and its HTTP mapping unchanged?
Measured before the review: the checks as pure functions over 1,629 real legitimate items of two projects (1,598 with `source`, 207 lessons with status tags, 93 imported items with supersedes links) refuse 0; planted violations fire.

# PV-1..PV-5 write-path validations: threat model and severity rubric (before review)

Written 2026-10-07, before the implementation is reviewed (CLAUDE.md: one-way doors get the threat model and the
rubric first). Scope: the five checks of protocol §5.2 on `memory.write`, `memory.call_the_day`,
`memory.register_lesson` and the MCP handlers (`server/tools/handlers.py`, `server/tools/risk.py`).

## What is protected
- **Integrity of an append-only store.** A refused write must leave no trace: no item, no event, no request-ledger row
  that holds the refused payload.
- **Secrecy.** A secret value must not be stored and must not be echoed (error message, `details`, server log, event,
  idempotency record).
- **Availability of legitimate writes.** Writers must not be blocked by a check that legitimate text trips. The cost
  of a false reject is a writer that routes around the rule, or a lost lesson.
- **Replay determinism.** Historic events must replay byte-identically. `db/replay.py` must not run the new checks.
- **The error contract.** Only codes from the closed list (`auth/errors.py`), with `details.reason`. The HTTP mapping
  and the spec stay unchanged.

## Failure modes to look for
1. **False reject** of a legitimate write class: placeholders (`sk-...`, `<token>`), JWT-shaped example strings in
   docs, code identifiers, long hex digests (sha256 is not a secret), Turkish or other non-ASCII text, NBSP handling in
   PV-3, tag case variants in PV-5. This is measured on real items before review (MEASURE.md, private).
2. **Bypass through an unchecked field:** tags, `card_update.body`, `notes`, `decisions[]`, register_lesson
   `context`, `updates[].replacement`, link targets, `title`; the `$i` and integer targets in PV-2; `project_ids` in
   PV-4.
3. **Persistence before validation:** the request ledger, the idempotency key or an event written before the check
   runs, so a refused secret is still stored; or a replayed `request_id` that skips the check.
4. **Echo:** the value inside `message`, `details`, an exception string or an INFO/ERROR log line.
5. **Replay divergence:** any check reachable from replay, or a migration of historic data.
6. **Exemption abuse:** PV-2 exempts items with `source`. A writer could add a fake `source` to raw-link anyway. Is
   that acceptable, given that imports must stay exempt?
7. **Contract drift:** a new code, a changed HTTP status, `retryable` set wrongly, or a different envelope shape.

## Severity rubric
- **HIGH:** a measured false-reject class of legitimate writes; a secret value persisted or echoed anywhere; replay
  divergence; a check bypassed through a supported field with ordinary input. A HIGH needs a reproducing test.
- **MEDIUM:** a bypass that needs unusual input (zero-width splitting, encodings); an inconsistent error shape; a
  missing test for a listed case.
- **LOW:** documentation, naming or test-structure gaps.

## Review plan
Codex `gpt-6-astra` low and `gpt-5.6-sol` xhigh in parallel on the branch diff plus this file and MEASURE's counts
(no private content). At most 2 rounds. Every HIGH gets a reproducing test. The owner decides any residual risk,
for example failure mode 6, then deploys.

## Diff (src and tests)
diff --git a/src/hlmemo/core/secret_guard.py b/src/hlmemo/core/secret_guard.py
new file mode 100644
index 0000000..3bb578e
--- /dev/null
+++ b/src/hlmemo/core/secret_guard.py
@@ -0,0 +1,38 @@
+"""PV-1 ``secret_pattern`` (protocol R7): the strong secret shapes no memory text needs.
+
+History is append-only, so a secret that reaches memory can only be erased by an owner-run database
+procedure. The write path therefore refuses text that matches one of these shapes. They are the
+strong shapes of the importer's refusal set (``importers/common.py`` ``SECRET_PATTERNS``) and of the
+capture scrub, with ``sk-api-key`` narrowed to the known key prefixes so that prose such as
+``sk-learn`` or a placeholder ``sk-...`` is never refused. The importer's broader rules
+(``assigned-secret``, ``dsn-with-password``, env-style assignments, credential pairs) stay out: they
+reject legitimate writes too often (D-219).
+
+The matched value is never returned: callers name the rule, the field and the item index only.
+"""
+
+from __future__ import annotations
+
+import re
+
+STRONG_SECRET_RULES: dict[str, re.Pattern[str]] = {
+    "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
+    "aws-access-key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
+    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b"),
+    "slack-token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
+    "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
+    "hlm-token": re.compile(r"\bhlm_[A-Za-z0-9_-]{43}\b"),
+    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
+    "sk-api-key": re.compile(r"\bsk-(?:or-v1-|ant-|proj-)[A-Za-z0-9_-]{24,}"),
+}
+
+
+def strong_secret_rule(text: str) -> str | None:
+    """The id of the first strong secret rule that ``text`` matches, else None."""
+    for rule, pattern in STRONG_SECRET_RULES.items():
+        if pattern.search(text):
+            return rule
+    return None
+
+
+__all__ = ["STRONG_SECRET_RULES", "strong_secret_rule"]
diff --git a/src/hlmemo/core/write_models.py b/src/hlmemo/core/write_models.py
index f601e6d..3396cef 100644
--- a/src/hlmemo/core/write_models.py
+++ b/src/hlmemo/core/write_models.py
@@ -9,6 +9,7 @@ from __future__ import annotations
 
 import re
 import uuid
+from collections.abc import Iterable
 from datetime import datetime
 from typing import Any, Literal
 
@@ -21,6 +22,7 @@ from pydantic import (
     model_serializer,
     model_validator,
 )
+from pydantic_core import PydanticCustomError
 
 from hlmemo.core.errors import ToolError
 
@@ -38,6 +40,35 @@ class _Strict(BaseModel):
     model_config = ConfigDict(extra="forbid", strict=False)
 
 
+#: Pydantic error types that ``parse_request`` reports as ``details.reason`` (protocol §5.2)
+REASON_TYPES = frozenset({"blank", "lesson_status_conflict"})
+LESSON_KINDS = frozenset({"lesson", "experience"})
+_CONCLUDED = frozenset({"resolved", "historical"})
+
+
+def _reject_blank(v: Any) -> Any:
+    """PV-3 ``blank`` (R6): text made only of whitespace (NBSP included) carries no claim, so a
+    title, body, note, decision or card that is blank is refused. ``min_length`` alone lets it pass."""
+    if isinstance(v, str) and not v.strip():
+        raise PydanticCustomError("blank", "must not be blank")
+    return v
+
+
+def lesson_status_conflict(kind: str, tags: Iterable[str]) -> bool:
+    """PV-5 (R15, D-234): a lesson is ``active`` or concluded (``resolved``/``historical``), never
+    both. ``resolved`` + ``historical`` stays valid (the D-234 import form); other kinds are free."""
+    t = set(tags)
+    return kind in LESSON_KINDS and "active" in t and bool(t & _CONCLUDED)
+
+
+def _refuse_status_conflict(kind: str, tags: Iterable[str]) -> None:
+    if lesson_status_conflict(kind, tags):
+        raise PydanticCustomError(
+            "lesson_status_conflict",
+            "a lesson cannot be active and resolved or historical at once: keep one status tag (D-234)",
+        )
+
+
 def canonical_device_scope(v: str) -> str:
     """F08: ``device:<id>`` must be spelled canonically (no leading zeros). Readers match the
     stored string against ``'device:' || device_id``, so ``device:02`` would be acked on write and
@@ -171,6 +202,11 @@ class Item(_Strict):
 
     _device_scope = field_validator("device_scope")(canonical_device_scope)
 
+    @field_validator("title", "body")
+    @classmethod
+    def _text_not_blank(cls, v: str) -> str:
+        return _reject_blank(v)
+
     @field_validator("describes")
     @classmethod
     def _describes(cls, v: list[str] | None) -> list[str] | None:
@@ -194,6 +230,7 @@ class Item(_Strict):
                 raise ValueError("a project card cannot be closed")
             if self.logical_id is None or self.valid_from is None:
                 raise ValueError("close needs logical_id, expected_version_id and valid_from")
+        _refuse_status_conflict(self.kind, self.tags)  # PV-5
         return self
 
     @field_validator("project_ids", mode="before")
@@ -227,11 +264,28 @@ class LessonSpec(_Strict):
 
     _device_scope = field_validator("device_scope")(canonical_device_scope)
 
+    @field_validator("title", "body")
+    @classmethod
+    def _text_not_blank(cls, v: str) -> str:
+        return _reject_blank(v)
+
+    @model_validator(mode="after")
+    def _status(self) -> LessonSpec:
+        # PV-5 here as well: ``_close_items`` turns each lesson into an ``Item``, and a conflict found
+        # only there would surface as an internal error instead of ``E_INVALID_ARG``
+        _refuse_status_conflict("lesson", self.tags)
+        return self
+
 
 class CardUpdate(_Strict):
     body: str = Field(min_length=1, max_length=64000)
     expected_version_id: int | None = Field(default=None, ge=1)  # None: no card exists yet (first close)
 
+    @field_validator("body")
+    @classmethod
+    def _text_not_blank(cls, v: str) -> str:
+        return _reject_blank(v)
+
 
 class ExpectedVersion(_Strict):
     logical_id: int = Field(ge=1)
@@ -256,6 +310,16 @@ class CloseRequest(_Strict):
     def _uuid(cls, v: str) -> str:
         return str(uuid.UUID(v))
 
+    @field_validator("notes")
+    @classmethod
+    def _notes_not_blank(cls, v: str) -> str:
+        return _reject_blank(v)
+
+    @field_validator("decisions")
+    @classmethod
+    def _decisions_not_blank(cls, v: list[str]) -> list[str]:
+        return [_reject_blank(d) for d in v]
+
     @model_validator(mode="after")
     def _derived_items_fit(self) -> CloseRequest:
         """F12: the close is expanded into ordinary write items (session note, lessons, card);
@@ -376,9 +440,23 @@ def _format_validation_error(exc: ValidationError) -> str:
     return "; ".join(parts)
 
 
+def _reason_details(exc: ValidationError) -> dict[str, Any]:
+    """Protocol §5.2: a PV-3/PV-5 refusal names its ``reason`` (and the ``index`` of the item, when
+    the error sits under ``items[i]``), like ``missing_home``/``duplicate_project`` do."""
+    for err in exc.errors(include_input=False, include_url=False):
+        if err.get("type") in REASON_TYPES:
+            out: dict[str, Any] = {"reason": err["type"]}
+            loc = err.get("loc", ())
+            if len(loc) >= 2 and loc[0] == "items" and isinstance(loc[1], int):
+                out["index"] = loc[1]
+            return out
+    return {}
+
+
 def parse_request[T: BaseModel](model: type[T], raw: T | dict[str, Any]) -> T:
     """Validate ``raw`` as ``model``; a Pydantic error becomes ``E_INVALID_ARG`` with a bounded
-    ``details.errors`` list (no input echo) and ``details.error_count`` (the full count)."""
+    ``details.errors`` list (no input echo), ``details.error_count`` (the full count) and, for a
+    PV-3/PV-5 refusal, ``details.reason`` (+ ``index``)."""
     if isinstance(raw, model):
         return raw
     try:
@@ -386,7 +464,11 @@ def parse_request[T: BaseModel](model: type[T], raw: T | dict[str, Any]) -> T:
     except ValidationError as exc:
         errors, total = bounded_validation_errors(exc)
         raise ToolError(
-            "E_INVALID_ARG", _format_validation_error(exc), errors=errors, error_count=total
+            "E_INVALID_ARG",
+            _format_validation_error(exc),
+            errors=errors,
+            error_count=total,
+            **_reason_details(exc),
         ) from exc
 
 
@@ -409,6 +491,7 @@ __all__ = [
     "WriteRequest",
     "WriteResult",
     "bounded_validation_errors",
+    "lesson_status_conflict",
     "parse_request",
     "session_note_body",
 ]
diff --git a/src/hlmemo/core/write_service.py b/src/hlmemo/core/write_service.py
index ca7d96b..cde0e71 100644
--- a/src/hlmemo/core/write_service.py
+++ b/src/hlmemo/core/write_service.py
@@ -62,6 +62,7 @@ from hlmemo.core.chunker import CHUNK_OVERLAP, CHUNK_TOK, Chunker
 from hlmemo.core.embedder import default_model_dir, sha256_file
 from hlmemo.core.errors import ToolError, invalid_arg
 from hlmemo.core.normalize import normalize
+from hlmemo.core.secret_guard import strong_secret_rule
 from hlmemo.core.temporal import (
     Interval,
     fmt_ts,
@@ -359,6 +360,7 @@ async def write(
     import and gets 6)."""
     request_payload = verbatim_args(req, raw)
     request = parse_request(WriteRequest, req)
+    _check_secrets(request.items)  # PV-1, before any transaction
     if librarian_priority is None and all(it.source is not None for it in request.items):
         librarian_priority = IMPORT_LIBRARIAN_PRIORITY
     deps = deps or default_deps()
@@ -409,6 +411,7 @@ async def call_the_day(
     except BudgetError as exc:
         raise ToolError(exc.code, str(exc), **exc.details) from exc
     items = _close_items(request)
+    _check_secrets(items)  # PV-1 over the derived note, lessons and card
     async with conn.transaction():
         result = await _execute(
             conn,
@@ -469,6 +472,36 @@ def _close_items(request: CloseRequest) -> list[Item]:
     return items
 
 
+# --------------------------------------------------------------------------- write-path validations
+def _check_secrets(items: list[Item]) -> None:
+    """PV-1 ``secret_pattern`` (R7): a title, body or tag matching a strong secret shape is
+    refused before the transaction, because append-only history can only be erased by an owner-run
+    database procedure. The message names the rule, the field and the index, never the value."""
+    for i, it in enumerate(items):
+        for name, text in (("title", it.title), ("body", it.body), *(("tags", t) for t in it.tags)):
+            rule = strong_secret_rule(text)
+            if rule is not None:
+                raise invalid_arg(
+                    f"items[{i}].{name} matches secret rule {rule}: remove it or name where it is stored",
+                    index=i,
+                    field=name,
+                    reason="secret_pattern",
+                    rule=rule,
+                )
+
+
+def _check_supersedes_links(i: int, it: Item) -> None:
+    """PV-2 ``supersedes_needs_updates`` (R13): a writer replaces a memory with ``updates`` (a
+    verbatim ``old_span``, the D-118 guards), not with a raw ``supersedes`` link, which reads as
+    whole scope and hides its target. Imports carry ``source`` and stay exempt (export round trips)."""
+    if it.source is None and any(ln.rel == "supersedes" for ln in it.links):
+        raise invalid_arg(
+            f"items[{i}].links: supersede with items[].updates (a verbatim old_span), not a link",
+            index=i,
+            reason="supersedes_needs_updates",
+        )
+
+
 # --------------------------------------------------------------------------- authorization
 def _forbidden(slug: str) -> ToolError:
     return ToolError("E_FORBIDDEN_PROJECT", f"no write grant on project {slug!r}", project=slug)
@@ -543,6 +576,7 @@ def _check_shapes(batch: _Batch, plans: list[_Plan]) -> None:
                     f"items[{i}]: logical_id {p.logical_id} appears twice in the batch", index=i
                 )
             seen_logical.add(p.logical_id)
+        _check_supersedes_links(i, it)  # PV-2
         seen_links: set[tuple[str, str]] = set()
         for ln in it.links:
             if isinstance(ln.target, str):
diff --git a/src/hlmemo/librarian/reserved.py b/src/hlmemo/librarian/reserved.py
index 87d5388..1a1a756 100644
--- a/src/hlmemo/librarian/reserved.py
+++ b/src/hlmemo/librarian/reserved.py
@@ -7,15 +7,43 @@ test suite needs it because its per-test truncation removes every row except dev
 from __future__ import annotations
 
 from dataclasses import dataclass
+from typing import Any
 
 from psycopg import AsyncConnection
 
+from hlmemo.core.errors import ToolError
+
 LIBRARIAN_DEVICE = "librarian"
 LIBRARIAN_TOKEN_PLACEHOLDER = "reserved:librarian"
 MEMORY_PROJECT = "hlm-librarian"
 GLOBAL_PROJECT = "hlm-global"
 RESERVED_PROJECTS = (MEMORY_PROJECT, GLOBAL_PROJECT)
 
+
+def refuse_reserved_project(args: Any) -> None:
+    """PV-4 ``reserved_project`` (R3, R16), on the MCP write tools only: no client writes into the
+    librarian's working memory, whatever its grants (grants belong to the device, not the chat). The
+    librarian writes there itself through the write service, which this check does not touch."""
+    if not isinstance(args, dict):
+        return
+    hit = args.get("project") == MEMORY_PROJECT
+    items = args.get("items")
+    if not hit and isinstance(items, list):
+        hit = any(
+            isinstance(it, dict)
+            and isinstance(it.get("project_ids"), list)
+            and MEMORY_PROJECT in it["project_ids"]
+            for it in items
+        )
+    if hit:
+        raise ToolError(
+            "E_FORBIDDEN_PROJECT",
+            f"project {MEMORY_PROJECT!r} is reserved for the librarian: write into your own project",
+            project=MEMORY_PROJECT,
+            reason="reserved_project",
+        )
+
+
 _ENSURE = r"""
 INSERT INTO devices (user_id, name, class, fingerprint, os, status, is_admin, is_system,
                      token_sha256, notes, approved_at, approved_by_device_id)
diff --git a/src/hlmemo/server/tools/handlers.py b/src/hlmemo/server/tools/handlers.py
index 40761de..d8d9da5 100644
--- a/src/hlmemo/server/tools/handlers.py
+++ b/src/hlmemo/server/tools/handlers.py
@@ -25,6 +25,7 @@ from hlmemo.core import write_service
 from hlmemo.core.budget import BudgetError, validate_budget
 from hlmemo.core.errors import ToolError
 from hlmemo.core.read_service import ReadDeps
+from hlmemo.librarian.reserved import refuse_reserved_project
 
 Handler = Callable[[AsyncConnection, AuthContext, dict[str, Any]], Awaitable[dict[str, Any]]]
 
@@ -64,12 +65,14 @@ def as_result_dict(result: Any) -> dict[str, Any]:
 
 
 async def memory_write(conn: AsyncConnection, ctx: AuthContext, args: dict[str, Any]) -> dict[str, Any]:
+    refuse_reserved_project(args)  # PV-4
     return as_result_dict(await write_service.write(conn, ctx, args, raw=args))
 
 
 async def memory_call_the_day(
     conn: AsyncConnection, ctx: AuthContext, args: dict[str, Any]
 ) -> dict[str, Any]:
+    refuse_reserved_project(args)  # PV-4
     return as_result_dict(await write_service.call_the_day(conn, ctx, args, raw=args))
 
 
diff --git a/src/hlmemo/server/tools/risk.py b/src/hlmemo/server/tools/risk.py
index 4df2a57..85fa1c3 100644
--- a/src/hlmemo/server/tools/risk.py
+++ b/src/hlmemo/server/tools/risk.py
@@ -17,6 +17,7 @@ from psycopg import AsyncConnection
 from hlmemo.auth.context import AuthContext
 from hlmemo.core import lesson_service, risk_service
 from hlmemo.core.errors import ToolError
+from hlmemo.librarian.reserved import refuse_reserved_project
 from hlmemo.server.tools.schemas import DEFS
 
 RISK_CHECK = "memory.risk_check"
@@ -105,6 +106,7 @@ async def memory_risk_check(
 async def memory_register_lesson(
     conn: AsyncConnection, ctx: AuthContext, args: dict[str, Any]
 ) -> dict[str, Any]:
+    refuse_reserved_project(args)  # PV-4
     return await lesson_service.register_lesson(conn, ctx, args)
 
 
diff --git a/tests/integration/test_librarian_attempt_gate.py b/tests/integration/test_librarian_attempt_gate.py
index ebc2e6c..92012ae 100644
--- a/tests/integration/test_librarian_attempt_gate.py
+++ b/tests/integration/test_librarian_attempt_gate.py
@@ -123,19 +123,24 @@ async def test_revocation_before_the_next_attempt_stops_it(
         assert await outcomes(conn) == ["authority_lost"]
 
 
-async def test_rules_are_redacted_and_rule_shaped(db_dsn, connect, world, deps) -> None:  # noqa: ANN001
+async def test_rules_are_redacted_and_rule_shaped(db_dsn, connect, world, deps, monkeypatch) -> None:  # noqa: ANN001
+    from hlmemo.core import write_service
+
     body_text = "PASTED ITEM BODY " + ("lorem ipsum dolor sit amet " * 40)  # > MAX_RULE_CHARS
     async with await connect() as conn:
         ids = await reserved_ids(conn)
         lib = memory_ctx(ids.librarian_device_id, ids.memory_project_id)
         rule = {"kind": "fact", "tags": ["librarian-rule"], "importance": 9}
-        # written by the librarian but NOT via write_rule: the secret must still be redacted at load
+        # written by the librarian but NOT via write_rule: the secret must still be redacted at load.
+        # Today PV-1 refuses such a write; a row stored before PV-1 is what load-time redaction guards.
+        monkeypatch.setattr(write_service, "_check_secrets", lambda items: None)
         await write(
             conn,
             lib,
             write_req(MEMORY_PROJECT, [{**rule, "title": "r1", "body": f"Never log {SECRET}"}]),
             deps=deps,
         )
+        monkeypatch.undo()
         await write(
             conn, lib, write_req(MEMORY_PROJECT, [{**rule, "title": "r2", "body": body_text}]), deps=deps
         )
diff --git a/tests/integration/test_pv_validations.py b/tests/integration/test_pv_validations.py
new file mode 100644
index 0000000..92b801a
--- /dev/null
+++ b/tests/integration/test_pv_validations.py
@@ -0,0 +1,301 @@
+"""PV-1..PV-5 (protocol §5.2) against a real database: refusals write nothing and never echo a
+secret; the legitimate side (imports with ``source``, the librarian's own write) still passes; and
+replay rebuilds historic events that today's checks would refuse, byte for byte."""
+
+from __future__ import annotations
+
+import json
+import uuid
+from typing import Any
+
+import pytest
+
+from hlmemo.auth.context import AuthContext, Role
+from hlmemo.core import lesson_service, write_models, write_service
+from hlmemo.core.errors import ToolError
+from hlmemo.core.write_service import call_the_day, default_deps, write
+from hlmemo.db.replay import rebuild_projections
+from hlmemo.librarian.reserved import ensure_reserved_rows
+from tests.integration._write_fixtures import (
+    MAIN,
+    World,
+    count,
+    dump_projections,
+    item,
+    seed_world,
+    write_req,
+)
+from tests.unit.test_pv_validations import FAKE
+
+pytestmark = pytest.mark.integration
+
+
+@pytest.fixture(scope="session")
+def deps():
+    return default_deps()
+
+
+@pytest.fixture
+async def world(connect) -> World:
+    async with await connect() as conn:
+        w = await seed_world(conn)
+        await ensure_reserved_rows(conn)
+        await conn.commit()
+        return w
+
+
+async def _counts(conn) -> tuple[int, int]:
+    return await count(conn, "events"), await count(conn, "memory_versions")
+
+
+def _close_req(**kw: Any) -> dict[str, Any]:
+    return {
+        "project": MAIN,
+        "request_id": str(uuid.uuid4()),
+        "session_id": str(uuid.uuid4()),
+        "client": "pytest/0",
+        "notes": "Session notes.",
+        **kw,
+    }
+
+
+def _source(path: str) -> dict[str, Any]:
+    return {"system": "markdown", "path": path, "sha256": "0" * 64}
+
+
+# --------------------------------------------------------------------------- PV-1
+@pytest.mark.parametrize("rule", sorted(FAKE))
+@pytest.mark.parametrize("field", ["title", "body", "tags"])
+async def test_memory_write_refuses_each_rule_in_each_field(
+    connect, world, deps, rule: str, field: str
+) -> None:
+    secret = FAKE[rule]
+    it = item("Plain title", "Plain body.")
+    if field == "tags":
+        it["tags"] = ["ok", f"k {secret}"[:64]]
+    else:
+        it[field] = f"see {secret} here"
+    async with await connect() as conn:
+        before = await _counts(conn)
+        with pytest.raises(ToolError) as exc:
+            await write(conn, world.ctx_a, write_req(MAIN, [item("Clean", "Clean body."), it]), deps=deps)
+        await conn.rollback()
+        assert await _counts(conn) == before
+    err = exc.value
+    assert err.code == "E_INVALID_ARG"
+    assert err.details == {"index": 1, "field": field, "reason": "secret_pattern", "rule": rule}
+    assert secret not in json.dumps(err.as_error())
+
+
+@pytest.mark.parametrize(
+    "patch",
+    [
+        {"notes": "token " + FAKE["github-token"]},
+        {"decisions": ["use " + FAKE["sk-api-key"]]},
+        {"lessons": [{"title": "t", "body": "key " + FAKE["aws-access-key"]}]},
+        {"card_update": {"body": "card " + FAKE["jwt"]}},
+    ],
+)
+async def test_call_the_day_refuses_secrets_in_derived_items(
+    connect, world, deps, patch: dict[str, Any]
+) -> None:
+    async with await connect() as conn:
+        before = await _counts(conn)
+        with pytest.raises(ToolError) as exc:
+            await call_the_day(conn, world.ctx_a, _close_req(**patch), deps=deps)
+        await conn.rollback()
+        assert await _counts(conn) == before
+    assert exc.value.code == "E_INVALID_ARG" and exc.value.details["reason"] == "secret_pattern"
+    assert all(v not in json.dumps(exc.value.as_error()) for v in FAKE.values())
+
+
+@pytest.mark.parametrize("part", ["mistake", "fix", "context"])
+async def test_register_lesson_refuses_secrets(connect, world, deps, part: str) -> None:
+    args = {
+        "project": MAIN,
+        "request_id": str(uuid.uuid4()),
+        "mistake": "Title line\nWhen: it went wrong.",
+        "fix": "Do: this. Avoid: that.",
+        "context": "Evidence: none.",
+    }
+    args[part] = args[part] + " " + FAKE["slack-token"]
+    async with await connect() as conn:
+        with pytest.raises(ToolError) as exc:
+            await lesson_service.register_lesson(conn, world.ctx_a, args, deps=deps)
+        await conn.rollback()
+    assert exc.value.details["reason"] == "secret_pattern" and exc.value.details["rule"] == "slack-token"
+    assert FAKE["slack-token"] not in json.dumps(exc.value.as_error())
+
+
+async def test_clean_text_and_an_import_with_source_pass(connect, world, deps) -> None:
+    async with await connect() as conn:
+        res = await write(
+            conn,
+            world.ctx_a,
+            write_req(
+                MAIN,
+                [
+                    item(
+                        "Key location",
+                        "The OpenRouter key lives in .env (OPENROUTER_API_KEY); never paste it.",
+                    ),
+                    item("Imported doc", "Use sk-learn.", source=_source("docs/a.md")),
+                ],
+            ),
+            deps=deps,
+        )
+        await conn.commit()
+    assert len(res.versions) == 2
+
+
+# --------------------------------------------------------------------------- PV-2
+async def test_raw_supersedes_link_without_source_is_refused(connect, world, deps) -> None:
+    async with await connect() as conn:
+        old = (
+            await write(conn, world.ctx_a, write_req(MAIN, [item("Old", "Old state.")]), deps=deps)
+        ).versions[0]
+        await conn.commit()
+        before = await _counts(conn)
+        for links, n_items in (
+            ([{"rel": "supersedes", "target": old.logical_id}], 1),
+            ([{"rel": "supersedes", "target": "$1"}], 2),
+        ):
+            items = [item("New", "New state.", links=links)] + ([item("Other", "x")] if n_items == 2 else [])
+            with pytest.raises(ToolError) as exc:
+                await write(conn, world.ctx_a, write_req(MAIN, items), deps=deps)
+            await conn.rollback()
+            assert exc.value.code == "E_INVALID_ARG"
+            assert exc.value.details == {"index": 0, "reason": "supersedes_needs_updates"}
+            assert "updates" in exc.value.message
+        assert await _counts(conn) == before
+
+
+async def test_supersedes_with_source_and_other_relations_pass(connect, world, deps) -> None:
+    async with await connect() as conn:
+        old = (
+            await write(conn, world.ctx_a, write_req(MAIN, [item("Old", "Old state.")]), deps=deps)
+        ).versions[0]
+        await conn.commit()
+        res = await write(
+            conn,
+            world.ctx_a,
+            write_req(
+                MAIN,
+                [
+                    item(
+                        "Imported new",
+                        "New state.",
+                        source=_source("docs/new.md"),
+                        links=[{"rel": "supersedes", "target": old.logical_id}],
+                    ),
+                    item("Related", "See also.", links=[{"rel": "relates_to", "target": old.logical_id}]),
+                ],
+            ),
+            deps=deps,
+        )
+        await conn.commit()
+        assert len(res.versions) == 2
+        assert await count(conn, "links", "rel = 'supersedes'") == 1
+
+
+# --------------------------------------------------------------------------- PV-4
+async def test_a_granted_device_is_still_refused_over_mcp_and_the_librarian_writes(
+    connect, world, deps
+) -> None:
+    from hlmemo.librarian.memory import write_rule
+    from hlmemo.librarian.reserved import MEMORY_PROJECT, reserved_ids
+    from hlmemo.server.tools import handlers, risk
+
+    async with await connect() as conn:
+        ids = await reserved_ids(conn)
+        ctx = AuthContext(
+            device_id=world.dev_a,
+            device_class="personal",
+            is_admin=False,
+            token_generation=1,
+            grants={world.main_id: Role.WRITE, ids.memory_project_id: Role.WRITE},
+            client="pytest/0",
+        )
+        before = await _counts(conn)
+        calls = [
+            handlers.memory_write(conn, ctx, write_req(MEMORY_PROJECT, [item("x", "y")])),
+            handlers.memory_write(
+                conn, ctx, write_req(MAIN, [item("x", "y", project_ids=[MAIN, MEMORY_PROJECT])])
+            ),
+            handlers.memory_call_the_day(conn, ctx, _close_req(project=MEMORY_PROJECT)),
+            risk.memory_register_lesson(
+                conn,
+                ctx,
+                {"project": MEMORY_PROJECT, "request_id": str(uuid.uuid4()), "mistake": "m", "fix": "f"},
+            ),
+        ]
+        for call in calls:
+            with pytest.raises(ToolError) as exc:
+                await call
+            assert exc.value.code == "E_FORBIDDEN_PROJECT"
+            assert exc.value.details == {"project": MEMORY_PROJECT, "reason": "reserved_project"}
+        await conn.rollback()
+        assert await _counts(conn) == before
+        vid = await write_rule(
+            conn,
+            title="Rule",
+            text="Prefer dated evidence over memory claims.",
+            clue_refs=[],
+            dedupe="pv4",
+            deps=deps,
+        )
+        await conn.commit()
+        assert vid > 0
+
+
+# --------------------------------------------------------------------------- PV-5 through register_lesson
+async def test_register_lesson_refuses_a_conflicting_status(connect, world, deps) -> None:
+    args = {
+        "project": MAIN,
+        "request_id": str(uuid.uuid4()),
+        "mistake": "Title\nWhen: x.",
+        "fix": "Do: y.",
+        "tags": ["py@3.12", "active", "historical"],
+    }
+    async with await connect() as conn:
+        with pytest.raises(ToolError) as exc:
+            await lesson_service.register_lesson(conn, world.ctx_a, args, deps=deps)
+        await conn.rollback()
+        args["tags"] = ["py@3.12", "resolved", "historical"]
+        res = await lesson_service.register_lesson(conn, world.ctx_a, args, deps=deps)
+        await conn.commit()
+    assert exc.value.details["reason"] == "lesson_status_conflict"
+    assert res["clue"].startswith("v")
+
+
+# --------------------------------------------------------------------------- replay
+async def test_replay_rebuilds_historic_events_that_today_would_be_refused(
+    connect, world, deps, monkeypatch: pytest.MonkeyPatch
+) -> None:
+    """Events written before PV-2/PV-3 (simulated by switching the checks off) replay byte-identically:
+    ``db/replay.py`` never calls the write service, so no validation runs on replay."""
+    async with await connect() as conn:
+        old = (
+            await write(conn, world.ctx_a, write_req(MAIN, [item("Old", "Old state.")]), deps=deps)
+        ).versions[0]
+        await conn.commit()
+        historic = write_req(
+            MAIN,
+            [
+                item("New", "New state.", links=[{"rel": "supersedes", "target": old.logical_id}]),
+                item("   ", "A blank title, as an old client could send it."),
+            ],
+        )
+        with pytest.raises(ToolError):
+            await write(conn, world.ctx_a, historic, deps=deps)
+        await conn.rollback()
+        monkeypatch.setattr(write_service, "_check_supersedes_links", lambda i, it: None)
+        monkeypatch.setattr(write_models, "_reject_blank", lambda v: v)
+        await write(conn, world.ctx_a, historic, deps=deps)
+        await conn.commit()
+        monkeypatch.undo()
+        before = await dump_projections(conn)
+        await rebuild_projections(conn)
+        await conn.commit()
+        assert await dump_projections(conn) == before
+        assert await count(conn, "links", "rel = 'supersedes'") == 1
diff --git a/tests/integration/test_superseded_read.py b/tests/integration/test_superseded_read.py
index 3293cc6..fa0cf9b 100644
--- a/tests/integration/test_superseded_read.py
+++ b/tests/integration/test_superseded_read.py
@@ -272,6 +272,7 @@ async def test_raw_superseded_by_respects_the_valid_time_of_the_version(
                 "The cache notes moved to the ops runbook.",
                 valid_from=D_OLD.isoformat(),
                 valid_to=d_mar.isoformat(),
+                source={"system": "markdown", "path": "docs/pointer.md", "sha256": "0" * 64},  # PV-2: a raw supersedes link needs an import's source
                 links=[{"rel": "supersedes", "target": old["logical_id"]}],
             )
         ],
diff --git a/tests/integration/test_write_updates_r76.py b/tests/integration/test_write_updates_r76.py
index efc13c7..a919b44 100644
--- a/tests/integration/test_write_updates_r76.py
+++ b/tests/integration/test_write_updates_r76.py
@@ -193,6 +193,7 @@ async def test_r76_2_a_carrier_declaring_the_same_supersedes_link_is_a_per_updat
         [
             item(
                 *NEW_TTL,
+                source={"system": "markdown", "path": "docs/new-ttl.md", "sha256": "0" * 64},  # PV-2: a raw supersedes link needs an import's source
                 links=[{"rel": "supersedes", "target": target["logical_id"]}],
                 updates=[_upd(target, replacement=REPL, mode=mode)],
             )
@@ -231,6 +232,7 @@ async def test_r76_4_an_existing_edge_that_only_overlaps_a_backdated_update_is_a
                     "Cache plan",
                     "The cache will be removed.",
                     valid_from=D_LATE.isoformat(),
+                    source={"system": "markdown", "path": "docs/cache-plan.md", "sha256": "0" * 64},  # PV-2: a raw supersedes link needs an import's source
                     links=[{"rel": "supersedes", "target": target["logical_id"]}],
                 )
             ],
diff --git a/tests/integration/test_write_updates_r96.py b/tests/integration/test_write_updates_r96.py
index fd09ac9..a14b0c2 100644
--- a/tests/integration/test_write_updates_r96.py
+++ b/tests/integration/test_write_updates_r96.py
@@ -483,6 +483,7 @@ async def test_r96_sol4_superseded_by_needs_the_link_and_the_version_to_overlap(
                     "Pointer",
                     "The cache notes moved to the ops runbook.",
                     valid_from=D_APR.isoformat(),
+                    source={"system": "markdown", "path": "docs/pointer.md", "sha256": "0" * 64},  # PV-2: a raw supersedes link needs an import's source
                     links=[{"rel": "supersedes", "target": v["logical_id"]} for v in vs],
                 )
             ],
diff --git a/tests/unit/test_d033_fixes.py b/tests/unit/test_d033_fixes.py
index 4edb219..0e2b832 100644
--- a/tests/unit/test_d033_fixes.py
+++ b/tests/unit/test_d033_fixes.py
@@ -128,7 +128,7 @@ def test_f06_long_error_messages_are_clipped():
 
 
 # --------------------------------------------------------------------------- F12
-@pytest.mark.parametrize("decisions", [[], ["a"], ["a", "b c", ""]])
+@pytest.mark.parametrize("decisions", [[], ["a"], ["a", "b c", " d "]])  # a blank entry is refused (PV-3)
 def test_f12_session_note_body_matches_close_items(decisions):
     req = CloseRequest.model_validate(
         {
diff --git a/tests/unit/test_pv_validations.py b/tests/unit/test_pv_validations.py
new file mode 100644
index 0000000..f95b83c
--- /dev/null
+++ b/tests/unit/test_pv_validations.py
@@ -0,0 +1,176 @@
+"""PV-1..PV-5 (protocol §5.2): the write-path validations that need no database.
+
+The fake secrets are built by concatenation so that no key-shaped literal sits in the source (the
+pre-push privacy gate scans it).
+"""
+
+from __future__ import annotations
+
+import uuid
+from typing import Any
+
+import pytest
+
+from hlmemo.core.errors import ToolError
+from hlmemo.core.secret_guard import STRONG_SECRET_RULES, strong_secret_rule
+from hlmemo.core.write_models import CloseRequest, WriteRequest, parse_request
+from hlmemo.importers.common import SECRET_PATTERNS
+
+FAKE: dict[str, str] = {
+    "private-key": "-----BEGIN RSA " + "PRIVATE KEY-----",
+    "aws-access-key": "AKIA" + "ABCDEFGHIJKLMNOP",
+    "github-token": "ghp_" + "FakeTokenForTests" * 2 + "xy",
+    "slack-token": "xox" + "b-" + "1234567890-fake",
+    "google-api-key": "AIza" + "FAKEkeyNotRealForTests" + "0123456789abc",
+    "hlm-token": "hlm_" + "A" * 43,
+    "jwt": "eyJ" + "a" * 12 + ".eyJ" + "b" * 12 + "." + "c" * 12,
+    "sk-api-key": "sk-" + "proj-" + "F" * 30,
+}
+
+
+# --------------------------------------------------------------------------- PV-1 secret_guard
+@pytest.mark.parametrize("rule", sorted(FAKE))
+def test_every_strong_rule_matches_its_shape(rule: str) -> None:
+    assert strong_secret_rule(f"the value is {FAKE[rule]} here") == rule
+
+
+@pytest.mark.parametrize(
+    "text",
+    [
+        "Use sk-learn for the baseline model.",
+        "The key looks like sk-... and lives in .env (OPENROUTER_API_KEY).",
+        "sk-" + "x" * 30,  # an unprefixed sk- string: outside the narrowed rule (D-219 false positives)
+        "password: see the keychain entry hlm/prod",
+        "postgresql://hlm:hlm@127.0.0.1:5432/hlm_test",  # dsn-with-password stays out of PV-1
+        "AKIA is the prefix of AWS access keys.",
+        "eyJ is how a base64 JSON object starts.",
+    ],
+)
+def test_placeholders_and_prose_pass(text: str) -> None:
+    assert strong_secret_rule(text) is None
+
+
+def test_shared_rules_equal_the_importer_rules_and_sk_is_narrowed() -> None:
+    shared = set(STRONG_SECRET_RULES) - {"sk-api-key"}
+    for rule in shared:
+        assert STRONG_SECRET_RULES[rule].pattern == SECRET_PATTERNS[rule].pattern, rule
+    assert set(STRONG_SECRET_RULES) == shared | {"sk-api-key"}
+    assert "assigned-secret" not in STRONG_SECRET_RULES and "dsn-with-password" not in STRONG_SECRET_RULES
+    assert STRONG_SECRET_RULES["sk-api-key"].pattern == r"\bsk-(?:or-v1-|ant-|proj-)[A-Za-z0-9_-]{24,}"
+
+
+# --------------------------------------------------------------------------- PV-3 blank
+def _write(**item: Any) -> dict[str, Any]:
+    it = {"kind": "fact", "title": "T", "body": "B", **item}
+    return {"project": "p-one", "request_id": str(uuid.uuid4()), "client": "pytest/0", "items": [it]}
+
+
+def _close(**kw: Any) -> dict[str, Any]:
+    return {
+        "project": "p-one",
+        "request_id": str(uuid.uuid4()),
+        "session_id": str(uuid.uuid4()),
+        "client": "pytest/0",
+        "notes": "Done.",
+        **kw,
+    }
+
+
+def _refused(model: Any, raw: dict[str, Any]) -> ToolError:
+    with pytest.raises(ToolError) as exc:
+        parse_request(model, raw)
+    return exc.value
+
+
+BLANKS = [" ", "\n\t ", " ", "  \n"]
+
+
+@pytest.mark.parametrize("blank", BLANKS)
+@pytest.mark.parametrize("field", ["title", "body"])
+def test_blank_item_text_is_refused(field: str, blank: str) -> None:
+    err = _refused(WriteRequest, _write(**{field: blank}))
+    assert err.code == "E_INVALID_ARG"
+    assert err.details["reason"] == "blank" and err.details["index"] == 0
+    assert "must not be blank" in err.message
+
+
+@pytest.mark.parametrize("blank", BLANKS)
+def test_blank_close_fields_are_refused(blank: str) -> None:
+    cases = [
+        _close(notes=blank),
+        _close(decisions=["fine", blank]),
+        _close(lessons=[{"title": blank, "body": "b"}]),
+        _close(lessons=[{"title": "t", "body": blank}]),
+        _close(card_update={"body": blank}),
+    ]
+    for raw in cases:
+        err = _refused(CloseRequest, raw)
+        assert err.code == "E_INVALID_ARG" and err.details["reason"] == "blank", raw
+
+
+def test_text_with_content_passes() -> None:
+    parse_request(WriteRequest, _write(title=" a title ", body=" body "))
+    parse_request(
+        CloseRequest,
+        _close(decisions=["one"], lessons=[{"title": "t", "body": "b"}], card_update={"body": "card"}),
+    )
+
+
+# --------------------------------------------------------------------------- PV-5 lesson status
+@pytest.mark.parametrize(
+    "tags", [["active", "resolved"], ["active", "historical"], ["x@1", "active", "resolved"]]
+)
+@pytest.mark.parametrize("kind", ["lesson", "experience"])
+def test_active_with_resolved_or_historical_is_refused(kind: str, tags: list[str]) -> None:
+    err = _refused(WriteRequest, _write(kind=kind, tags=tags))
+    assert err.code == "E_INVALID_ARG"
+    assert err.details["reason"] == "lesson_status_conflict" and err.details["index"] == 0
+
+
+@pytest.mark.parametrize(
+    ("kind", "tags"),
+    [
+        ("lesson", ["resolved", "historical"]),  # the D-234 import form
+        ("lesson", ["active"]),
+        ("lesson", ["resolved"]),
+        ("fact", ["active", "resolved"]),  # only lessons carry a status
+        ("episode", ["active", "historical"]),
+    ],
+)
+def test_valid_status_tags_pass(kind: str, tags: list[str]) -> None:
+    parse_request(WriteRequest, _write(kind=kind, tags=tags))
+
+
+def test_call_the_day_lessons_get_the_same_status_check() -> None:
+    err = _refused(
+        CloseRequest, _close(lessons=[{"title": "t", "body": "b", "tags": ["active", "resolved"]}])
+    )
+    assert err.code == "E_INVALID_ARG" and err.details["reason"] == "lesson_status_conflict"
+
+
+def test_the_reason_points_at_the_offending_item() -> None:
+    raw = _write()
+    raw["items"].append({"kind": "lesson", "title": "L", "body": "B", "tags": ["active", "historical"]})
+    err = _refused(WriteRequest, raw)
+    assert err.details["index"] == 1 and err.details["reason"] == "lesson_status_conflict"
+
+
+# --------------------------------------------------------------------------- PV-4 reserved project (MCP path)
+@pytest.mark.parametrize("tool", ["memory_write", "memory_call_the_day", "memory_register_lesson"])
+async def test_mcp_write_tools_refuse_the_librarian_project(tool: str) -> None:
+    from hlmemo.server.tools import handlers, risk
+
+    fn = getattr(handlers, tool, None) or getattr(risk, tool)
+    with pytest.raises(ToolError) as exc:
+        await fn(None, None, {"project": "hlm-librarian", "items": []})  # refused before any database use
+    assert exc.value.code == "E_FORBIDDEN_PROJECT"
+    assert exc.value.details == {"project": "hlm-librarian", "reason": "reserved_project"}
+
+
+async def test_mcp_write_refuses_the_librarian_project_in_project_ids() -> None:
+    from hlmemo.server.tools import handlers
+
+    args = _write(project_ids=["p-one", "hlm-librarian"])
+    with pytest.raises(ToolError) as exc:
+        await handlers.memory_write(None, None, args)
+    assert exc.value.code == "E_FORBIDDEN_PROJECT" and exc.value.details["reason"] == "reserved_project"
