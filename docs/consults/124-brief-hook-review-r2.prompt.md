Round 2 (final) of the review of the HLMemo brief hook change (consult 123: prompt, rubric and round-1 findings below). Working dir = a clean export of commit fea0f33, read-only.
Fixes: (HIGH) settle(snap, cut=True) on a deadline-cut snapshot marks every candidate without the server's own incoming status unverified, so the pool fallback can no longer show an item whose superseder's read was cut; (MEDIUM) _classify maps HTTP-client timeouts (any subclass of a class named TimeoutException, e.g. httpx ReadTimeout/ConnectTimeout) to transport TimeoutError → hook status timeout, never retried; (MEDIUM) an HTTP status >= 500 without a JSON error body → ToolCallError E_UNAVAILABLE without transport → error:server. Each has a unit test (tests/unit/test_brief_fetch.py, test_brief_hook.py).
Reply in <= 15 lines: verdict (GO / GO-WITH-FIXES / NO-GO) and any remaining HIGH/MEDIUM per the round-1 rubric with file:line and a concrete failing input.

Review a client-side change in HLMemo (the Claude Code SessionStart brief hook and the shared MCP client). Working dir = a clean export of commit 582a9fc, read-only. No server code changes.
Problem it fixes: on slow networks the hook injected "unavailable (the memory read failed)": an outer asyncio wait_for cancelled the fetch, MemoryClient.session() classified the CancelledError as ToolCallError E_UNAVAILABLE, so a timeout looked like a server failure (local log: 3 of 8 fetches failed, each at exactly the 6.5 s budget). Changes: CancelledError re-raised unchanged in mcp_client.session() and call_async(); ToolCallError gains a transport field; one deadline (asyncio.timeout_at) for the whole fetch; a status with a cause (timeout / error:auth / error:network / error:server); a partial brief (card + verified items read before the deadline) on timeout; the card raw read runs alongside the other raw reads; one retry for a fast retryable network/server error while >= 3 s remain.
Reply in <= 25 lines: verdict (GO / GO-WITH-FIXES / NO-GO), then findings HIGH/MEDIUM/LOW with file:line and a concrete trigger. HIGH = a caller of MemoryClient (importers, migrate runner, preflight, hlm review, the hook) now hangs, leaks a session/connection, swallows a real error, or exits differently in a way that loses data or hides a failure; the hook exceeding its 8 s wall budget or the 10 s Claude Code kill; a partial brief showing an item the full brief would not show (unverified, superseded, other project); a secret or token reaching stdout or the log. MEDIUM = wrong cause label, a retry that can double a side effect, log/dry-run fields wrong, tests that cannot fail.

## Round-1 findings (Astra)
**NO-GO**

- **HIGH — `src/hlmemo/brief/fetch.py:377,390`:** Eski sunucu `superseded_by` alanını göndermiyorsa ve yerine geçen kaydın raw okuması deadline’a takılırsa, kısmi brief superseded kaydı gösteriyor. Yeniden üretildi: tam sonuç `[v21]`, `v20` → `superseded`; kısmi sonuç `[v20]`, `v21` → `unverified`. Eksik havuzda authoritative incoming bilgisi olmayan adayları göstermeyin.
- **MEDIUM — `src/hlmemo/cli/mcp_client.py:144`:** Gerçek `httpx2.ConnectTimeout` / `ReadTimeout`, builtin `TimeoutError` kontrolüne girmiyor; sonuç `timeout` yerine `error:network`. Ayrıca timeout için yasaklanan retry yoluna girebiliyor. Her iki exception ile doğrulandı.
- **MEDIUM — `src/hlmemo/cli/mcp_client.py:149`:** JSON hata zarfı içermeyen HTTP 503 yanıtı `error:server` yerine `error:network/HTTPStatusError` oluyor; kullanıcıya “server unreachable” gösteriliyor. 503 response ile doğrulandı.

Doğrulama: gerçek istemci cancellation testi ve iki yeni fetch testi doğrudan çalıştırılarak geçti. Standart pytest, zorunlu `HLM_TEST_DSN` bulunmadığından çalışmadı. Dosyalar değiştirilmedi.
## Diff of the fixes
diff --git a/src/hlmemo/brief/fetch.py b/src/hlmemo/brief/fetch.py
index d10e5f9..1e48308 100644
--- a/src/hlmemo/brief/fetch.py
+++ b/src/hlmemo/brief/fetch.py
@@ -369,10 +369,12 @@ async def gather_snapshot(
     return snap
 
 
-def settle(snap: Snapshot) -> Snapshot:
+def settle(snap: Snapshot, *, cut: bool = False) -> Snapshot:
     """The shown sessions and lessons, the exclusions and ``as_of`` from the candidate pools. A candidate
     whose memory.raw did not (completely) come back, e.g. one a deadline cut, is ``unverified`` and never
-    shown. (On an older server a cut pool also knows fewer superseders: the fallback gap above.)"""
+    shown. ``cut`` (a deadline stopped the reads): a candidate without the server's own incoming status is
+    ``unverified`` too, because the pool fallback may have missed a superseder whose read was cut (review
+    123: an older server, the superseder cut, the superseded item shown)."""
     s_pool, l_pool = snap.pools
     dead = superseded_pool_ids(s_pool + l_pool)
 
@@ -389,6 +391,8 @@ def settle(snap: Snapshot) -> Snapshot:
                 snap.excluded.append((it.handle, "superseded-part"))
             elif not it.server_incoming and pool_supersedes(it, dead):  # older server only
                 snap.excluded.append((it.handle, "superseded"))
+            elif cut and not it.server_incoming:  # the fallback cannot vouch for a cut pool
+                snap.excluded.append((it.handle, "unverified"))
             elif it.logical_id is None:
                 snap.excluded.append((it.handle, "unverified"))
             else:
diff --git a/src/hlmemo/brief/hook.py b/src/hlmemo/brief/hook.py
index c89636e..1ef335d 100644
--- a/src/hlmemo/brief/hook.py
+++ b/src/hlmemo/brief/hook.py
@@ -206,7 +206,7 @@ async def fetch_snapshot(slug: str, cfg: BC.BriefConfig, fetcher: Fetcher | None
                     retried = True
     except TimeoutError:
         stage = partial.stage if partial is not None else ""
-        return "timeout", stage, F.settle(partial) if stage == "details" else None
+        return "timeout", stage, F.settle(partial, cut=True) if stage == "details" else None
 
 
 def build_brief(slug: str, cfg: BC.BriefConfig, fetcher: Fetcher | None, room: int) -> tuple[str, str, Any]:
diff --git a/src/hlmemo/cli/mcp_client.py b/src/hlmemo/cli/mcp_client.py
index 2d54aba..0243f82 100644
--- a/src/hlmemo/cli/mcp_client.py
+++ b/src/hlmemo/cli/mcp_client.py
@@ -141,7 +141,16 @@ def _classify(exc: BaseException) -> ToolCallError:
             return ToolCallError("E_AUTH", "server rejected the device token (401)")
         if status == 403:
             return ToolCallError("E_DEVICE_PENDING", "device not trusted yet (403)", retryable=True)
-    if any(isinstance(leaf, TimeoutError | asyncio.TimeoutError) for leaf in leaves):
+        if (
+            isinstance(status, int) and status >= 500
+        ):  # the server answered: not a network failure (review 123)
+            return ToolCallError(
+                "E_UNAVAILABLE",
+                f"memory server error (HTTP {status})",
+                retryable=True,
+                details={"http": status},
+            )
+    if any(isinstance(leaf, TimeoutError | asyncio.TimeoutError) or _http_timeout(leaf) for leaf in leaves):
         return ToolCallError(
             "E_UNAVAILABLE", "timeout waiting for the memory server", retryable=True, transport="TimeoutError"
         )
@@ -154,6 +163,12 @@ def _classify(exc: BaseException) -> ToolCallError:
     )
 
 
+def _http_timeout(exc: BaseException) -> bool:
+    """An HTTP client's own timeout (httpx ``ConnectTimeout``, ``ReadTimeout``, ...: subclasses of its
+    ``TimeoutException``, not of the builtin ``TimeoutError``) is a timeout too (review 123)."""
+    return any(c.__name__ == "TimeoutException" for c in type(exc).__mro__)
+
+
 def decode_result(result: Any) -> dict[str, Any]:
     """CallToolResult -> dict (success) or ToolCallError (isError)."""
     texts = [getattr(c, "text", None) for c in (result.content or [])]
diff --git a/tests/unit/test_brief_fetch.py b/tests/unit/test_brief_fetch.py
index badc7e1..086ddc5 100644
--- a/tests/unit/test_brief_fetch.py
+++ b/tests/unit/test_brief_fetch.py
@@ -384,6 +384,57 @@ def test_into_keeps_what_was_read_when_the_fetch_is_cut() -> None:
     assert ("v20", "unverified") in snap.excluded
 
 
+def test_r123_a_cut_pool_never_shows_an_item_the_server_did_not_vouch_for() -> None:
+    """Review 123 (Astra): on an older server (no incoming status) lesson 21 supersedes 20 only through the
+    pool fallback; when 21's read is cut, a settle that ignored the cut would show the superseded 20."""
+    s = server_with_notes()
+    s.versions[21]["links"] = [sup(s.versions[20]["lid"])]
+
+    async def call(tool: str, args: dict) -> dict:
+        if tool == "memory.raw" and args["version_id"] == 21:
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
+    assert 20 in [i.version_id for i in F.settle(snap).lessons]  # the hazard the cut flag closes
+    F.settle(snap, cut=True)
+    assert (
+        snap.lessons == []
+        and ("v20", "unverified") in snap.excluded
+        and ("v21", "unverified") in snap.excluded
+    )
+
+
+def test_r123_a_cut_pool_shows_items_the_server_vouched_for() -> None:
+    """A current server sends each item's incoming status: a cut pool still shows what it vouched for."""
+    s = server_with_notes()
+    for v in (10, 11, 20, 21):
+        s.versions[v]["incoming"] = []
+    s.versions[20]["incoming"] = [incoming(s.versions[21]["lid"])]
+
+    async def call(tool: str, args: dict) -> dict:
+        if tool == "memory.raw" and args["version_id"] == 21:
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
+    F.settle(snap, cut=True)
+    assert [i.version_id for i in snap.sessions] == [11, 10] and snap.lessons == []
+    assert ("v20", "superseded") in snap.excluded and ("v21", "unverified") in snap.excluded
+
+
 # --------------------------------------------------------------------------- review 96 Sol #5
 def chunk(a: int, text: str, ordinal: int = 0) -> dict:
     return {"ordinal": ordinal, "char_start": a, "char_end": a + len(text), "text": text}
diff --git a/tests/unit/test_brief_hook.py b/tests/unit/test_brief_hook.py
index 035ee46..6c863c3 100644
--- a/tests/unit/test_brief_hook.py
+++ b/tests/unit/test_brief_hook.py
@@ -597,6 +597,15 @@ def test_deadline_before_the_queries_came_back_is_a_plain_timeout(
 
 
 # --------------------------------------------------------------------------- the cause
+_httpx = pytest.importorskip("httpx2")
+_REQ = _httpx.Request("POST", "https://memory.example/mcp")
+
+
+def test_r123_an_http_timeout_is_never_retried() -> None:
+    status, _ = H.failure(_classify(_httpx.ReadTimeout("read timed out", request=_REQ)))
+    assert status == "timeout" and not H._retryable(status, _classify(_httpx.ReadTimeout("x", request=_REQ)))
+
+
 @pytest.mark.parametrize(
     ("exc", "status", "cause"),
     [
@@ -610,6 +619,16 @@ def test_deadline_before_the_queries_came_back_is_a_plain_timeout(
         (ToolCallError("E_UNAVAILABLE", "database down", retryable=True), "error:server", "E_UNAVAILABLE"),
         (ToolCallError("E_BUDGET_TOO_SMALL", "x"), "error:server", "E_BUDGET_TOO_SMALL"),
         (RuntimeError("x"), "error:RuntimeError", ""),
+        # review 123: an HTTP client's own timeouts are timeouts; a 5xx without a JSON body is the server's
+        (_classify(_httpx.ReadTimeout("read timed out", request=_REQ)), "timeout", ""),
+        (_classify(_httpx.ConnectTimeout("connect timed out")), "timeout", ""),
+        (
+            _classify(
+                _httpx.HTTPStatusError("503", request=_REQ, response=_httpx.Response(503, request=_REQ))
+            ),
+            "error:server",
+            "E_UNAVAILABLE",
+        ),  # fmt: skip
     ],
 )
 def test_failure_names_the_cause(exc: BaseException, status: str, cause: str) -> None:
