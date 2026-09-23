You are the adversarial reviewer on HLMemo (repo /Users/cemalkurt/Projects/HLMemo). Your consult 36 (docs/consults/36-sol-review-w0a-delta.md) said DO-NOT-MERGE for W0a: H = the manual rollback after a successful cutover does not restore env secrets; M1 = non-atomic markers; M2 = ops status depends on the DB. The implementer added commits c836d2b and 2862d39 on branch `worktree-agent-a142ee2b9b4948da6` (worktree /Users/cemalkurt/Projects/HLMemo/.claude/worktrees/agent-a142ee2b9b4948da6). Review `git -C <wt> diff 6ddd2c8 2862d39`.
Claims:
- `deploy.sh --rollback` runs rollback.sh through the detached runner, reading only release-state.json;
- preflight before any stop: previous commit, quiesced dump, image ID, env backups, rendered model pinned to that image;
- it refuses a pre-W0 previous release without a restored registration secret;
- order: stop, save the current DB, restore env, checkout, image, dump restore, start, readiness; the rollback pair is consumed;
- backups are kept until `--accept-release`;
- release_state.py writes atomically, with legacy markers derived from it;
- ops status reads loopback /ready first and the DB best-effort.
STATIC REVIEW ONLY. For each of H, M1 and M2: FIXED / PARTIAL / NOT FIXED, and try to break it. Examples: a rollback while a deploy holds the lock; a crash between the env restore and the dump restore (is re-running safe? can it leave new code with old env, or old code with new env?); restoring the dump of the wrong ref; is the "save current DB" dump ever used or cleaned; the refusal logic for a pre-W0 release bypassed via an env precedence path; `--accept-release` deleting backups for a release that is not the current one.
Also answer: is W0a now safe to MERGE and rehearse on a VM before production?
OUTPUT (≤ 350 words): ## Verdict (MERGE / MERGE-WITH-FIXES / DO-NOT-MERGE); ## H/M1/M2 status; ## Remaining findings (table, max 4).
