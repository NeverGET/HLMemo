# Consult 70 — CRITICAL dual review (D-085), RELEASE-GATING for R3 (D-099): pivot-s1-r3 after the D-102 allow-list gates

Your cwd is a clean export of 97344eb (no .git, no secrets).
- `D102.patch`: only the fix commits since review 67 (COMMITS.txt; first-parent, no merges). Wordlist .txt files are excluded but present in `src/hlmemo/core/wordlists/`.
- `FULL-VS-MAIN.patch`: the whole branch under src/tests/deploy/profiles against its merge base with main. This is what R3 ships, with HLM_QUERY_REWRITE ON.
Review 67 is in docs/consults/67-*review*.md. Rules: the rows D-097 and **D-102** in docs/decisions/DECISIONS.md. Read-only.

## Claims (verify; try hard to break them)
1. **Send gate** (`send_block_reason`): zero provider calls if the query contains ANY of the following. All 8 review-67 cases are tested at zero HTTP calls.
   - a credential-lexicon stem anywhere (case/diacritic-insensitive, TR suffixes);
   - an assignment `NAME=value` / `NAME: value`;
   - any URL (`scheme://`, case-insensitive);
   - a high-entropy token (≥16 mixed alnum).
2. **Allow-list guard** (`core/rewrite_guard.py`, prompt v3):
   - Every token of the original that is not a recognised word of the query's language (bundled TR/DE/EN frequency lists, ASCII-folded, TR suffix stripping) must appear verbatim in the rewrite: same multiset, same order, quote delimiters kept.
   - The rewrite may add no protected token, but a token copied verbatim from the query does not count as new.
   - CLI_HEADS is kept, because the wordlists contain "git", "run" and "migrate". Executable paths like `./manage.py` count as command heads.
   - The six D-097 cases and all review-67 cases are rejected; diacritic-less Turkish and German still rewrite.
3. **Latency mode:** it moves to the next profile after the first schema failure, and the per-task hook answers inside 3 s. Background mode keeps the schema retry.
4. The genericity test is AST-scoped.
5. **Gates, flags off/on:** unit 755; full integration 555; G3 0.980; G4 p95 261–271 ms; G-L3 p95 354–397 ms.
6. **Live acceptance:** TR 87%, TR without diacritics 90%, DE 100%.

## Focus
- **Send gate:** find ANY realistic secret-bearing query that still produces a provider call. Consider:
  - obfuscations: "p a r o l a", "şıfre", leetspeak "passw0rd", "pwd", "PIN", "pin kodu", "kredi kartı", "IBAN";
  - JWTs, SSH keys, AWS keys (AKIA…), GitHub tokens (ghp_…), connection strings (postgres://… is a URL, but what about `host=… password=…`?);
  - base64 blobs, emails, phone numbers, personal data;
  - short secrets without a keyword ("hunter2 çalışmıyor").
  Decide what is in scope for the stated policy and what the policy should add.
- **Guard:** construct an ACCEPTED rewrite that changes any identifier, path, command, flag, number, quoted string or negation-relevant technical token. Could the wordlists be abused (a common word that is actually an identifier in this codebase, e.g. `query`, `worker`, `sweeper`)? Is "copied verbatim ⇒ not new" exploitable (a token moved to a different position or role)?
- **Licensing:** the wordlists are CC-BY-SA-4.0 (Hermit Dave FrequencyWords / OpenSubtitles 2018). Is bundling them in this repo, with attribution in wordlists/README.md, compatible with shipping? Flag any obligation.
- Flag-off byte identity versus main is still intact. No regression in the D-097 items (lineage, quotas, 20% share, deadline, demotion union).

## Output contract (≤ 40 lines)
- `## Verdict (MERGE | FIX-NEEDED | DO-NOT-MERGE)`, with one paragraph.
- `## Per-claim`: claims 1–4, each OK, PARTIAL or OPEN, with file:line.
- `## New findings`: a table of severity | file:line | trigger | fix. Real defects only. Mark whether each is in scope of D-102 or a policy extension.
- `## Ship-with-flag-ON (R3)`: yes or no, plus the reason.
