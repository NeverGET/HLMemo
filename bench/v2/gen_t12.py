#!/usr/bin/env python3
"""Deterministic generator for bench v2 T12 (extract_review): long synthetic code-review documents
with follow-up rounds (fixes, re-opens, severity changes, won't-fix) and their gold JSON.

    python bench/v2/gen_t12.py            # writes bench/v2/tasks/t12_extract_review.json

Same seed -> byte-identical output. The documents are synthetic (fictional findings in the style of
HLMemo's review rounds); no sentence is copied from the repo's docs.
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "tasks" / "t12_extract_review.json"
SEED = 20260923

COMPONENTS = ["api", "worker", "librarian", "retrieval", "deploy", "cli", "db", "importer"]

# (component, title, primary files, problem sentence)
POOL = [
    ("worker", "Lease renewal ignores clock skew", ["src/hlmemo/worker/lease.py"],
     "the lease renewal compares the database clock with the local monotonic clock, so a host with a skewed clock renews too late and a second worker picks up the same job"),
    ("worker", "Heartbeat stops after a transient DB error", ["src/hlmemo/worker/main.py"],
     "a single OperationalError inside the heartbeat coroutine ends the loop silently, and the dashboard shows the worker as idle while it is still processing"),
    ("worker", "Job retries do not back off", ["src/hlmemo/worker/main.py", "src/hlmemo/worker/lease.py"],
     "failed jobs are re-queued with run_after equal to now, so a poison job spins at full speed until it reaches the attempts limit"),
    ("api", "Request body limit checked after buffering", ["src/hlmemo/server/middleware.py"],
     "the size limit is evaluated only after the whole body has been read into memory, which lets a slow client pin memory well above the configured cap"),
    ("api", "Error envelope leaks internal exception text", ["src/hlmemo/server/errors.py"],
     "unexpected exceptions are rendered with str(exc), which exposes SQL fragments and file paths to the client"),
    ("api", "Readiness probe shares the request pool", ["src/hlmemo/server/app.py"],
     "the readiness probe borrows a connection from the request pool, so under load the probe times out and the orchestrator restarts a healthy container"),
    ("api", "Rate limiter keyed on the proxy address", ["src/hlmemo/server/ratelimit.py"],
     "behind the reverse proxy every client shares one limiter bucket, so one noisy client throttles everyone"),
    ("librarian", "Schema retry resends the full prompt without a cap", ["src/hlmemo/librarian/provider.py"],
     "a schema failure triggers a retry that is not counted against the per-job call ceiling, so a model that keeps returning invalid JSON can loop"),
    ("librarian", "Cost reservation not released on timeout", ["src/hlmemo/librarian/budget.py"],
     "when the provider call times out the reserved worst-case amount stays reserved until the sweeper runs, which blocks later jobs for ten minutes"),
    ("librarian", "Circuit breaker never half-opens", ["src/hlmemo/librarian/breaker.py"],
     "after the breaker opens it never moves to the half-open state, so a single burst of provider errors disables the librarian until the service is restarted"),
    ("librarian", "Contradiction batch mixes device scopes", ["src/hlmemo/librarian/tasks/contradiction.py"],
     "candidate pairs are batched without checking device scope, so a personal-device item can be sent to the model together with a work-device item"),
    ("retrieval", "Query-term filter drops identifiers with digits", ["src/hlmemo/core/retrieval.py"],
     "the document-frequency filter treats tokens such as v2 or p95 as common words and removes them from the lexical query"),
    ("retrieval", "Preview window cuts through multibyte characters", ["src/hlmemo/core/preview.py"],
     "the preview is sliced by byte offset, so Turkish characters at the boundary are split and the JSON encoder replaces them"),
    ("retrieval", "RRF tie order is not deterministic", ["src/hlmemo/core/retrieval.py", "src/hlmemo/core/rank.py"],
     "hits with equal fused scores are returned in hash order, which makes the golden replay flaky"),
    ("retrieval", "Title list ignores archived flag", ["src/hlmemo/db/read_queries.py"],
     "the title lexical list does not filter archived items, so an archived note can outrank the current one"),
    ("db", "Migration takes an ACCESS EXCLUSIVE lock on chunks", ["alembic/versions/0007_signals.py"],
     "adding the column with a volatile default rewrites the whole chunks table under an exclusive lock"),
    ("db", "Missing index on jobs(priority, run_after)", ["alembic/versions/0006_librarian.py"],
     "the lease query scans the whole jobs table once the backlog passes a few thousand rows"),
    ("db", "Downgrade drops data without a guard", ["alembic/versions/0008_topics.py"],
     "the downgrade path drops the topics table even when it contains rows, with no confirmation flag"),
    ("deploy", "Backup timer runs before the volume is mounted", ["deploy/systemd/hlm-backup.timer", "deploy/backup.sh"],
     "after a reboot the timer fires before the data volume is available and writes an empty dump that later passes the size check"),
    ("deploy", "Deploy script inherits stdin in remote commands", ["deploy/deploy.sh"],
     "commands inside the remote script read from the same stdin as the piped script and swallow the remaining lines"),
    ("deploy", "Checkout permissions too strict for the container user", ["deploy/remote-deploy.sh"],
     "files created under the runner's strict umask are unreadable for the unprivileged container user"),
    ("deploy", "Caddy config reload not validated", ["deploy/caddy/Caddyfile", "deploy/deploy.sh"],
     "a syntax error in the edge config is only noticed after the reload, when the proxy has already dropped the old config"),
    ("cli", "Wrapper exits 0 when the preflight query fails", ["src/hlmemo/cli/hlm.py"],
     "a failed preflight query is logged but the wrapper still launches the agent and returns success"),
    ("cli", "Config dir created world-readable", ["src/hlmemo/cli/config.py"],
     "the config directory holding the device token is created with the default umask and ends up readable by other local users"),
    ("cli", "Device fingerprint reused across config dirs", ["src/hlmemo/cli/device.py"],
     "two config directories on one machine produce the same fingerprint, so the second registration is rejected"),
    ("importer", "Serena importer loses original timestamps", ["src/hlmemo/importer/serena.py"],
     "imported memories get the import time as valid_from instead of the file's modification time"),
    ("importer", "Re-import creates duplicate versions", ["src/hlmemo/importer/base.py"],
     "the idempotency key includes the absolute path, so importing the same directory from another mount point duplicates every item"),
    ("importer", "Markdown front matter parsed as body", ["src/hlmemo/importer/markdown.py"],
     "YAML front matter is kept in the body text, which pollutes the lexical index with keys like tags and date"),
    ("api", "Admin token compared with ==", ["src/hlmemo/server/admin.py"],
     "the admin token check uses a plain equality comparison instead of a constant-time compare"),
    ("worker", "Embedding batch size ignores token length", ["src/hlmemo/worker/embed.py"],
     "batches are sized by chunk count only, so a batch of long chunks exceeds the model's memory budget"),
    ("librarian", "Prompt version not recorded in the ledger", ["src/hlmemo/librarian/ledger.py"],
     "llm_calls rows omit the prompt version, so a quality regression cannot be tied to a prompt change"),
    ("retrieval", "Historical queries use the current DF cache", ["src/hlmemo/core/df_cache.py"],
     "a valid_at query in the past is filtered with term statistics of the current corpus"),
    ("db", "Advisory lock taken before authorization", ["src/hlmemo/core/write_service.py"],
     "the write path takes the advisory lock before checking grants, which turns lock contention into an existence oracle"),
    ("deploy", "Rollback keeps the failed image tag", ["deploy/remote-deploy.sh"],
     "after a failed deploy the prod tag still points at the failed release, so the next restart runs broken code"),
    ("cli", "Token printed in verbose mode", ["src/hlmemo/cli/hlm.py", "src/hlmemo/cli/http.py"],
     "the verbose flag dumps request headers including the bearer token to stderr"),
    ("importer", "Large files read fully into memory", ["src/hlmemo/importer/base.py"],
     "every source file is read with read_text before chunking, which spikes memory on multi-megabyte exports"),
]

SEV_PHRASES = {
    "high": ["Severity: High.", "We rate this high: it is reachable without special access.", "(high)", "This is a high-severity issue."],
    "medium": ["Severity: Medium.", "We consider it medium severity.", "(medium)", "Impact is moderate, so medium."],
    "low": ["Severity: Low.", "Low severity; mostly an operability annoyance.", "(low)", "We rate this low."],
}
REPRO = [
    "It reproduces reliably with the fixture from the previous round.",
    "We reproduced it twice on a fresh stack; the third attempt needed a slower network to trigger.",
    "A minimal reproduction is a two-line script that calls the endpoint in a loop.",
    "The integration test suite does not cover this path, which is why it was not caught earlier.",
    "It only shows up under concurrent load, so unit tests with a single client pass.",
]
IMPACT = [
    "In production this would surface as sporadic timeouts rather than a clear error.",
    "The failure is silent: no log line at warning level or above is emitted.",
    "Operators would notice only through indirect symptoms, for example a growing backlog.",
    "The blast radius is limited to a single project, but that project is usually the busiest one.",
    "Recovery requires a manual restart, and nothing alerts on the condition.",
]
FIX = [
    "The suggested fix is small and local to the named file.",
    "We suggest adding a regression test first and then changing the implementation.",
    "A guard at the call site is enough for now; a structural fix can follow later.",
    "The fix should also add a metric so that the condition becomes visible.",
    "Prefer failing loudly over the current silent fallback.",
]
FILLER = [
    "This round reviewed the diff between the previous release and the current branch head, plus the files the diff touches indirectly.",
    "We ran the full test suite, the golden replay and the load probe before writing this report; all numbers below come from those runs.",
    "Where a finding mentions a file, the line numbers refer to the branch head at the time of the review and may have moved since.",
    "Findings are grouped by the order in which we found them, not by severity; the summary table at the end is authoritative only for the first round.",
    "General observation: logging around error paths is inconsistent, but we do not file that as a finding because it is not actionable as written.",
    "The documentation under docs/ was not part of this review. The helper scripts under scripts/ were read but not executed.",
    "Several suspected issues turned out to be false alarms after reproduction; they are not listed.",
    "Unrelated to any single finding: the file tests/conftest.py was touched by this diff, but we found nothing wrong in it.",
    "For context, the configuration used for the load probe lives in deploy/compose.yaml and was not modified during the review.",
]


def commit(rng: random.Random) -> str:
    return "".join(rng.choice("0123456789abcdef") for _ in range(7))


def make_case(idx: int, rng: random.Random, n: int, tier: str) -> dict:
    picks = rng.sample(POOL, n)
    ids = [f"F{i + 1:02d}" for i in range(n)]
    state = {}
    for fid, (comp, title, files, prob) in zip(ids, picks):
        state[fid] = {"id": fid, "title": title, "severity": rng.choice(["high", "medium", "medium", "low", "low"]),
                      "status": "open", "component": comp, "files": list(files), "fixed_in": None, "_prob": prob}
    initial = {fid: dict(s, files=list(s["files"])) for fid, s in state.items()}

    # finding sections, presented in shuffled order for medium/hard
    order = list(ids)
    if tier != "easy":
        rng.shuffle(order)
    parts = [f"# Review report R{idx:02d}: branch review round {idx}\n",
             " ".join(rng.sample(FILLER, 3)) + "\n",
             "## Round 1 findings\n"]
    for fid in order:
        s = initial[fid]
        files_txt = " and ".join(f"`{f}`" for f in s["files"])
        area = rng.choice([f"Component: {s['component']}.", f"Area: {s['component']}.", f"This sits in the {s['component']} component."])
        body = (f"### {fid}: {s['title']}\n{area} In {files_txt}, {s['_prob']}. {rng.choice(SEV_PHRASES[s['severity']])} "
                f"{rng.choice(REPRO)} {rng.choice(IMPACT)} {rng.choice(FIX)} {rng.choice(REPRO)} {rng.choice(IMPACT)}\n")
        parts.append(body)
    parts.append(" ".join(rng.sample(FILLER, 2)) + "\n")

    # follow-up rounds
    rounds = {"easy": 1, "medium": 2, "hard": 3}[tier]
    for r in range(2, 2 + rounds):
        parts.append(f"## Round {r} follow-up\n")
        parts.append(rng.choice(FILLER) + "\n")
        n_events = {"easy": max(2, n // 3), "medium": max(4, n // 2), "hard": max(6, (2 * n) // 3)}[tier]
        for fid in rng.sample(ids, n_events):
            s = state[fid]
            kinds = ["fix", "fix", "wontfix"]
            if tier != "easy":
                kinds += ["sev", "files"]
            if s["status"] == "fixed" and tier == "hard":
                kinds += ["reopen", "reopen"]
            k = rng.choice(kinds)
            if k == "fix" and s["status"] != "fixed":
                c = commit(rng)
                s["status"], s["fixed_in"] = "fixed", c
                extra = ""
                if tier == "hard" and rng.random() < 0.4:
                    nf = rng.choice(["tests/integration/test_regressions.py", "tests/unit/test_guards.py", "docs/status/BACKLOG.md"])
                    if nf not in s["files"]:
                        s["files"].append(nf)
                    extra = f" The fix also touched `{nf}`."
                parts.append(f"- {fid} ({s['title']}): fixed in commit {c}; verified by re-running the reproduction.{extra}\n")
            elif k == "wontfix" and s["status"] == "open":
                s["status"], s["fixed_in"] = "wont_fix", None
                parts.append(f"- {fid}: owner decision, won't fix (accepted risk for now). Documented in the backlog.\n")
            elif k == "sev":
                new = rng.choice([x for x in ("high", "medium", "low") if x != s["severity"]])
                word = "downgraded" if ["low", "medium", "high"].index(new) < ["low", "medium", "high"].index(s["severity"]) else "upgraded"
                s["severity"] = new
                parts.append(f"- {fid}: severity {word} to {new} after a second reproduction.\n")
            elif k == "files":
                nf = rng.choice(["src/hlmemo/core/config.py", "src/hlmemo/server/app.py", "deploy/compose.prod.yaml"])
                if nf not in s["files"]:
                    s["files"].append(nf)
                    parts.append(f"- {fid}: the same defect also exists in `{nf}`; the finding now covers both places.\n")
                else:
                    parts.append(f"- {fid}: no change since the last round.\n")
            elif k == "reopen" and s["status"] == "fixed":
                old = s["fixed_in"]
                s["status"], s["fixed_in"] = "open", None
                parts.append(f"- {fid}: re-opened. The fix in {old} was incomplete: the second code path is still affected.\n")
            else:
                parts.append(f"- {fid}: no change since the last round.\n")
        if tier == "hard":
            parts.append("Decoy note: an earlier draft of this report mentioned `src/hlmemo/core/legacy_rank.py`, "
                         "but that file no longer exists and is not part of any finding.\n")
    parts.append("## Closing\n" + " ".join(rng.sample(FILLER, 2)) +
                 " The summary table from round 1 is outdated; the follow-up rounds above are the final state.\n")
    doc = "\n".join(parts)
    # pad medium/hard documents with extra reviewer context so the token length lands in 3-6k
    target_chars = {"easy": 12000, "medium": 18000, "hard": 23000}[tier]
    notes = []
    while len(doc) + sum(len(x) for x in notes) < target_chars:
        notes.append(rng.choice(FILLER) + " " + rng.choice(IMPACT) + " " + rng.choice(REPRO))
    if notes:
        doc = doc.replace("## Closing\n", "## Reviewer notes\n" + "\n".join(notes) + "\n\n## Closing\n")

    findings = []
    for fid in ids:
        s = state[fid]
        findings.append({k: s[k] for k in ("id", "title", "severity", "status", "component", "files", "fixed_in")})
    counts = {sv: sum(1 for f in findings if f["severity"] == sv) for sv in ("high", "medium", "low")}
    counts["open"] = sum(1 for f in findings if f["status"] == "open")
    return {
        "id": f"T12-{idx:02d}", "tier": tier, "source": "synthetic",
        "rationale": f"generator seed {SEED}, {n} findings, {rounds} follow-up round(s); gold = final state after all follow-ups",
        "components": COMPONENTS, "document": doc,
        "gold": {"findings": findings, "counts": counts},
        "doc_sha256": hashlib.sha256(doc.encode()).hexdigest()[:16],
    }


def main():
    rng = random.Random(SEED)
    plan = [("easy", 10), ("easy", 11), ("easy", 12), ("medium", 13), ("medium", 14), ("medium", 15), ("medium", 16),
            ("hard", 17), ("hard", 19), ("hard", 21)]
    cases = [make_case(i + 1, rng, n, tier) for i, (tier, n) in enumerate(plan)]
    pack = {"task": "extract_review", "family": "T12", "version": 1, "pack": "public",
            "description": "Long synthetic review report with follow-up rounds -> strict nested JSON of the final state of every finding.",
            "changelog": [], "generator": {"script": "bench/v2/gen_t12.py", "seed": SEED}, "cases": cases}
    OUT.write_text(json.dumps(pack, ensure_ascii=False, indent=1) + "\n")
    for c in cases:
        print(c["id"], c["tier"], len(c["gold"]["findings"]), "findings", len(c["document"]), "chars", c["gold"]["counts"])


if __name__ == "__main__":
    main()
