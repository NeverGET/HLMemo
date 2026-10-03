## Verdict

**GO-with-fixes.** No HIGH under the stated `hlm.export`-style contract; fix paging before release.

1. **MEDIUM — unstable/ignored offset paging.** [questions.py:695](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-acbb46109cca57a96/src/hlmemo/librarian/questions.py:695), [review.py:257](/Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-acbb46109cca57a96/src/hlmemo/cli/review.py:257)  
   **Failure:** With Q1–Q20 open, answer offset-0 Q1–Q10, then request offset 10: the remaining Q11–Q20 are skipped because `OFFSET` applies to the now-smaller open set. Against an older server, fallback silently ignores offset and repeats the same newest ≤3 notices.  
   **Minimal fix:** replace offset with a `(created_at, question_id)` keyset cursor bound to project/kind/device generation; explicitly reject cursor/offset in legacy fallback.

Verified:

- Visibility matches notices/raw: every subject—including both cross-project subjects—must pass device/class scope plus a READ grant before titles, body heads, quotes, reason, or actions are returned.
- `hlm.questions` is absent from `tools/list`, so G-SURF is unchanged. It is **not owner-client-only**: any trusted bearer with project/subject READ access can call it directly by name. This matches the explicit `hlm.export` precedent. If owner-only was intended, this becomes a HIGH/NO-GO and needs a server-issued capability—not a spoofable client header.
- Answer UUID5s are deterministic over project/question/decision; retry arguments are identical. A later different decision is rejected because the question is closed.
- `--dry-run` sends no `memory.answer` and writes no access event or review log. `review_list` is SELECT-only.
- Checks: 23 unit + 4 targeted integration/G-SURF tests passed; modified-file Ruff and `git diff --check` passed.