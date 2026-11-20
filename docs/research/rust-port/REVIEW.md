# Review of REPORT.md (rust-port), 2026-11-20

Verdict: the recommendation (do not port the server now) is supported. Headline numbers trace to A/B/C and to the code. No HIGH finding. Four MEDIUM, several LOW.

## Numbers checked
Traced and correct: 233 ms (e2e-results.json `query_p50` 232.8, p95 335; note the file is from 2026-09-24, two months old); 859 cassette lines (recounted: 859); 91 of 115 integration files import hlmemo.core/db/librarian (recounted: 91 of 115); 3.7 s (C:22, brief.log n=90); 0.45 s, 15-30 ms, 1.2 GiB (C:96, e2e-results.json:66) all appear in C with labels (the 15-30 ms and 0.1-0.2 GiB are estimates "E", and REPORT does say so only in a footnote).

## Findings

**MEDIUM-1. "~38 go through MCP/HTTP" does not add up with 91 direct.** 91 + 38 = 129 > 115 files. Either some files do both or 38 is wrong. My rough grep for mcp/httpx/TestClient finds 43 files. Evidence: A-inventory.md:116-117, REPORT.md section 1. Fix: state "about 24 files do not import services directly; of the 115, 38-43 touch the wire, some also import services", or recount with one rule.

**MEDIUM-2. "Parameter rewrite `%(name)s` in ~60 query files" is not what the code shows.** `%(name)s` appears in only 18 src files (db 5, librarian 5, ops 4, core 1, server 1, worker 2). B-ecosystem.md:12 says "raw SQL in ~63 files", which is a different quantity (files with SQL, not files with that placeholder style; I count about 53 files with `%s`/`%(`). Evidence: grep over src. Fix: say "~18 files use named placeholders, ~50-60 files hold SQL" and adjust the "error-prone" claim; the work is smaller than implied, though effort totals do not depend on it.

**MEDIUM-3. "22 lookaround regexes" is a count I cannot reproduce.** Lookaround syntax occurs 63 times on 32 lines in 6 files of src/hlmemo/librarian (research.py 19 lines, provider.py 5, redact.py 3, guards.py 2, synthesis.py 2, risk_judge.py 1). A:39 says 22 of 87 `re.compile`; plausible if it counts compiled objects, but the method is not stated. Fix: give the count method or write "about 20-60 lookaround uses in 6 files". Does not change the fancy-regex conclusion.

**MEDIUM-4. Effort has no basis other than line counts.** 170 person-days (range 120-220, A:54 says 169) is stated with a precise table, but REPORT admits no pilot. The per-row days are consistent with A, but the range is a judgement, not a measurement. Fix: label the table "estimate, +-30%" in the headline (lines 1 of "five lines"), not only in the section 1 footnote. Also "eight months of one full-time engineer" contradicts "170 person-days" for most readings (170 days is about 8 months only at ~21 days/month; fine) but it is stated as if two numbers; harmless, clarify.

**LOW-1. The perf headline mixes dates and sources.** 233 ms is a 2026-09-24 e2e run; "prod as of 2026-11-20" in the header overstates freshness. Fix: cite the run date.

**LOW-2. "-0.8 s from one server-side brief call" vs the C text.** C:90 says "about 0.8 s (E), ~6 round trips" at 130 ms WAN = 0.78 s, fine, but REPORT presents it as fact next to Rust's -0.4 s. Mark both as estimates.

**LOW-3. "Memory 0.1-0.2 GiB out of 1.2 GiB"** compares a saving estimate with a prod peak of 1.215 GiB; the smoke figure is 453 MiB (C:96). The saving is the same either way but the percentage looks smaller than it is (up to ~30% of smoke RSS). Mention both.

**LOW-4. rmcp version** B says 3.11.0 (2026-11-17); a search result showed 3.1.2 only in an older blog, consistent with fast release cadence. Not contradicted. Worth stating that major version 3 means API churn (the same churn risk the report assigns to ort).

## Code claims
1. read_service.py:261 `qvec = deps.embedder.embed_query(request.query)` called directly inside the async function: confirmed (inline on the event loop).
2. write_service.py:119 `run_in_executor` in `_cpu`: confirmed, but only for text of at least OFFLOAD_MIN_CHARS (short text runs inline). Fix: REPORT says "as the write path already does", add "for long text".
3. Hook command: `python -P -m hlmemo.brief.hook` appears in src/hlmemo/brief/hook.py:1 docstring and integrations/claude/skills/hlm-library/SKILL.md:129 (with `.venv/bin/python`). Confirmed as the documented form; I did not inspect an installed settings.json.
4. Integration tests: 115 files, 91 import services directly; see MEDIUM-1.
5. Lookaround: see MEDIUM-3.
6. `%(name)s`: see MEDIUM-2.

## Ecosystem claims (web)
- rmcp: official, in modelcontextprotocol/rust-sdk, feature `transport-streamable-http-server` exists (README). Confirmed. Latest version not independently verified beyond a 3.x release.
- ort: latest 2.0.0-rc.15 (published 2026-10-19, docs.rs and arewelearningyet.com). Confirmed. Note ort's own site still pins rc.13 in docs and calls it "production-ready"; the report's "riskiest dependency" framing is fair but slightly strong.
- Official Rust SDK from Anthropic/OpenAI/Google: no evidence of one in my search (results were about Python/TS agent SDKs); inconclusive for Google. B:133-137 reasoning stands but I could not independently confirm absence. Fix: word as "none found as of 2026-11-20".
- tiktoken-rs o200k_base: confirmed in tiktoken-rs source (Tokenizer::O200kBase). A newer crate `tiktoken` v4 (lib.rs) also offers it and is smaller; B lists only tiktoken-rs and bpe-openai. Minor: add the alternative.

## Reasoning gaps
- **Missing: PyO3 hybrid** (keep Python server, move hot packing/normalize/preview to a Rust extension, e.g. via maturin). It targets exactly the 10-30% CPU share at far lower cost than 170 days, and keeps the tests. The report lists only "quick wins in Python" vs full port. Add as option 2.5. Not HIGH because the recommendation is unchanged, but it is the strongest alternative.
- **Missing: dual-implementation cost** during a gradual port (the report says "package by package behind the harness" but gives no cost for maintaining two stacks, doubled review, and the librarian changing weekly). No librarian commit rate is given; `git log` would settle it.
- **Missing: deployment**: Docker image size, build and CI times (Rust release builds with ort/tokenizers are slow), cold start, RAM on the 2-vCPU VM. A static single binary is a real server-side benefit for the Hostinger VM, mentioned only for the client.
- **Missing: team skills.** Cemal is a solo developer working in Python/Next.js/Godot; Rust learning curve and review capacity (the report names review as the bottleneck) are not costed.
- **Security**: memory safety and the rmcp CVE-2026-63128 are mentioned, but a net security view (Python dependency surface vs Rust) is absent. Neutral; LOW.
- Not overreaching in either direction: the "revisit only on a measured trigger" rule is sound. It slightly understates the client option's value by not mentioning that a Rust client would also remove the 0.35-0.4 s `hlm` startup for every CLI call (it does, in the table).
