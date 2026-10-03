# Consult 105: `hlm curate` routine review (D-085 astra-low, one round)

You review a clean archive of branch `r4.4-curate` at cfeb81e. REVIEW.diff = `git diff c535d5bfdb1ceb400f5cd5d206fefc3e30c71c77..cfeb81e`.

**What it is:** a LOCAL, owner-run tool. It exports project memory and fans candidate relations out to worker agents. The agent command is configurable (`HLM_CURATE_AGENT_CMD`) and the prompt goes on stdin. The pipeline then:
1. builds `hlm links backfill` records;
2. runs a deterministic gate (verbatim and unique quotes, current heads, cycles, duplicates, already linked);
3. runs 2 refuter agents in cross mode;
4. applies an authority filter (D-244);
5. writes a preview bundle.

`--apply` prints, and `--execute preview|apply` runs, the RUNBOOK backfill procedure against prod over SSH.

**Threat model:**
- (a) owner data leaking into tracked files or logs, or to a process that should not get it (the run dir must be gitignored);
- (b) input handling: memory text, item titles and paths are untrusted data. Do they ever become part of a command line instead of staying data? Check the placeholder substitution in the agent command template (`{export}`, `{workdir}`, `{slice}`, `{out}`) and the generated apply.sh (quoting, argv lists vs shell strings);
- (c) prod writes without preview, an exact count and unchanged-counts checks, or an apply of something other than the reviewed final.jsonl;
- (d) the gate accepting a record that is not verbatim or not current, or a cycle;
- (e) worker output trusted without schema validation, or a failed slice silently dropped;
- (f) secrets printed.

**Severity:** HIGH = a reachable (a), (b) or (c), or a gate bypass (d). A HIGH needs a concrete reproducer. MEDIUM = robustness or test gaps. LOW = docs.

**Output:** `## Verdict` (GO / GO-with-fixes / NO-GO); numbered findings with severity, file:line, the scenario and a minimal fix; at most 50 lines.
