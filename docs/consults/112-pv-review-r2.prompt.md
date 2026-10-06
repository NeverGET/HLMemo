Round 2 (final) of the review of HLMemo's PV-1..PV-5 write-path validations. The working directory is the candidate's worktree (read-only). Round 1 (commit 77d3404) was NO-GO; its findings are in /Users/cemalkurt/Projects/HLMemo/docs/consults/111-pv-review-astra.md and 111-pv-review-sol.md. The fix commit is 7bf72d2. Threat model and rubric: /Users/cemalkurt/Projects/HLMemo/docs/consults/110-pv-threat-model.md.

Decisions taken by the orchestrator (do not re-raise as findings unless the fix is wrong): JWT is removed from the write-path rules (documentation examples are legitimate memory text; importer and capture still refuse JWTs); PV-2's `source` exemption stays forgeable by design and goes to the owner as a residual risk (documented in protocol §5.2 PV-2).

Reply in <= 30 lines: verdict (GO / GO-WITH-FIXES / NO-GO); then for EACH round-1 finding one line "fixed | not fixed | partially" with file:line evidence; then NEW findings only (HIGH/MEDIUM/LOW per the rubric, file:line, concrete trigger), especially regressions the wider scan may cause: false rejects of legitimate fields (e.g. request_id/UUIDs, sha256 digests, base64 bodies, URLs, code), performance on a 64,000-char body x 50 items, and any path where a value is still echoed or persisted before the scan. Measured: the wide scan refuses 0 of 49,932 strings/keys of 1,629 real items.

## Fix diff (77d3404..7bf72d2, src and tests)
diff --git a/src/hlmemo/core/lesson_service.py b/src/hlmemo/core/lesson_service.py
index 2a8fe11..e2a677b 100644
--- a/src/hlmemo/core/lesson_service.py
+++ b/src/hlmemo/core/lesson_service.py
@@ -49,6 +49,7 @@ from hlmemo.core.clues import encode_clue
 from hlmemo.core.write_models import (
     DEVICE_SCOPE_RE,
     SLUG_RE,
+    _reject_blank,
     _Strict,
     canonical_device_scope,
     parse_request,
@@ -81,9 +82,7 @@ class LessonRequest(_Strict):
     @field_validator("mistake", "fix", "context")
     @classmethod
     def _not_blank(cls, v: str | None) -> str | None:
-        if v is not None and not v.strip():
-            raise ValueError("must not be blank")
-        return v
+        return v if v is None else _reject_blank(v)  # PV-3: details.reason "blank"
 
 
 def lesson_title(mistake: str) -> str:
@@ -121,6 +120,7 @@ async def register_lesson(
     *,
     deps: WriteDeps | None = None,
 ) -> dict[str, Any]:
+    write_service._check_secrets(args)  # PV-1 over the raw arguments, before parsing
     request = parse_request(LessonRequest, args)
     deps = deps or write_service.default_deps()
     write_args = {
diff --git a/src/hlmemo/core/secret_guard.py b/src/hlmemo/core/secret_guard.py
index 3bb578e..3375539 100644
--- a/src/hlmemo/core/secret_guard.py
+++ b/src/hlmemo/core/secret_guard.py
@@ -1,19 +1,27 @@
 """PV-1 ``secret_pattern`` (protocol R7): the strong secret shapes no memory text needs.
 
 History is append-only, so a secret that reaches memory can only be erased by an owner-run database
-procedure. The write path therefore refuses text that matches one of these shapes. They are the
-strong shapes of the importer's refusal set (``importers/common.py`` ``SECRET_PATTERNS``) and of the
-capture scrub, with ``sk-api-key`` narrowed to the known key prefixes so that prose such as
-``sk-learn`` or a placeholder ``sk-...`` is never refused. The importer's broader rules
-(``assigned-secret``, ``dsn-with-password``, env-style assignments, credential pairs) stay out: they
-reject legitimate writes too often (D-219).
-
-The matched value is never returned: callers name the rule, the field and the item index only.
+procedure. The write path therefore refuses a request in which ANY string value or dict key of the
+raw arguments matches one of these shapes (``find_secret``): the event log stores the arguments
+verbatim, so a field the server never indexes (``updates[].replacement``, ``source.path``,
+``client``) would keep a secret just as well as a body.
+
+The shapes are the importer's strong refusal set (``importers/common.py`` ``SECRET_PATTERNS``) and
+the capture scrub's, with two differences:
+- ``sk-api-key`` is narrowed to the known key prefixes, so prose such as ``sk-learn`` or a
+  placeholder ``sk-...`` passes;
+- ``jwt`` stays out: documentation examples (the jwt.io sample, test fixtures) are legitimate memory
+  text and have the same shape as a live token (review 111).
+The importer's broader rules (``assigned-secret``, ``dsn-with-password``, env-style assignments,
+credential pairs) stay out as well: they reject legitimate writes too often (D-219).
+
+The matched value is never returned: callers name the rule and a field path only.
 """
 
 from __future__ import annotations
 
 import re
+from typing import Any
 
 STRONG_SECRET_RULES: dict[str, re.Pattern[str]] = {
     "private-key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
@@ -22,10 +30,12 @@ STRONG_SECRET_RULES: dict[str, re.Pattern[str]] = {
     "slack-token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
     "google-api-key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
     "hlm-token": re.compile(r"\bhlm_[A-Za-z0-9_-]{43}\b"),
-    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
     "sk-api-key": re.compile(r"\bsk-(?:or-v1-|ant-|proj-)[A-Za-z0-9_-]{24,}"),
 }
 
+#: a reported field path is clipped to this length (paths are built from key names, never values)
+PATH_MAX = 200
+
 
 def strong_secret_rule(text: str) -> str | None:
     """The id of the first strong secret rule that ``text`` matches, else None."""
@@ -35,4 +45,36 @@ def strong_secret_rule(text: str) -> str | None:
     return None
 
 
-__all__ = ["STRONG_SECRET_RULES", "strong_secret_rule"]
+def find_secret(args: Any) -> tuple[str, str] | None:
+    """``(field_path, rule)`` of the first string value or dict key in ``args`` (any nesting of
+    dicts, lists and tuples) that matches a strong secret rule, else None.
+
+    The path names keys and list positions (``items[0].updates[0].replacement``, ``project``,
+    ``decisions[1]``); a key that is itself secret-shaped is reported as ``<key>`` under its parent,
+    so the path never carries the value."""
+    stack: list[tuple[Any, str]] = [(args, "")]
+    while stack:
+        obj, path = stack.pop()
+        if isinstance(obj, str):
+            rule = strong_secret_rule(obj)
+            if rule is not None:
+                return _clip(path or "<value>"), rule
+        elif isinstance(obj, dict):
+            children: list[tuple[Any, str]] = []
+            for key, value in obj.items():
+                name = str(key)
+                rule = strong_secret_rule(name)
+                if rule is not None:
+                    return _clip(f"{path}.<key>" if path else "<key>"), rule
+                children.append((value, f"{path}.{name}" if path else name))
+            stack.extend(reversed(children))
+        elif isinstance(obj, list | tuple):
+            stack.extend(reversed([(v, f"{path}[{i}]") for i, v in enumerate(obj)]))
+    return None
+
+
+def _clip(path: str) -> str:
+    return path if len(path) <= PATH_MAX else path[: PATH_MAX - 1] + "…"
+
+
+__all__ = ["STRONG_SECRET_RULES", "find_secret", "strong_secret_rule"]
diff --git a/src/hlmemo/core/write_models.py b/src/hlmemo/core/write_models.py
index 3396cef..bf87c18 100644
--- a/src/hlmemo/core/write_models.py
+++ b/src/hlmemo/core/write_models.py
@@ -56,8 +56,9 @@ def _reject_blank(v: Any) -> Any:
 
 def lesson_status_conflict(kind: str, tags: Iterable[str]) -> bool:
     """PV-5 (R15, D-234): a lesson is ``active`` or concluded (``resolved``/``historical``), never
-    both. ``resolved`` + ``historical`` stays valid (the D-234 import form); other kinds are free."""
-    t = set(tags)
+    both. ``resolved`` + ``historical`` stays valid (the D-234 import form); other kinds are free.
+    Tags are compared stripped and case-folded, so ``Active`` counts as ``active`` (review 111)."""
+    t = {tag.strip().casefold() for tag in tags}
     return kind in LESSON_KINDS and "active" in t and bool(t & _CONCLUDED)
 
 
@@ -76,7 +77,7 @@ def canonical_device_scope(v: str) -> str:
     if v.startswith("device:"):
         digits = v[len("device:") :]
         if digits.startswith("0"):
-            raise ValueError(f"device_scope {v!r} is not canonical: use 'device:<id>' without leading zeros")
+            raise ValueError("device_scope is not canonical: use 'device:<id>' without leading zeros")
     return v
 
 
@@ -440,12 +441,28 @@ def _format_validation_error(exc: ValidationError) -> str:
     return "; ".join(parts)
 
 
+#: fields whose empty value is a PV-3 ``blank`` refusal too (``min_length=1`` fires before the
+#: blank validator, so ``""`` arrives as ``string_too_short``): titles, bodies, notes, lesson parts
+BLANK_FIELDS = frozenset({"title", "body", "notes", "mistake", "fix", "context"})
+
+
+def _reason_type(err: Any) -> str | None:
+    kind = err.get("type")
+    if kind in REASON_TYPES:
+        return str(kind)
+    loc = err.get("loc", ())
+    if kind == "string_too_short" and loc and loc[-1] in BLANK_FIELDS:
+        return "blank"
+    return None
+
+
 def _reason_details(exc: ValidationError) -> dict[str, Any]:
     """Protocol §5.2: a PV-3/PV-5 refusal names its ``reason`` (and the ``index`` of the item, when
     the error sits under ``items[i]``), like ``missing_home``/``duplicate_project`` do."""
     for err in exc.errors(include_input=False, include_url=False):
-        if err.get("type") in REASON_TYPES:
-            out: dict[str, Any] = {"reason": err["type"]}
+        reason = _reason_type(err)
+        if reason is not None:
+            out: dict[str, Any] = {"reason": reason}
             loc = err.get("loc", ())
             if len(loc) >= 2 and loc[0] == "items" and isinstance(loc[1], int):
                 out["index"] = loc[1]
@@ -463,13 +480,10 @@ def parse_request[T: BaseModel](model: type[T], raw: T | dict[str, Any]) -> T:
         return model.model_validate(raw)
     except ValidationError as exc:
         errors, total = bounded_validation_errors(exc)
-        raise ToolError(
-            "E_INVALID_ARG",
-            _format_validation_error(exc),
-            errors=errors,
-            error_count=total,
-            **_reason_details(exc),
-        ) from exc
+        message, extra = _format_validation_error(exc), _reason_details(exc)
+    # Raised outside the ``except`` block: neither ``__cause__`` nor ``__context__`` keeps the
+    # ValidationError, whose text carries the input values (review 111, F06).
+    raise ToolError("E_INVALID_ARG", message, errors=errors, error_count=total, **extra)
 
 
 __all__ = [
diff --git a/src/hlmemo/core/write_service.py b/src/hlmemo/core/write_service.py
index cde0e71..fca44c2 100644
--- a/src/hlmemo/core/write_service.py
+++ b/src/hlmemo/core/write_service.py
@@ -37,6 +37,7 @@ from __future__ import annotations
 
 import asyncio
 import hashlib
+import re
 from collections.abc import Callable
 from concurrent.futures import ThreadPoolExecutor
 from dataclasses import dataclass, field, replace
@@ -62,7 +63,7 @@ from hlmemo.core.chunker import CHUNK_OVERLAP, CHUNK_TOK, Chunker
 from hlmemo.core.embedder import default_model_dir, sha256_file
 from hlmemo.core.errors import ToolError, invalid_arg
 from hlmemo.core.normalize import normalize
-from hlmemo.core.secret_guard import strong_secret_rule
+from hlmemo.core.secret_guard import find_secret
 from hlmemo.core.temporal import (
     Interval,
     fmt_ts,
@@ -359,8 +360,8 @@ async def write(
     ``memory.register_lesson`` sets 2; W1.5: a batch whose items all carry ``source`` is an
     import and gets 6)."""
     request_payload = verbatim_args(req, raw)
+    _check_secrets(request_payload, req)  # PV-1 over the raw arguments, before parsing
     request = parse_request(WriteRequest, req)
-    _check_secrets(request.items)  # PV-1, before any transaction
     if librarian_priority is None and all(it.source is not None for it in request.items):
         librarian_priority = IMPORT_LIBRARIAN_PRIORITY
     deps = deps or default_deps()
@@ -404,6 +405,7 @@ async def call_the_day(
     raw: dict[str, Any] | None = None,
 ) -> CloseResult:
     request_payload = verbatim_args(req, raw)
+    _check_secrets(request_payload, req)  # PV-1 over the raw arguments, before parsing
     request = parse_request(CloseRequest, req)
     deps = deps or default_deps()
     try:
@@ -411,7 +413,6 @@ async def call_the_day(
     except BudgetError as exc:
         raise ToolError(exc.code, str(exc), **exc.details) from exc
     items = _close_items(request)
-    _check_secrets(items)  # PV-1 over the derived note, lessons and card
     async with conn.transaction():
         result = await _execute(
             conn,
@@ -473,21 +474,31 @@ def _close_items(request: CloseRequest) -> list[Item]:
 
 
 # --------------------------------------------------------------------------- write-path validations
-def _check_secrets(items: list[Item]) -> None:
-    """PV-1 ``secret_pattern`` (R7): a title, body or tag matching a strong secret shape is
-    refused before the transaction, because append-only history can only be erased by an owner-run
-    database procedure. The message names the rule, the field and the index, never the value."""
-    for i, it in enumerate(items):
-        for name, text in (("title", it.title), ("body", it.body), *(("tags", t) for t in it.tags)):
-            rule = strong_secret_rule(text)
-            if rule is not None:
-                raise invalid_arg(
-                    f"items[{i}].{name} matches secret rule {rule}: remove it or name where it is stored",
-                    index=i,
-                    field=name,
-                    reason="secret_pattern",
-                    rule=rule,
-                )
+def _check_secrets(*payloads: Any) -> None:
+    """PV-1 ``secret_pattern`` (R7): every string value and dict key of the raw arguments (and of
+    the request the service was handed, when it differs) is checked against the strong secret
+    shapes before parsing, authorization or the transaction. Append-only history can only be erased
+    by an owner-run database procedure, and the event stores the arguments verbatim, so no field is
+    exempt (``updates[].replacement``, ``source.path``, ``client``, ``project`` ...). A refusal names
+    the field path and the rule, never the value; a secret therefore never reaches the model
+    validation or an authorization error either."""
+    for payload in payloads:
+        if not isinstance(payload, dict | list):
+            payload = payload.model_dump(mode="json") if hasattr(payload, "model_dump") else None
+        hit = find_secret(payload)
+        if hit is None:
+            continue
+        path, rule = hit
+        details: dict[str, Any] = {"field": path, "reason": "secret_pattern", "rule": rule}
+        m = _ITEM_INDEX.match(path)
+        if m is not None:
+            details["index"] = int(m.group(1))
+        raise invalid_arg(
+            f"{path} matches secret rule {rule}: remove it or name where it is stored", **details
+        )
+
+
+_ITEM_INDEX = re.compile(r"^items\[(\d+)\]")
 
 
 def _check_supersedes_links(i: int, it: Item) -> None:
diff --git a/tests/integration/test_librarian_attempt_gate.py b/tests/integration/test_librarian_attempt_gate.py
index 92012ae..1750559 100644
--- a/tests/integration/test_librarian_attempt_gate.py
+++ b/tests/integration/test_librarian_attempt_gate.py
@@ -133,7 +133,7 @@ async def test_rules_are_redacted_and_rule_shaped(db_dsn, connect, world, deps,
         rule = {"kind": "fact", "tags": ["librarian-rule"], "importance": 9}
         # written by the librarian but NOT via write_rule: the secret must still be redacted at load.
         # Today PV-1 refuses such a write; a row stored before PV-1 is what load-time redaction guards.
-        monkeypatch.setattr(write_service, "_check_secrets", lambda items: None)
+        monkeypatch.setattr(write_service, "_check_secrets", lambda *payloads: None)
         await write(
             conn,
             lib,
diff --git a/tests/integration/test_pv_validations.py b/tests/integration/test_pv_validations.py
index 92b801a..1085531 100644
--- a/tests/integration/test_pv_validations.py
+++ b/tests/integration/test_pv_validations.py
@@ -25,7 +25,7 @@ from tests.integration._write_fixtures import (
     seed_world,
     write_req,
 )
-from tests.unit.test_pv_validations import FAKE
+from tests.unit.test_pv_validations import FAKE, JWT_EXAMPLES
 
 pytestmark = pytest.mark.integration
 
@@ -83,7 +83,8 @@ async def test_memory_write_refuses_each_rule_in_each_field(
         assert await _counts(conn) == before
     err = exc.value
     assert err.code == "E_INVALID_ARG"
-    assert err.details == {"index": 1, "field": field, "reason": "secret_pattern", "rule": rule}
+    path = "items[1].tags[1]" if field == "tags" else f"items[1].{field}"
+    assert err.details == {"index": 1, "field": path, "reason": "secret_pattern", "rule": rule}
     assert secret not in json.dumps(err.as_error())
 
 
@@ -93,7 +94,7 @@ async def test_memory_write_refuses_each_rule_in_each_field(
         {"notes": "token " + FAKE["github-token"]},
         {"decisions": ["use " + FAKE["sk-api-key"]]},
         {"lessons": [{"title": "t", "body": "key " + FAKE["aws-access-key"]}]},
-        {"card_update": {"body": "card " + FAKE["jwt"]}},
+        {"card_update": {"body": "card " + FAKE["google-api-key"]}},
     ],
 )
 async def test_call_the_day_refuses_secrets_in_derived_items(
@@ -299,3 +300,60 @@ async def test_replay_rebuilds_historic_events_that_today_would_be_refused(
         await conn.commit()
         assert await dump_projections(conn) == before
         assert await count(conn, "links", "rel = 'supersedes'") == 1
+
+
+# --------------------------------------------------------------------------- review 111 (round 1)
+@pytest.mark.parametrize(
+    "extra",
+    [
+        lambda target, s: {
+            "updates": [{"item": target, "old_span": "no such span", "mode": "revise", "replacement": s}]
+        },
+        lambda target, s: {"source": {"system": "markdown", "path": f"docs/{s}.md", "sha256": "0" * 64}},
+        lambda target, s: {"describes": [f"src/{s}.py"]},
+    ],
+    ids=["updates.replacement", "source.path", "describes"],
+)
+async def test_review_secret_outside_title_body_tags_is_refused_and_never_stored(
+    connect, world, deps, extra
+) -> None:
+    """Astra #1 / Sol #1: a secret in a field the server does not index used to be stored verbatim in
+    the carrier's event (the update itself was rejected). Now the whole write is refused."""
+    s = "sk-" + "proj-" + "a" * 30
+    async with await connect() as conn:
+        target = (
+            await write(conn, world.ctx_a, write_req(MAIN, [item("Old", "Old text here.")]), deps=deps)
+        ).versions[0]
+        await conn.commit()
+        before = await _counts(conn)
+        carrier = item("Carrier", "Clean body.", **extra(f"v{target.version_id}", s))
+        with pytest.raises(ToolError) as exc:
+            await write(conn, world.ctx_a, write_req(MAIN, [carrier]), deps=deps)
+        await conn.rollback()
+        assert await _counts(conn) == before
+        cur = await conn.execute("SELECT count(*) FROM events WHERE payload::text LIKE %s", (f"%{s}%",))
+        assert (await cur.fetchone())[0] == 0
+    assert exc.value.details["reason"] == "secret_pattern" and s not in json.dumps(exc.value.as_error())
+
+
+async def test_review_secret_shaped_client_is_refused(connect, world, deps) -> None:
+    req = write_req(MAIN, [item("t", "b")])
+    req["client"] = "agent/" + FAKE["slack-token"]
+    async with await connect() as conn:
+        with pytest.raises(ToolError) as exc:
+            await write(conn, world.ctx_a, req, deps=deps)
+        await conn.rollback()
+    assert exc.value.details["field"] == "client"
+
+
+@pytest.mark.parametrize("example", JWT_EXAMPLES)
+async def test_review_jwt_documentation_example_is_written(connect, world, deps, example: str) -> None:
+    async with await connect() as conn:
+        res = await write(
+            conn,
+            world.ctx_a,
+            write_req(MAIN, [item("JWT format", f"JWT format example: {example}")]),
+            deps=deps,
+        )
+        await conn.commit()
+    assert len(res.versions) == 1
diff --git a/tests/unit/test_pv_validations.py b/tests/unit/test_pv_validations.py
index f95b83c..b255f22 100644
--- a/tests/unit/test_pv_validations.py
+++ b/tests/unit/test_pv_validations.py
@@ -12,7 +12,7 @@ from typing import Any
 import pytest
 
 from hlmemo.core.errors import ToolError
-from hlmemo.core.secret_guard import STRONG_SECRET_RULES, strong_secret_rule
+from hlmemo.core.secret_guard import STRONG_SECRET_RULES, find_secret, strong_secret_rule
 from hlmemo.core.write_models import CloseRequest, WriteRequest, parse_request
 from hlmemo.importers.common import SECRET_PATTERNS
 
@@ -23,9 +23,16 @@ FAKE: dict[str, str] = {
     "slack-token": "xox" + "b-" + "1234567890-fake",
     "google-api-key": "AIza" + "FAKEkeyNotRealForTests" + "0123456789abc",
     "hlm-token": "hlm_" + "A" * 43,
-    "jwt": "eyJ" + "a" * 12 + ".eyJ" + "b" * 12 + "." + "c" * 12,
     "sk-api-key": "sk-" + "proj-" + "F" * 30,
 }
+#: the canonical jwt.io sample (a documentation example, not a credential) and the review's fake one
+JWT_EXAMPLES = (
+    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
+    + ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ"
+    + ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
+    "eyJ" + "a" * 12 + ".eyJ" + "b" * 12 + "." + "c" * 12,
+)
+S = FAKE["github-token"]
 
 
 # --------------------------------------------------------------------------- PV-1 secret_guard
@@ -55,6 +62,7 @@ def test_shared_rules_equal_the_importer_rules_and_sk_is_narrowed() -> None:
     for rule in shared:
         assert STRONG_SECRET_RULES[rule].pattern == SECRET_PATTERNS[rule].pattern, rule
     assert set(STRONG_SECRET_RULES) == shared | {"sk-api-key"}
+    assert "jwt" in SECRET_PATTERNS and "jwt" not in STRONG_SECRET_RULES  # kept for imports only
     assert "assigned-secret" not in STRONG_SECRET_RULES and "dsn-with-password" not in STRONG_SECRET_RULES
     assert STRONG_SECRET_RULES["sk-api-key"].pattern == r"\bsk-(?:or-v1-|ant-|proj-)[A-Za-z0-9_-]{24,}"
 
@@ -174,3 +182,135 @@ async def test_mcp_write_refuses_the_librarian_project_in_project_ids() -> None:
     with pytest.raises(ToolError) as exc:
         await handlers.memory_write(None, None, args)
     assert exc.value.code == "E_FORBIDDEN_PROJECT" and exc.value.details["reason"] == "reserved_project"
+
+
+# --------------------------------------------------------------------------- review 111 (round 1)
+@pytest.mark.parametrize("example", JWT_EXAMPLES)
+def test_jwt_documentation_examples_are_accepted(example: str) -> None:
+    text = f"JWT format example: {example}"
+    assert strong_secret_rule(text) is None and find_secret({"items": [{"body": text}]}) is None
+    parse_request(WriteRequest, _write(body=text))
+
+
+@pytest.mark.parametrize(
+    ("args", "path"),
+    [
+        ({"project": S}, "project"),
+        ({"project": "p", "client": S}, "client"),
+        ({"items": [{"title": "t", "project_ids": ["p", S]}]}, "items[0].project_ids[1]"),
+        ({"items": [{"updates": [{"old_span": "a", "replacement": S}]}]}, "items[0].updates[0].replacement"),
+        ({"items": [{"updates": [{"old_span": S}]}]}, "items[0].updates[0].old_span"),
+        ({"items": [{}, {"source": {"path": S}}]}, "items[1].source.path"),
+        ({"items": [{"describes": ["a.py", S]}]}, "items[0].describes[1]"),
+        ({"items": [{"links": [{"rel": "relates_to", "target": S}]}]}, "items[0].links[0].target"),
+        ({"notes": "n", "decisions": ["ok", S]}, "decisions[1]"),
+        ({"card_update": {"body": S}}, "card_update.body"),
+        ({"context": S}, "context"),
+        ({"items": [{S: "x"}]}, "items[0].<key>"),
+        ({"items": [{"body": [S]}]}, "items[0].body[0]"),
+    ],
+)
+def test_find_secret_names_the_path_and_never_the_value(args: dict[str, Any], path: str) -> None:
+    hit = find_secret(args)
+    assert hit == (path, "github-token") and S not in hit[0]
+
+
+def _write_refusal(args: dict[str, Any]) -> ToolError:
+    import asyncio
+
+    from hlmemo.core import write_service
+
+    with pytest.raises(ToolError) as exc:  # refused before any database use (conn/ctx are None)
+        asyncio.run(write_service.write(None, None, args))  # type: ignore[arg-type]
+    return exc.value
+
+
+def _no_trace(err: ToolError) -> None:
+    import json
+
+    assert S not in json.dumps(err.as_error()) and S not in err.message
+    assert err.__cause__ is None and err.__context__ is None
+
+
+def test_astra_extra_key_shaped_like_a_secret_is_refused_without_echo() -> None:
+    args = _write()
+    args["items"][0][S] = "x"
+    err = _write_refusal(args)
+    assert err.details == {
+        "field": "items[0].<key>",
+        "reason": "secret_pattern",
+        "rule": "github-token",
+        "index": 0,
+    }
+    _no_trace(err)
+
+
+def test_astra_secret_in_a_wrongly_typed_field_never_reaches_the_validation_error() -> None:
+    err = _write_refusal(_write(body=[S]))
+    assert err.details["field"] == "items[0].body[0]" and err.details["reason"] == "secret_pattern"
+    _no_trace(err)
+
+
+def test_parse_request_drops_the_chained_validation_error_and_echoes_no_value() -> None:
+    import json
+
+    value = "plain-input-value-7f3e"
+    err = _refused(WriteRequest, _write(body=[value]))
+    assert value not in json.dumps(err.as_error()) and err.__cause__ is None and err.__context__ is None
+    err = _refused(WriteRequest, _write(device_scope="device:0" + "7"))
+    assert "device:07" not in json.dumps(err.as_error())
+
+
+def test_sol_secret_shaped_project_is_refused_before_authorization() -> None:
+    args = _write()
+    args["project"] = S
+    err = _write_refusal(args)
+    assert err.code == "E_INVALID_ARG" and err.details["field"] == "project"
+    _no_trace(err)
+
+
+@pytest.mark.parametrize("tool", ["memory_write", "memory_call_the_day", "memory_register_lesson"])
+async def test_mcp_tools_refuse_a_secret_shaped_project_without_echo(tool: str) -> None:
+    from hlmemo.server.tools import handlers, risk
+
+    fn = getattr(handlers, tool, None) or getattr(risk, tool)
+    with pytest.raises(ToolError) as exc:
+        await fn(None, None, {"project": S, "request_id": str(uuid.uuid4()), "mistake": "m", "fix": "f"})
+    assert exc.value.details["reason"] == "secret_pattern" and exc.value.details["field"] == "project"
+    _no_trace(exc.value)
+
+
+@pytest.mark.parametrize("tags", [["Active", "resolved"], [" active ", "HISTORICAL"], ["ACTIVE", "Resolved"]])
+def test_astra_status_tags_compare_case_insensitively(tags: list[str]) -> None:
+    err = _refused(WriteRequest, _write(kind="lesson", tags=tags))
+    assert err.details["reason"] == "lesson_status_conflict"
+
+
+@pytest.mark.parametrize("tags", [["Resolved", "HISTORICAL"], ["resolved", "historical"], ["Active"]])
+def test_concluded_or_active_alone_passes_in_any_case(tags: list[str]) -> None:
+    parse_request(WriteRequest, _write(kind="lesson", tags=tags))
+
+
+@pytest.mark.parametrize("field", ["title", "body"])
+def test_sol_empty_item_text_carries_reason_blank(field: str) -> None:
+    err = _refused(WriteRequest, _write(**{field: ""}))
+    assert err.details["reason"] == "blank" and err.details["index"] == 0
+
+
+@pytest.mark.parametrize("value", ["\u00a0", "", " \n "])
+@pytest.mark.parametrize("part", ["mistake", "fix", "context"])
+def test_sol_register_lesson_blank_parts_carry_reason_blank(part: str, value: str) -> None:
+    from hlmemo.core.lesson_service import LessonRequest
+
+    args = {"project": "p-one", "request_id": str(uuid.uuid4()), "mistake": "m", "fix": "f", part: value}
+    err = _refused(LessonRequest, args)
+    assert err.code == "E_INVALID_ARG" and err.details["reason"] == "blank"
+
+
+def test_empty_close_notes_and_lesson_title_carry_reason_blank() -> None:
+    for raw in (
+        _close(notes=""),
+        _close(lessons=[{"title": "", "body": "b"}]),
+        _close(card_update={"body": ""}),
+    ):
+        assert _refused(CloseRequest, raw).details["reason"] == "blank", raw
