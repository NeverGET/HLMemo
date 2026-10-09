## Verdict

**NO-GO.** Server-side gating works, but ambient-environment custody still lets a documented direct agent launch obtain the owner capability. Round-1 HIGH is therefore **PARTIAL**, not closed.

1. **HIGH — Owner token is exposed to directly launched agents.** `src/hlmemo/cli/hlm.py:939-942`; `src/hlmemo/cli/launch.py:74-79`; `docs/USAGE.md:119-121,303`; `src/hlmemo/server/mcp_server.py:168-185,229-230`
   **Failure:** `hlm review` reads a long-lived ambient `HLM_OWNER_TOKEN`. Only `hlm claude|codex|agy` sanitizes it; documented direct launches inherit it. A shell-capable agent can read the variable and issue `tools/call` with the header.
   **Reproducer:** with a sentinel exported, a direct child reported `HLM_OWNER_TOKEN=present`, while `child_env` reported absent; using that inherited value as `X-HLM-Owner-Token` changed the same agent `AuthContext` call from `E_FORBIDDEN` to success.
   **Minimal fix:** stop ambient-env custody on the owner machine; obtain the capability through explicit user presence (hidden TTY input) or a short-lived SSH-minted capability, and test every documented agent launch. Do not rely on wrapper sanitization alone.

2. **MEDIUM — Production has no owner-token provisioning or rotation path.** `deploy/api.env.example:1-7`; `deploy/scripts/first_deploy.sh:141-146`; `deploy/RUNBOOK.md:88-105`; `src/hlmemo/config.py:349-355`
   **Failure:** fresh and existing production installs omit `HLM_OWNER_TOKEN`; the API remains healthy, but `hlm.questions` always refuses and `hlm review` silently degrades to limited notices. No documented atomic server/client rotation exists.
   **Minimal fix:** generate/provision an independent ≥32-character token in `api.env`, document owner-client transfer/storage and API recreation on rotation, and add release smoke calls proving bearer-only denial and owner success without printing the token.

3. **MEDIUM — A valid generated cursor can exceed its own limit. Round-1 paging is PARTIAL.** `src/hlmemo/librarian/questions.py:602-632`; `src/hlmemo/server/tools/questions.py:40-41`; `src/hlmemo/cli/hlm.py:935-938`
   **Failure:** a valid 64-character project plus 32-character kind produces a 162-character cursor, but server schema, request model, and CLI cap it at 160; page two is rejected.
   **Reproducer:** `review_cursor("p"*64,"k"*32,...)` returned length 162 and parsed successfully, but `ReviewListRequest` raised `string_too_long(max_length=160)`.
   **Minimal fix:** derive the maximum from field bounds (currently 162) and add maximum-boundary server/CLI tests.

4. **LOW — Header scope and timing are broader than necessary.** `src/hlmemo/cli/mcp_client.py:71-90,163-190`; `src/hlmemo/server/mcp_server.py:175-185,221-269`
   **Failure:** the owner header is sent on initialize/list and every review-side query/export/drilldown/answer, not only `hlm.questions`. Current application/Caddy logs and errors do not echo it; wrong configured tokens use `compare_digest`, while missing/short configuration takes an observable early-return path that reveals only configuration state.
   **Minimal fix:** attach the header per `hlm.questions` call and compare against a fixed-size dummy when configuration is unusable.

5. **MEDIUM — Gate `hlm.export` now, or explicitly redefine it as an agent API.** `src/hlmemo/server/tools/__init__.py:75-91,103`
   **Failure:** despite “hlm CLI only,” any read bearer can directly bulk-enumerate its entire readable project, bypassing progressive disclosure. It does not cross device/class/project visibility, so this is not a disclosure HIGH under the stated rubric.
   **Minimal fix:** set `owner_only=True` and update import/export clients to present the repaired owner capability; otherwise document and explicitly accept bulk agent enumeration.

Round-1 status: agent-callability **PARTIAL**; offset mutation bug **CLOSED** at `questions.py:737-745`; fallback cursor refusal **CLOSED** at `review.py:272-281`; budget dead-end **CLOSED** at `questions.py:813-821`. Unmodified cross-project/kind cursors are rejected, but the unsigned fields can be rewritten; authorization and visibility are rechecked, so forgery grants no data. Cursor/token-generation binding remains an acceptable non-security omission.

Checks: both requested diffs inspected; both `git diff --check` passed; focused pure-Python owner-gate/environment and maximum-cursor reproducers run. DB pytest was not rerun in this read-only worker because the global fixture attempted `docker compose up`.
