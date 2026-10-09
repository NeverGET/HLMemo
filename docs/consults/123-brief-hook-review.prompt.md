Review a client-side change in HLMemo (the Claude Code SessionStart brief hook and the shared MCP client). Working dir = a clean export of commit 582a9fc, read-only. No server code changes.
Problem it fixes: on slow networks the hook injected "unavailable (the memory read failed)": an outer asyncio wait_for cancelled the fetch, MemoryClient.session() classified the CancelledError as ToolCallError E_UNAVAILABLE, so a timeout looked like a server failure (local log: 3 of 8 fetches failed, each at exactly the 6.5 s budget). Changes: CancelledError re-raised unchanged in mcp_client.session() and call_async(); ToolCallError gains a transport field; one deadline (asyncio.timeout_at) for the whole fetch; a status with a cause (timeout / error:auth / error:network / error:server); a partial brief (card + verified items read before the deadline) on timeout; the card raw read runs alongside the other raw reads; one retry for a fast retryable network/server error while >= 3 s remain.
Reply in <= 25 lines: verdict (GO / GO-WITH-FIXES / NO-GO), then findings HIGH/MEDIUM/LOW with file:line and a concrete trigger. HIGH = a caller of MemoryClient (importers, migrate runner, preflight, hlm review, the hook) now hangs, leaks a session/connection, swallows a real error, or exits differently in a way that loses data or hides a failure; the hook exceeding its 8 s wall budget or the 10 s Claude Code kill; a partial brief showing an item the full brief would not show (unverified, superseded, other project); a secret or token reaching stdout or the log. MEDIUM = wrong cause label, a retry that can double a side effect, log/dry-run fields wrong, tests that cannot fail.

## Diff
diff --git a/src/hlmemo/brief/config.py b/src/hlmemo/brief/config.py
index 4cf1097..62a7613 100644
--- a/src/hlmemo/brief/config.py
+++ b/src/hlmemo/brief/config.py
@@ -18,7 +18,8 @@ WALL_ENV = "HLM_BRIEF_WALL_S"  # test/ops override of the hard wall clock
 
 # 2026-10-06 (D-252): 3.2 s was at the edge of measured fetch times (2.3-3.2 s; slower under librarian load)
 WALL_S = 8.0  # hard budget for the whole hook process (the registered hook timeout is 10)
-FETCH_S = 6.5  # the memory calls must finish by then; the rest is assembly + output
+FETCH_S = 6.5  # ONE deadline for the whole fetch (session open, every read, close); then assembly + output
+RETRY_MIN_S = 3.0  # a fast retryable failure is retried once, only while this much of FETCH_S is left
 BRIEF_TOKENS = 1500
 SOURCES = ("startup", "clear", "compact")
 
diff --git a/src/hlmemo/brief/fetch.py b/src/hlmemo/brief/fetch.py
index f7cd9a9..d10e5f9 100644
--- a/src/hlmemo/brief/fetch.py
+++ b/src/hlmemo/brief/fetch.py
@@ -97,6 +97,11 @@ class Snapshot:
     card_date: datetime | None = None  # recorded_at of the card version (None: unknown)
     as_of: datetime | None = None  # newest recorded_at among the items read
     now: datetime = field(default_factory=lambda: datetime.now(UTC))
+    #: how far the reads got (``gather_snapshot``): session (opening it) -> queries -> details
+    #: (memory.raw of the card and the candidates) -> done
+    stage: str = "session"
+    #: the session-note and lesson candidates (``details`` on): ``settle`` keeps the verified ones
+    pools: tuple[list[Item], list[Item]] = field(default_factory=lambda: ([], []))
 
 
 def parse_ts(v: Any) -> datetime | None:
@@ -307,10 +312,16 @@ async def _card_date(call: Call, project: str, card: dict[str, Any] | None) -> d
     return parse_ts(r.get("recorded_at"))
 
 
-async def gather_snapshot(call: Call, project: str, *, now: datetime | None = None) -> Snapshot:
-    """Everything the brief shows, read-only. Raises only if NOTHING could be read."""
-    now = now or datetime.now(UTC)
-    snap = Snapshot(project=project, now=now)
+async def gather_snapshot(
+    call: Call, project: str, *, now: datetime | None = None, into: Snapshot | None = None
+) -> Snapshot:
+    """Everything the brief shows, read-only. Raises only if NOTHING could be read.
+
+    ``into`` is filled as the reads come back (``stage``, the card, the candidate pools, each verified
+    item), so a caller whose deadline cancels this can still ``settle`` what was read in time."""
+    snap = into if into is not None else Snapshot(project=project)
+    snap.now = now = now or datetime.now(UTC)
+    snap.stage = "queries"
 
     def q(query: str, kind: str) -> Awaitable[dict[str, Any]]:
         return call(
@@ -333,21 +344,36 @@ async def gather_snapshot(call: Call, project: str, *, now: datetime | None = No
             n = lib.get("pending_questions")
             snap.pending = n if isinstance(n, int) and n > 0 else 0
             snap.notices = [x for x in (lib.get("notices") or []) if isinstance(x, dict)]
-    snap.card_date = await _card_date(call, project, snap.card)
     lesson_res = results[0] if isinstance(results[0], dict) else {}
     sess_hits = [h for r in results[1:] if isinstance(r, dict) for h in (r.get("hits") or [])]
     s_pool = candidates(sess_hits, POOL_SESSIONS)
     l_pool = candidates(lesson_res.get("hits") or [], POOL_LESSONS)
+    snap.pools = (s_pool, l_pool)
+    snap.stage = "details"
+
+    async def card_date() -> None:  # in parallel with the candidates' reads, not one round trip before
+        snap.card_date = await _card_date(call, project, snap.card)
 
     sem = asyncio.Semaphore(CONCURRENCY)
     jobs = [(it, RAW_BUDGET_SESSION) for it in s_pool] + [(it, RAW_BUDGET_LESSON) for it in l_pool]
-    outcomes = await asyncio.gather(
-        *[_bounded(sem, read_raw(call, project, it, b, now)) for it, b in jobs], return_exceptions=True
+    _, *outcomes = await asyncio.gather(
+        card_date(),
+        *[_bounded(sem, read_raw(call, project, it, b, now)) for it, b in jobs],
+        return_exceptions=True,
     )
     for (it, _b), res in zip(jobs, outcomes, strict=True):
         if isinstance(res, BaseException):
             it.verified = False
+    settle(snap)
+    snap.stage = "done"
+    return snap
+
 
+def settle(snap: Snapshot) -> Snapshot:
+    """The shown sessions and lessons, the exclusions and ``as_of`` from the candidate pools. A candidate
+    whose memory.raw did not (completely) come back, e.g. one a deadline cut, is ``unverified`` and never
+    shown. (On an older server a cut pool also knows fewer superseders: the fallback gap above.)"""
+    s_pool, l_pool = snap.pools
     dead = superseded_pool_ids(s_pool + l_pool)
 
     def keep(pool: list[Item]) -> list[Item]:
@@ -369,6 +395,7 @@ async def gather_snapshot(call: Call, project: str, *, now: datetime | None = No
                 out.append(it)
         return out
 
+    snap.excluded = []
     snap.sessions, snap.lessons = keep(s_pool), keep(l_pool)
     stamps = [i.recorded_at for i in snap.sessions + snap.lessons if i.recorded_at is not None]
     snap.as_of = max(stamps) if stamps else None
diff --git a/src/hlmemo/brief/hook.py b/src/hlmemo/brief/hook.py
index b9718a4..c89636e 100644
--- a/src/hlmemo/brief/hook.py
+++ b/src/hlmemo/brief/hook.py
@@ -9,8 +9,12 @@ prints ``{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalCon
    ``docs/protocol/HLMEMO-PROTOCOL.md`` section 6) with ``<slug>`` filled in, unless
    ``HLM_BRIEF_DIGEST=off``; then
 2. the memory brief (read-only memory calls), or, when the digest is on and no brief can be produced
-   (timeout, error, nothing to show), ONE line saying the brief is unavailable. With the digest off a
-   missing brief prints nothing, as before.
+   (timeout, error, nothing to show), ONE line saying the brief is unavailable and why. With the digest
+   off a missing brief prints nothing, as before.
+
+The memory calls run under ONE deadline (``FETCH_S``: session open, every read, close). When it hits
+after the card and the queries came back, the brief shows what was verified in time plus a one-line
+note. A fast retryable network/server failure is retried once while ``RETRY_MIN_S`` remain.
 
 Global install (``~/.claude/settings.json``): until the cwd is known to be mapped the hook imports only
 stdlib-light modules, opens no socket and prints nothing. ``-P`` keeps the session cwd off ``sys.path``,
@@ -49,18 +53,26 @@ SLUG_MARK = "<slug>"
 #: file and shows the model only a 2,000-character preview). Digest + brief stay below, with a margin.
 CONTEXT_CHARS = 9500
 Fetcher = Callable[[str, BC.BriefConfig], Awaitable[Any]]
+AUTH_CODES = frozenset({"E_AUTH", "E_DEVICE_PENDING"})
+#: the last line of a brief the deadline cut short (the card and the queries came back, not every detail)
+PARTIAL_NOTE = "(cut short by the {s:g} s time limit: items not verified in time are left out)"
 
 
 @dataclass
 class Outcome:
     #: killed | unmapped | ignored | dryrun, or the brief's own status: injected | empty | timeout |
-    #: error:<Type> (with the digest on, empty/timeout/error still carry an output)
+    #: error:auth | error:network | error:server | error:<Type> (with the digest on, empty/timeout/error
+    #: still carry an output; a timeout after the queries may carry a partial brief)
     status: str
     output: str | None = None  # the exact stdout (a JSON line) when something is to be injected
     tokens: int = 0  # the brief's tokens (0: no brief)
     sections: tuple[str, ...] = ()  # "Digest" first when the digest is part of the output
     digest: bool = False
     chars: int = 0  # len(additionalContext)
+    slug: str = ""  # the mapped project
+    #: why: the stage a timeout cut (session | queries | details), the error code (auth/server) or the
+    #: transport error's type (network)
+    cause: str = ""
 
 
 def hook_output(text: str) -> str:
@@ -90,10 +102,23 @@ def _digest_or_empty(slug: str, env: Mapping[str, str]) -> str:
         return ""
 
 
-def unavailable(slug: str, status: str) -> str:
-    """The one line that stands in for a brief that could not be produced."""
+def _token(s: str) -> str:
+    """A code or type name, safe for one log field or one line of context."""
+    return re.sub(r"[^A-Za-z0-9._:-]", "", s)[:64]
+
+
+def unavailable(slug: str, status: str, cause: str = "") -> str:
+    """The one line that stands in for a brief that could not be produced, with the reason."""
     if status == "timeout":
-        why = "the memory read timed out"
+        why = f"timed out after {BC.FETCH_S:g} s; memory.query still works"
+    elif status == "error:auth" and cause == "E_DEVICE_PENDING":
+        why = "this device is not approved yet"
+    elif status == "error:auth":
+        why = "the server refused this device's token"
+    elif status == "error:network":
+        why = "server unreachable"
+    elif status == "error:server":
+        why = f"server error {_token(cause)}".rstrip()
     elif status.startswith("error"):
         why = "the memory read failed"
     else:
@@ -104,6 +129,34 @@ def unavailable(slug: str, status: str) -> str:
     )
 
 
+def failure(exc: BaseException) -> tuple[str, str]:
+    """``(status, cause)`` of a failed fetch: ``timeout``; ``error:auth`` and ``error:server`` with the
+    code; ``error:network`` (no answer from the server) with the transport error's type; anything else
+    ``error:<Type>``. Never raises."""
+    if isinstance(exc, TimeoutError):
+        return "timeout", ""
+    if isinstance(exc, ConnectionError):
+        return "error:network", type(exc).__name__
+    try:
+        from hlmemo.cli.mcp_client import ToolCallError
+    except Exception:  # noqa: BLE001 - the client cannot load, so it raised nothing
+        return f"error:{type(exc).__name__}", ""
+    if not isinstance(exc, ToolCallError):
+        return f"error:{type(exc).__name__}", ""
+    if exc.code in AUTH_CODES:
+        return "error:auth", exc.code
+    if exc.transport == "TimeoutError":
+        return "timeout", ""
+    if exc.transport:
+        return "error:network", exc.transport
+    return "error:server", exc.code
+
+
+def _retryable(status: str, exc: BaseException) -> bool:
+    """Network or server failures the client/server flagged retryable (never auth, never a timeout)."""
+    return status in ("error:network", "error:server") and getattr(exc, "retryable", False) is True
+
+
 def compose(head: str, body: str) -> str:
     return f"{head}\n\n{body}" if head and body else head or body
 
@@ -112,30 +165,66 @@ def fit(text: str, limit: int) -> str:
     return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "…"
 
 
-async def default_fetcher(slug: str, cfg: BC.BriefConfig) -> Any:
-    import asyncio
-
+async def default_fetcher(slug: str, cfg: BC.BriefConfig, partial: Any = None) -> Any:
+    """The snapshot, read in one MCP session (no timer here: ``fetch_snapshot`` owns the deadline).
+    ``partial`` (a ``Snapshot``) is filled as the reads come back, for a deadline that cuts them."""
     from hlmemo.brief import fetch as F
 
     cm = F.open_call_factory(cfg.capture, timeout_s=BC.FETCH_S)
     if cm is None:  # no device token: the relay fallback runs an LLM, which this hook never does
         return None
     async with cm as call:
-        return await asyncio.wait_for(F.gather_snapshot(call, slug), timeout=BC.FETCH_S)
+        return await F.gather_snapshot(call, slug, into=partial)
 
 
-def build_brief(slug: str, cfg: BC.BriefConfig, fetcher: Fetcher | None, room: int) -> tuple[str, Any]:
-    """``(status, Brief | None)``; status = injected | empty | timeout | error:<Type>. The brief keeps
-    its token budget AND fits in ``room`` characters (what the digest leaves). Never raises."""
+async def fetch_snapshot(slug: str, cfg: BC.BriefConfig, fetcher: Fetcher | None) -> tuple[str, str, Any]:
+    """``(status, cause, Snapshot | None)``, status = ok | empty | timeout | error:... (``failure``),
+    under ONE deadline ``BC.FETCH_S`` from now. A retryable network/server failure is retried once while
+    ``BC.RETRY_MIN_S`` remain. When the deadline cuts the default fetcher after the card and the queries,
+    the snapshot of what was verified by then comes back with status ``timeout`` (cause: the stage)."""
+    import asyncio
+
+    from hlmemo.brief import fetch as F
+
+    loop = asyncio.get_running_loop()
+    deadline = loop.time() + BC.FETCH_S
+    partial: Any = None
+    try:
+        async with asyncio.timeout_at(deadline):
+            retried = False
+            while True:
+                partial = F.Snapshot(project=slug) if fetcher is None else None
+                try:
+                    snap = await (fetcher(slug, cfg) if fetcher else default_fetcher(slug, cfg, partial))
+                    return ("ok", "", snap) if snap is not None else ("empty", "", None)
+                except Exception as exc:
+                    if loop.time() >= deadline:  # the deadline's cancellation, turned into an error
+                        raise TimeoutError from exc
+                    status, cause = failure(exc)
+                    if retried or not _retryable(status, exc) or deadline - loop.time() < BC.RETRY_MIN_S:
+                        return status, cause, None
+                    retried = True
+    except TimeoutError:
+        stage = partial.stage if partial is not None else ""
+        return "timeout", stage, F.settle(partial) if stage == "details" else None
+
+
+def build_brief(slug: str, cfg: BC.BriefConfig, fetcher: Fetcher | None, room: int) -> tuple[str, str, Any]:
+    """``(status, cause, Brief | None)``; status = injected | empty | timeout | error:... (``failure``).
+    A timeout that cut only the details still gives a brief of what was verified, ending in
+    ``PARTIAL_NOTE``. The brief keeps its token budget AND fits in ``room`` characters (what the digest
+    leaves). Never raises."""
     try:
         import asyncio
 
         from hlmemo.brief.assemble import assemble, count_tokens
 
-        snap = asyncio.run(asyncio.wait_for((fetcher or default_fetcher)(slug, cfg), timeout=BC.FETCH_S))
+        status, cause, snap = asyncio.run(fetch_snapshot(slug, cfg, fetcher))
         if snap is None:
-            return "empty", None
-        budget = cfg.budget_tokens
+            return status, cause, None
+        note = PARTIAL_NOTE.format(s=BC.FETCH_S) if status == "timeout" else ""
+        budget = cfg.budget_tokens - (count_tokens(note) + 1 if note else 0)
+        room -= (len(note) + 1) if note else 0
 
         def counter(text: str) -> int:  # over the room = over budget: assemble drops lines until it fits
             n = count_tokens(text)
@@ -151,15 +240,15 @@ def build_brief(slug: str, cfg: BC.BriefConfig, fetcher: Fetcher | None, room: i
             counter=counter,
         )
         if brief is None:
-            return "empty", None
+            return ("timeout" if note else "empty"), cause, None
         if len(brief.text) > room:  # last resort; the drop loop normally prevents it
             brief.text, brief.truncated = fit(brief.text, room), True
+        if note:
+            brief.text += "\n" + note
         brief.tokens = count_tokens(brief.text)
-        return "injected", brief
-    except TimeoutError:
-        return "timeout", None
+        return ("timeout" if note else "injected"), cause, brief
     except BaseException as exc:  # noqa: BLE001 - fail open, always
-        return f"error:{type(exc).__name__}", None
+        return (*failure(exc), None)
 
 
 def _safe(s: str) -> str:
@@ -210,16 +299,17 @@ def handle(
         if head and arm is not None and not dry:
             arm(hook_output(compose(head, unavailable(slug, "timeout"))))
         room = CONTEXT_CHARS - (len(head) + 2 if head else 0)
-        status, brief = build_brief(slug, cfg, fetcher, room)
-        body = brief.text if brief is not None else (unavailable(slug, status) if head else "")
+        status, cause, brief = build_brief(slug, cfg, fetcher, room)
+        body = brief.text if brief is not None else (unavailable(slug, status, cause) if head else "")
         text = compose(head, body)
         if not text:
-            return Outcome(status)
+            return Outcome(status, slug=slug, cause=cause)
         tokens = brief.tokens if brief is not None else 0
         sections = (("Digest",) if head else ()) + (tuple(brief.sections) if brief is not None else ())
         if dry:
             meta = {
                 "status": status,
+                "cause": cause,
                 "digest": bool(head),
                 "tokens": tokens,
                 "sections": list(sections),
@@ -228,8 +318,8 @@ def handle(
                 "chars": len(text),
             }
             write_dryrun(dry, data, text, meta)
-            return Outcome("dryrun", None, tokens, sections, bool(head), len(text))
-        return Outcome(status, hook_output(text), tokens, sections, bool(head), len(text))
+            return Outcome("dryrun", None, tokens, sections, bool(head), len(text), slug, cause)
+        return Outcome(status, hook_output(text), tokens, sections, bool(head), len(text), slug, cause)
     except BaseException as exc:  # noqa: BLE001 - fail open, always
         return Outcome(f"error:{type(exc).__name__}")
 
@@ -242,8 +332,8 @@ def log_line(o: Outcome, ms: int) -> None:
         stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
         with (d / "brief.log").open("a", encoding="utf-8") as fh:
             fh.write(
-                f"{stamp} {o.status} {ms}ms tokens={o.tokens} chars={o.chars} digest={int(o.digest)} "
-                f"sections={','.join(o.sections)}\n"
+                f"{stamp} {o.status} {ms}ms slug={_token(o.slug) or '-'} cause={_token(o.cause) or '-'} "
+                f"tokens={o.tokens} chars={o.chars} digest={int(o.digest)} sections={','.join(o.sections)}\n"
             )
     except BaseException:  # noqa: BLE001
         pass
diff --git a/src/hlmemo/cli/mcp_client.py b/src/hlmemo/cli/mcp_client.py
index 80810c8..2d54aba 100644
--- a/src/hlmemo/cli/mcp_client.py
+++ b/src/hlmemo/cli/mcp_client.py
@@ -3,7 +3,9 @@
 Wire format (D-024 (6)): every tool result is one `TextContent` carrying canonical JSON; errors are
 `isError:true` results whose text is `{code, message, retryable, details}`. Both are decoded here into a
 dict or a `ToolCallError`. Transport failures (timeouts, connection refused, HTTP 401/403 from the status
-gate) become `ToolCallError` with `E_UNAVAILABLE` / `E_AUTH` / `E_DEVICE_PENDING`.
+gate) become `ToolCallError` with `E_UNAVAILABLE` / `E_AUTH` / `E_DEVICE_PENDING`. A cancellation
+(``asyncio.CancelledError``, e.g. from a caller's ``asyncio.timeout``) is never classified: it propagates
+unchanged, so the caller's timeout still sees its own cancellation and reports a timeout.
 
 `MemoryClient(url, token)` talks to a server; `MemoryClient.in_memory(server)` binds an `MCPServer`
 instance in-process (unit tests), no network.
@@ -47,13 +49,22 @@ def _skip_unlisted_validation(client: Client) -> None:
 
 class ToolCallError(Exception):
     def __init__(
-        self, code: str, message: str, *, retryable: bool = False, details: dict[str, Any] | None = None
+        self,
+        code: str,
+        message: str,
+        *,
+        retryable: bool = False,
+        details: dict[str, Any] | None = None,
+        transport: str | None = None,
     ) -> None:
         super().__init__(f"{code}: {message}")
         self.code = code
         self.message = message
         self.retryable = retryable
         self.details = details or {}
+        #: no answer came from the server (a timeout, a refused connection, TLS, a proxy error): the
+        #: transport failure's exception type. None when the server (or its auth gate) sent the error.
+        self.transport = transport
 
     def to_dict(self) -> dict[str, Any]:
         return {
@@ -131,9 +142,16 @@ def _classify(exc: BaseException) -> ToolCallError:
         if status == 403:
             return ToolCallError("E_DEVICE_PENDING", "device not trusted yet (403)", retryable=True)
     if any(isinstance(leaf, TimeoutError | asyncio.TimeoutError) for leaf in leaves):
-        return ToolCallError("E_UNAVAILABLE", "timeout waiting for the memory server", retryable=True)
+        return ToolCallError(
+            "E_UNAVAILABLE", "timeout waiting for the memory server", retryable=True, transport="TimeoutError"
+        )
     text = "; ".join(f"{type(leaf).__name__}: {leaf}" for leaf in leaves)[:400]
-    return ToolCallError("E_UNAVAILABLE", f"memory server unreachable: {text}", retryable=True)
+    return ToolCallError(
+        "E_UNAVAILABLE",
+        f"memory server unreachable: {text}",
+        retryable=True,
+        transport=type(leaves[0]).__name__,
+    )
 
 
 def decode_result(result: Any) -> dict[str, Any]:
@@ -197,7 +215,7 @@ class MemoryClient:
         except ToolCallError:
             raise
         except BaseException as exc:  # noqa: BLE001 - anyio groups, httpx2 errors, TimeoutError
-            if isinstance(exc, KeyboardInterrupt):
+            if isinstance(exc, KeyboardInterrupt | asyncio.CancelledError):
                 raise
             raise _classify(exc) from exc
         return decode_result(result)
@@ -210,6 +228,7 @@ class MemoryClient:
         """One MCP session for many calls (``hlm import``/``hlm export``); yields ``call(tool, args)``.
 
         Tool errors raise ``ToolCallError``; transport failures are classified like ``call_async``.
+        A cancellation (the caller's timeout) propagates unchanged from a call and from the session.
         The unlisted client tool ``hlm.export`` is not output-validated (``UNLISTED_TOOLS``).
         """
         try:
@@ -222,7 +241,7 @@ class MemoryClient:
                     except ToolCallError:
                         raise
                     except BaseException as exc:  # noqa: BLE001 - classified below
-                        if isinstance(exc, KeyboardInterrupt):
+                        if isinstance(exc, KeyboardInterrupt | asyncio.CancelledError):
                             raise
                         raise _classify(exc) from exc
                     return decode_result(result)
@@ -231,7 +250,7 @@ class MemoryClient:
         except ToolCallError:
             raise
         except BaseException as exc:  # noqa: BLE001 - session setup / teardown failures
-            if isinstance(exc, KeyboardInterrupt | GeneratorExit):
+            if isinstance(exc, KeyboardInterrupt | GeneratorExit | asyncio.CancelledError):
                 raise
             raise _classify(exc) from exc
 
diff --git a/tests/unit/test_brief_digest.py b/tests/unit/test_brief_digest.py
index 879b5b2..017e434 100644
--- a/tests/unit/test_brief_digest.py
+++ b/tests/unit/test_brief_digest.py
@@ -137,9 +137,10 @@ def test_mapped_with_failed_fetch_gets_digest_and_unavailable_line(
     text = ctx(o)
     head, _, tail = text.rpartition("\n\n")
     assert head == H.digest("proj")
-    assert tail == H.unavailable("proj", o.status) and "\n" not in tail
+    assert tail == H.unavailable("proj", o.status, o.cause) and "\n" not in tail
     assert tail.startswith("# Memory brief: project proj: unavailable this session (")
-    assert o.status == ("error:ConnectionError" if status_fetcher == "error" else "timeout")
+    assert o.status == ("error:network" if status_fetcher == "error" else "timeout")
+    assert ("(server unreachable)" if status_fetcher == "error" else "(timed out after 0.2 s;") in tail
 
 
 def test_unmapped_prints_nothing(tmp_path: Path, cfg_file: Path) -> None:
@@ -175,7 +176,7 @@ def test_hlm_brief_digest_off_keeps_the_brief_only(tmp_path: Path, cfg_file: Pat
 
 def test_hlm_brief_digest_off_with_failed_fetch_prints_nothing(tmp_path: Path, cfg_file: Path) -> None:
     o = go(payload(tmp_path), cfg_file, fetcher=boom, env={"HLM_BRIEF_DIGEST": "off"})
-    assert o.output is None and o.status == "error:ConnectionError"
+    assert o.output is None and (o.status, o.cause) == ("error:network", "ConnectionError")
 
 
 def test_other_digest_values_keep_the_digest(tmp_path: Path, cfg_file: Path) -> None:
@@ -253,7 +254,7 @@ def test_watchdog_prints_digest_when_the_fetch_hangs(tmp_path: Path, cfg_file: P
     code = (
         "import sys, time\n"
         "from hlmemo.brief import hook as H\n"
-        "async def hung(slug, cfg):\n"
+        "async def hung(slug, cfg, partial=None):\n"
         "    time.sleep(30)\n"
         "H.default_fetcher = hung\n"
         "sys.exit(H.main())\n"
diff --git a/tests/unit/test_brief_fetch.py b/tests/unit/test_brief_fetch.py
index de1bd8e..badc7e1 100644
--- a/tests/unit/test_brief_fetch.py
+++ b/tests/unit/test_brief_fetch.py
@@ -342,6 +342,48 @@ def test_card_date_unknown_when_raw_fails_or_no_card() -> None:
     assert run(s2).card_date is None
 
 
+def test_card_date_is_read_alongside_the_candidates() -> None:
+    """The card's memory.raw shares the candidates' round trip (it used to cost one of its own first)."""
+    s = server_with_notes()
+    s.add(1, "project_card", "Project card", "# Proj", 27)
+    events: list[str] = []
+
+    async def call(tool: str, args: dict) -> dict:
+        if tool == "memory.raw" and args["version_id"] == 1:
+            await asyncio.sleep(0.05)
+            events.append("card")
+        elif tool == "memory.raw":
+            events.append(f"v{args['version_id']}")
+        return await s(tool, args)
+
+    snap = asyncio.run(F.gather_snapshot(call, "proj", now=NOW))
+    assert events[-1] == "card" and len(events) == 5  # every candidate read started before the card's ended
+    assert snap.card_date == datetime(2026, 9, 27, 11, 0, tzinfo=UTC) and snap.stage == "done"
+
+
+def test_into_keeps_what_was_read_when_the_fetch_is_cut() -> None:
+    """A deadline that cancels the candidates' reads: ``into`` holds the card, the stage and the items
+    verified in time; ``settle`` shows those and marks the cut ones unverified."""
+    s = server_with_notes()
+
+    async def call(tool: str, args: dict) -> dict:
+        if tool == "memory.raw" and args["version_id"] == 20:
+            await asyncio.sleep(30)
+        return await s(tool, args)
+
+    async def cut(snap: F.Snapshot) -> None:
+        with pytest.raises(TimeoutError):
+            async with asyncio.timeout(0.2):
+                await F.gather_snapshot(call, "proj", now=NOW, into=snap)
+
+    snap = F.Snapshot(project="proj")
+    asyncio.run(cut(snap))
+    assert snap.stage == "details" and snap.card == CARD
+    F.settle(snap)
+    assert [i.version_id for i in snap.lessons] == [21] and [i.version_id for i in snap.sessions] == [11, 10]
+    assert ("v20", "unverified") in snap.excluded
+
+
 # --------------------------------------------------------------------------- review 96 Sol #5
 def chunk(a: int, text: str, ordinal: int = 0) -> dict:
     return {"ordinal": ordinal, "char_start": a, "char_end": a + len(text), "text": text}
diff --git a/tests/unit/test_brief_hook.py b/tests/unit/test_brief_hook.py
index d69c37a..035ee46 100644
--- a/tests/unit/test_brief_hook.py
+++ b/tests/unit/test_brief_hook.py
@@ -11,6 +11,8 @@ import io
 import json
 import subprocess
 import sys
+import time
+from contextlib import asynccontextmanager
 from datetime import UTC, datetime
 from pathlib import Path
 from typing import Any
@@ -20,6 +22,7 @@ import pytest
 from hlmemo.brief import config as BC
 from hlmemo.brief import hook as H
 from hlmemo.brief.fetch import Item, Snapshot
+from hlmemo.cli.mcp_client import MemoryClient, ToolCallError, _classify
 
 PROJ = "/w/proj"
 
@@ -34,7 +37,7 @@ def good_snapshot() -> Snapshot:
     )
 
 
-async def ok_fetcher(slug: str, cfg: Any) -> Snapshot:
+async def ok_fetcher(slug: str, cfg: Any, partial: Any = None) -> Snapshot:
     assert slug == "proj"
     return good_snapshot()
 
@@ -197,10 +200,10 @@ def test_timeout_fails_open(
         return good_snapshot()
 
     o = go(payload(tmp_path), cfg_file, fetcher=slow, env={} if digest_on else DIGEST_OFF)
-    assert o.status == "timeout"
+    assert o.status == "timeout" and o.slug == "proj"
     assert_fail_open(o, digest_on)
     if digest_on:
-        assert "(the memory read timed out)" in ctx(o)
+        assert "(timed out after 0.2 s; memory.query still works)" in ctx(o)
 
 
 @pytest.mark.parametrize("digest_on", [True, False])
@@ -226,8 +229,10 @@ def test_server_unreachable_fails_open(
 
     monkeypatch.setattr(F, "open_call_factory", refuse)
     o = H.handle(payload(tmp_path), env={} if digest_on else DIGEST_OFF, config_path=cfg_file)
-    assert o.status.startswith("error:")
+    assert (o.status, o.cause) == ("error:network", "ConnectionError")
     assert_fail_open(o, digest_on)
+    if digest_on:
+        assert "(server unreachable)" in ctx(o)
 
 
 @pytest.mark.parametrize("digest_on", [True, False])
@@ -324,6 +329,7 @@ def test_main_prints_json_and_exits_zero(
     assert doc["hookSpecificOutput"]["hookEventName"] == "SessionStart"
     log = (tmp_path / "state" / "brief.log").read_text()
     assert " injected " in log and " digest=1 " in log and "sections=Digest,Now,Lessons" in log
+    assert " slug=proj cause=- " in log
 
 
 def test_main_unmapped_prints_nothing(
@@ -365,7 +371,7 @@ def test_watchdog_ends_a_hung_process_silently_with_exit_zero() -> None:
 
 def test_wall_clock_is_eight_seconds_and_inside_the_hook_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
     monkeypatch.delenv("HLM_BRIEF_WALL_S", raising=False)
-    assert BC.wall_seconds() == 8.0 and BC.FETCH_S < BC.wall_seconds() < 10.0
+    assert BC.wall_seconds() == 8.0 and BC.RETRY_MIN_S < BC.FETCH_S < BC.wall_seconds() < 10.0
     monkeypatch.setenv("HLM_BRIEF_WALL_S", "bogus")
     assert BC.wall_seconds() == 8.0
 
@@ -467,3 +473,231 @@ def test_history_and_body_settings_reach_the_brief(tmp_path: Path) -> None:
     )
     on = go(payload(tmp_path), p, fetcher=with_note).output or ""
     assert "Use the new parser." in on and "A lesson — The rule." in on
+
+
+# --------------------------------------------------------------------------- one deadline, real client
+def slow_server(delay: float) -> Any:
+    """An in-process MCP server whose every tool call takes ``delay`` seconds."""
+    from mcp import types
+    from mcp.server.lowlevel import Server
+
+    async def on_list_tools(ctx: Any, params: Any) -> types.ListToolsResult:
+        tool = types.Tool(name="memory.query", description="q", inputSchema={"type": "object"})
+        return types.ListToolsResult(tools=[tool])
+
+    async def on_call_tool(ctx: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
+        await asyncio.sleep(delay)
+        return types.CallToolResult(content=[types.TextContent(type="text", text="{}")], isError=False)
+
+    return Server("hlmemo-slow", on_list_tools=on_list_tools, on_call_tool=on_call_tool)
+
+
+def test_client_session_lets_the_deadline_cancellation_through() -> None:
+    """``MemoryClient.session()`` / ``call_async`` re-raise a cancellation unchanged, so the caller's
+    ``asyncio.timeout`` ends in TimeoutError (it was classified as ToolCallError E_UNAVAILABLE)."""
+    client = MemoryClient.in_memory(slow_server(30), timeout_s=30)
+
+    async def in_session() -> None:
+        async with asyncio.timeout(0.2):
+            async with client.session() as call:
+                await call("memory.query", {})
+
+    async def one_call() -> None:
+        async with asyncio.timeout(0.2):
+            await client.call_async("memory.query", {})
+
+    for run in (in_session, one_call):
+        with pytest.raises(TimeoutError):
+            asyncio.run(run())
+
+
+def test_a_slow_server_through_the_real_session_is_a_timeout(
+    tmp_path: Path, cfg_file: Path, monkeypatch: pytest.MonkeyPatch
+) -> None:
+    """The default fetcher over a real ``MemoryClient.session()``: a slow server is reported as a
+    timeout of the queries (it was logged as ``error:ToolCallError`` at exactly the fetch budget)."""
+    from hlmemo.brief import fetch as F
+
+    monkeypatch.setattr(BC, "FETCH_S", 0.5)
+    client = MemoryClient.in_memory(slow_server(30), timeout_s=30)
+    monkeypatch.setattr(F, "open_call_factory", lambda *a, **k: client.session())
+    t = time.monotonic()
+    o = H.handle(payload(tmp_path), env={}, config_path=cfg_file)
+    assert o.status == "timeout"  # was error:ToolCallError
+    assert o.cause == "queries" and "(timed out after 0.5 s; memory.query still works)" in ctx(o)
+    assert time.monotonic() - t < 3  # one deadline, and the session closes promptly after it
+
+
+# --------------------------------------------------------------------------- partial brief
+CARD = {"clue": "v1", "text": "# Proj\n\nReal card.", "stale": False}
+
+
+def lesson_hit(vid: int, title: str) -> dict[str, Any]:
+    return {"clue": f"v{vid}.0", "kind": "lesson", "title": title, "tags": [],
+            "valid_from": f"2026-09-{vid:02d}T10:00:00Z"}  # fmt: skip
+
+
+def raw(vid: int) -> dict[str, Any]:
+    return {"version_id": vid, "logical_id": vid + 1000, "kind": "lesson", "valid_to": None,
+            "superseded_at": None, "recorded_at": "2026-09-30T11:00:00Z", "superseded_by": [],
+            "payload_item": {"body": f"body {vid}"}, "links": [], "next_cursor": None}  # fmt: skip
+
+
+def patch_session(monkeypatch: pytest.MonkeyPatch, call: Any) -> None:
+    """``open_call_factory`` yields the fake ``call``: the real default fetcher and gather_snapshot run."""
+    from hlmemo.brief import fetch as F
+
+    @asynccontextmanager
+    async def session() -> Any:
+        yield call
+
+    monkeypatch.setattr(F, "open_call_factory", lambda *a, **k: session())
+
+
+def test_deadline_after_the_queries_injects_what_was_verified(
+    tmp_path: Path, cfg_file: Path, monkeypatch: pytest.MonkeyPatch
+) -> None:
+    """The card and the queries came back, one lesson was verified, the other's memory.raw hangs: the
+    card and the verified lesson are shown with the cut note; the unverified lesson never is."""
+    monkeypatch.setattr(BC, "FETCH_S", 0.5)
+
+    async def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
+        if tool == "memory.query":
+            lessons = [lesson_hit(21, "Fast lesson"), lesson_hit(20, "Slow lesson")]
+            return {"card": CARD, "hits": lessons if args["kinds"] == ["lesson"] else []}
+        if args["version_id"] == 20:
+            await asyncio.sleep(30)
+        return raw(args["version_id"])
+
+    patch_session(monkeypatch, call)
+    for env in ({}, DIGEST_OFF):
+        o = H.handle(payload(tmp_path), env=env, config_path=cfg_file)
+        text = ctx(o)
+        assert (o.status, o.cause) == ("timeout", "details") and o.tokens > 0
+        assert "## Now (project card v1, updated 2026-09-30" in text and "Real card." in text
+        assert "- [v21] Fast lesson" in text and "Slow lesson" not in text
+        assert text.endswith("\n" + H.PARTIAL_NOTE.format(s=0.5)) and "unavailable this session" not in text
+        assert o.sections[-2:] == ("Now", "Lessons") and len(text) <= H.CONTEXT_CHARS
+
+
+def test_deadline_before_the_queries_came_back_is_a_plain_timeout(
+    tmp_path: Path, cfg_file: Path, monkeypatch: pytest.MonkeyPatch
+) -> None:
+    monkeypatch.setattr(BC, "FETCH_S", 0.3)
+
+    async def call(tool: str, args: dict[str, Any]) -> dict[str, Any]:
+        await asyncio.sleep(30)
+        return {}
+
+    patch_session(monkeypatch, call)
+    o = H.handle(payload(tmp_path), env={}, config_path=cfg_file)
+    assert (o.status, o.cause) == ("timeout", "queries")
+    assert_fail_open(o, True)
+    assert ctx(o).endswith(H.unavailable("proj", "timeout"))
+
+
+# --------------------------------------------------------------------------- the cause
+@pytest.mark.parametrize(
+    ("exc", "status", "cause"),
+    [
+        (TimeoutError(), "timeout", ""),
+        (_classify(TimeoutError()), "timeout", ""),
+        (ToolCallError("E_AUTH", "rejected"), "error:auth", "E_AUTH"),
+        (_classify(RuntimeError("HTTP 401 Unauthorized")), "error:auth", "E_AUTH"),
+        (ToolCallError("E_DEVICE_PENDING", "not yet", retryable=True), "error:auth", "E_DEVICE_PENDING"),
+        (_classify(ConnectionRefusedError("refused")), "error:network", "ConnectionRefusedError"),
+        (ConnectionError("x"), "error:network", "ConnectionError"),
+        (ToolCallError("E_UNAVAILABLE", "database down", retryable=True), "error:server", "E_UNAVAILABLE"),
+        (ToolCallError("E_BUDGET_TOO_SMALL", "x"), "error:server", "E_BUDGET_TOO_SMALL"),
+        (RuntimeError("x"), "error:RuntimeError", ""),
+    ],
+)
+def test_failure_names_the_cause(exc: BaseException, status: str, cause: str) -> None:
+    assert H.failure(exc) == (status, cause)
+
+
+@pytest.mark.parametrize(
+    ("status", "cause", "why"),
+    [
+        ("timeout", "queries", "timed out after 6.5 s; memory.query still works"),
+        ("error:auth", "E_AUTH", "the server refused this device's token"),
+        ("error:auth", "E_DEVICE_PENDING", "this device is not approved yet"),
+        ("error:network", "ConnectError", "server unreachable"),
+        ("error:server", "E_UNAVAILABLE", "server error E_UNAVAILABLE"),
+        ("error:RuntimeError", "", "the memory read failed"),
+    ],
+)
+def test_unavailable_line_says_why(status: str, cause: str, why: str) -> None:
+    assert H.unavailable("proj", status, cause) == (
+        f"# Memory brief: project proj: unavailable this session ({why}). "
+        "Use memory.query (token_budget 3000) for your task."
+    )
+
+
+def test_cause_reaches_the_line_and_the_log(
+    tmp_path: Path, cfg_file: Path, monkeypatch: pytest.MonkeyPatch
+) -> None:
+    async def refused(slug: str, cfg: Any) -> Snapshot:
+        raise ToolCallError("E_AUTH", "server rejected the device token (401)")
+
+    o = go(payload(tmp_path), cfg_file, fetcher=refused)
+    assert (o.status, o.slug, o.cause) == ("error:auth", "proj", "E_AUTH")
+    assert "(the server refused this device's token)" in ctx(o)
+    monkeypatch.setenv("HLM_CAPTURE_STATE_DIR", str(tmp_path / "state"))
+    H.log_line(o, 42)
+    log = (tmp_path / "state" / "brief.log").read_text()
+    assert " error:auth 42ms slug=proj cause=E_AUTH tokens=0 " in log
+
+
+# --------------------------------------------------------------------------- one retry
+def flaky(errors: list[BaseException], calls: list[float], delay: float = 0.0) -> Any:
+    async def fetcher(slug: str, cfg: Any) -> Snapshot:
+        calls.append(time.monotonic())
+        await asyncio.sleep(delay)
+        if errors:
+            raise errors.pop(0)
+        return good_snapshot()
+
+    return fetcher
+
+
+def reset() -> ToolCallError:
+    return _classify(ConnectionResetError("reset by peer"))  # retryable, no answer from the server
+
+
+def test_a_fast_retryable_failure_is_retried_once(tmp_path: Path, cfg_file: Path) -> None:
+    calls: list[float] = []
+    assert go(payload(tmp_path), cfg_file, fetcher=flaky([reset()], calls)).status == "injected"
+    assert len(calls) == 2
+    calls.clear()
+    o = go(payload(tmp_path), cfg_file, fetcher=flaky([reset(), reset(), reset()], calls))
+    assert (o.status, o.cause, len(calls)) == ("error:network", "ConnectionResetError", 2)
+
+
+@pytest.mark.parametrize(
+    "exc",
+    [
+        ToolCallError("E_AUTH", "no"),
+        ToolCallError("E_DEVICE_PENDING", "wait", retryable=True),
+        ToolCallError("E_INVALID_ARG", "bad"),
+        RuntimeError("bug"),
+        ConnectionError("not a classified, retryable client error"),
+    ],
+)
+def test_auth_and_non_retryable_failures_are_not_retried(
+    tmp_path: Path, cfg_file: Path, exc: BaseException
+) -> None:
+    calls: list[float] = []
+    assert go(payload(tmp_path), cfg_file, fetcher=flaky([exc], calls)).status.startswith("error:")
+    assert len(calls) == 1
+
+
+def test_a_slow_failure_is_not_retried(
+    tmp_path: Path, cfg_file: Path, monkeypatch: pytest.MonkeyPatch
+) -> None:
+    """Less than RETRY_MIN_S left before the deadline: the failure is reported, not retried."""
+    monkeypatch.setattr(BC, "FETCH_S", 1.0)
+    monkeypatch.setattr(BC, "RETRY_MIN_S", 0.6)
+    calls: list[float] = []
+    o = go(payload(tmp_path), cfg_file, fetcher=flaky([reset()], calls, delay=0.5))
+    assert (o.status, len(calls)) == ("error:network", 1)
