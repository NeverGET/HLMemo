**1. BLOCKERS**

1. **D-024 contradiction — §3:** “Every tool result returns `structuredContent` plus the identical compact JSON.” §9.7 repeats this. **Fix:** one canonical JSON string in one `TextContent`; remove duplicate-output language and advertised output schemas. See §3 below.

2. **Approval grants privilege escalation — §2:** “any trusted device may approve” accepts `grants:[{project, role}]`, bypassing the separate grant endpoint’s project-admin requirement. **Fix:** authorize each embedded grant using that same requirement, atomically with approval. Explicitly restrict global device revocation.

3. **Revision authorization is incomplete — §3:** “every listed slug needs `write` grant” checks replacement scopes, without explicitly authorizing the existing logical item. **Fix:** validate immutable home-project ownership and write grants over old∪new project memberships before supersession or conflict disclosures. Reauthorize stored-result replay and define request-ID ownership across devices.

4. **Temporal corrections cannot preserve unaffected intervals — §1:** `CREATE UNIQUE INDEX mv_current ... (logical_id) WHERE superseded_at = 'infinity'` prevents multiple current, nonoverlapping valid-time segments. **Fix:** retain the temporal exclusion constraint, replace conflicting current/card/link uniqueness rules, split corrections into replacement plus surviving intervals, and use one recorded/superseded timestamp. Replace “chunks/links … are rewritten” with immutable historical chunks and temporally superseded edges; drilldown edges must respect requested timestamps.

5. **D-015 card provenance is unrepresentable — §3:** link targets are “logical_id or … item_index”; §4.10 checks whether “derived_from targets [are] superseded.” Logical IDs cannot identify which immutable source version supported the card. **Fix:** store referenced `target_version_id`, expose version-specific dependency input, and compute staleness against it.

6. **D-010 replay lacks sufficient recorded material — §1/§7:** projections use `DEFAULT clock_timestamp()` yet require `test_rebuild_projections_from_events_identical`. **Fix:** specify canonical event payloads containing resolved defaults, generated identities, exact temporal boundaries, and projection/chunking versions. Replay must reuse these rather than generate fresh clocks or IDs.

**2. DEVICE MODEL AUDIT — D-023**

**Not yet sufficient.** The token-derived identity, grant matrix, and §4.4 version predicate are sound foundations, subject to the blockers above.

- Array integrity is explicitly application-enforced, but G5 lacks the promised dedicated integrity test. Test nonexistent/null/duplicate targets and missing home membership.
- Apply both scope axes to cards, raw reads, cursor continuations, links and their endpoints. Chunk scope is only a prefilter; version scope remains authoritative.
- “Immediately” revoked requires defined transaction ordering against concurrent reads/writes/replays and fresh authorization per request. Sequential token rejection is insufficient.
- Reserve device 1 atomically before registration. Its admin flag bypasses project grants, **not** device scope; its effective class remains `server`. Define restart/revocation behavior and keep its token out of ordinary device workflows.
- Pending-device G5 must exercise every route and MCP initialize/list/call, not merely the five tools.

D-022’s later-phase reconstruction placement is consistent.

**3. WIRE FORMAT**

**Compatible with SDK 2.x and MCP; no D-024 reversal needed.** Use `@mcp.tool(structured_output=False)` returning the canonical JSON string, or explicit low-level `CallToolResult(content=[TextContent(...)], is_error=...)`. Validate output internally. Omit `outputSchema`: advertising it requires conforming structured results. [SDK](https://py.sdk.modelcontextprotocol.io/servers/structured-output/#opting-out), [MCP specification](https://modelcontextprotocol.io/specification/2026-07-28/server/tools#output-schema).

Text results are the interoperable baseline; exact pinned Claude/Codex/agy consumption remains G7 verification, not demonstrated here. Add wire assertions for one text block and absent `structuredContent`. Meter that exact text, including final budget metadata. This guarantees payload size under `o200k_base`, not identical model-context cost: clients can wrap, truncate or offload results. [Claude output limits](https://code.claude.com/docs/en/mcp#mcp-output-limits-and-warnings).

**4. NO-GO — resolve these contract defects before starting Phase-0 implementation.**