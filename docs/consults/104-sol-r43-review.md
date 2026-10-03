## Verdict

**NO-GO.** Four reproducible HIGH false-keeps violate the fixed release rubric.

1. **HIGH — Markdown wrapping allows authority expansion.** `src/hlmemo/librarian/tasks/research.py:2047-2061,2105-2116`  
   Reproducer: excerpt `See [mirror](https://a|b/path); the pipe is literal.`; writer answer ``See `//b/path`.`` Rules-off drops it; the ship set keeps it with `how=expansion`. Markdown prevents `_SCHEME_AUTHORITY` recognition, so the authority is processed as a path.  
   Minimal fix: unwrap Markdown destinations before parsing and categorically exclude authority components from expansion; add this reproducer.

2. **HIGH — “Exact” matching normalizes meaningful path bytes.** `src/hlmemo/librarian/tasks/research.py:487-492,2026,2043-2044,2139-2145`  
   Reproducer: excerpt ``Routes: `/api/user/guest|admin`.``; answer ``Call `/api/user/admin+`.`` Rules-off drops it; `notation` keeps it because `_notation_key` strips `+`. Case and dash normalization also makes `/API/Setup` equal `/api/setup` and `/foo-bar/baz` equal `/foo—bar/baz`; `url_literals` reports these as `stated`.  
   Minimal fix: use a dedicated byte-preserving URI/path comparator; do not casefold, unify dashes/numbers, or strip valid URI characters.

3. **HIGH — Multi-token code spans are assembled from unrelated commands.** `src/hlmemo/librarian/tasks/research.py:2128-2137`  
   Reproducer: excerpt ``Use `read /api/jobs/pause|resume`. Separately use `delete /api/health`.``; answer ``Use `delete /api/jobs/resume`.`` Rules-off drops it; `notation` keeps it by finding `delete` globally and the expanded path independently.  
   Minimal fix: require one whole source token/code span to support one whole answer code span; never combine token support across commands.

4. **HIGH — Look-alike brackets are treated as optional syntax despite explicit contrary evidence.** `src/hlmemo/librarian/tasks/research.py:2018,2072-2097`  
   Reproducer: excerpt ``The exact literal key is `report[.json]`; the square brackets are part of the key.``; answer ``The exact literal key is `report.json`.`` Rules-off drops it; `notation` keeps it.  
   Minimal fix: remove bracket expansion from R4.3 unless optionality can be authenticated independently; retain only the proven-safe subset and rerun the audit.

5. **MEDIUM — 2d-iii rejects supported Markdown links.** `src/hlmemo/librarian/tasks/research.py:1835-1885`  
   Excerpt `Docs are at https://example.com/guide.` and answer `See [guide](https://example.com/guide).` is kept rules-off but dropped by `url_literals`; the extracted literal is `guide](https://example.com/guide`.  
   Minimal fix: extract the Markdown destination as the literal, excluding label and delimiters; test supported and unsupported destinations.

6. **MEDIUM — Attribution does not use the enabled URL rule.** `src/hlmemo/librarian/tasks/research.py:2544-2551,2923`  
   With generic “setup guide” text in `v1` and the exact URL in `v2`, validation finds the URL in `v2`, but attribution can return `v1` as the primary source because it calls `hard_literals` without `url_literals`.  
   Minimal fix: thread `ProseRules` through attribution and reuse validation’s literal set.

7. **LOW — A verification-line prefix satisfies “resolved”.** `eval/active/al_e4.py:504-514,546-551`  
   Verification evidence `Added a check for the index. Verification completed: all migration regression tests pass.` is satisfied by quoting only `Added a check for the index.`  
   Minimal fix: require normalized equality with a complete verification evidence line for this check.

Confirmed: absent/empty rules preserve main behavior; unknown and cut-rule names fail startup validation; 2b/2d-i/2d-ii and WS-1 have no runtime delta; prompt/schema, migration, Compose, privacy, and spend paths are unchanged. The template, fingerprint, both-service recreation, old-env compatibility, and env-first rollback flow are intact.

Astra-low and Sol-xhigh reviews were merged; direct reproducers were rerun. Full pytest/private replay could not be rerun because the review sandbox provides no writable temporary directory.