"""The ``hlm curate`` preview bundle and the ``--apply`` script (deterministic).

``final.jsonl``  the records to apply (``hlm links backfill --proposals``)
``held.jsonl``   records NOT applied, each with ``held_reason`` (authority | pass2_conflict |
                 pass2_incomplete | fix_failed_gate)
``REVIEW.md``    one section per link: stale span, current quote, why, the verifiers' verdicts
``summary.json`` counts per stage

``apply/apply.sh`` follows the RUNBOOK "Curated links" procedure: stream the file into the api
container, a ``--dry-run`` preview whose link count must equal the approved count with the
event/link counts unchanged, and only in ``apply`` mode the apply (one event), then the clean-up.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hlmemo.curate.pipeline import Run

HISTORY_POLICY_HEADING = "### HISTORY POLICY (owner rule, D-244)"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
REMOTE_PATH_RE = re.compile(r"^/[A-Za-z0-9_./-]+$")


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _quote(text: str | None) -> str:
    if not text:
        return "  > (none)"
    return "\n".join("  > " + ln for ln in str(text).splitlines() or [""])


def _one_line(text: Any, n: int = 200) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _item_line(r: Run, lid: int, vid: Any) -> str:
    it = r.export().head(int(lid))
    if it is None:
        return f"v{vid} (not in the export)"
    src = f" · `{it.source_path}`" if it.source_path else " · (no source path)"
    return f"{it.clue} {_one_line(it.title, 90)!r}{src} · valid_from {it.valid_from[:10]}"


def _section(
    r: Run,
    n: str,
    rec: dict[str, Any],
    p1: dict[str, dict[str, Any]],
    p2: dict[str, list[dict[str, Any]]],
    warns: dict[str, list[str]],
) -> list[str]:
    cid = rec.get("cid") or "?"
    out = [
        f"### {n}. {rec.get('src_clue')} → {rec.get('dst_clue')}  ({cid})",
        f"- **Newer (src):** {_item_line(r, rec['src_logical_id'], rec['src_vid'])}",
        f"- **Older (dst):** {_item_line(r, rec['dst_logical_id'], rec['dst_vid'])}",
        "- **Stale span (older):**",
        _quote(rec.get("older_span")),
        "- **Current quote (newer):**",
        _quote(rec.get("newer_quote")),
        f"- **Why:** {_one_line(rec.get('why'))}",
    ]
    v1 = p1.get(cid)
    if v1:
        d = v1.get("direction_ok")
        dtxt = "" if d is None else f", direction_ok={str(d).lower()}"
        out.append(f"- **Pass 1** ({v1.get('wid')}): {v1['verdict']}{dtxt}: {_one_line(v1.get('why'))}")
    for v in p2.get(cid, []):
        tests = f" failed {v['failed_tests']}" if v.get("failed_tests") else ""
        fix = v.get("fix") or {}
        fixed = [k for k in ("older_span", "newer_quote", "src_logical_id") if fix.get(k)]
        ftxt = f" (fix: {', '.join(fixed)})" if fixed else ""
        out.append(
            f"- **Pass 2** ({v.get('reviewer')}): {v['verdict']}{tests}{ftxt}: {_one_line(v.get('reason'))}"
        )
    if rec.get("fixed"):
        out.append("- **Fixed by pass 2**, then re-gated.")
    if warns.get(cid):
        out.append(f"- **Gate:** {', '.join(warns[cid])}")
    if rec.get("held_reason"):
        extra = ""
        if rec.get("src_source") is not None or rec["held_reason"] == "authority":
            extra = f" (src source: `{rec.get('src_source')}`)"
        if rec.get("gate_errors"):
            extra = f" (gate: {', '.join(rec['gate_errors'])})"
        out.append(f"- **HELD:** {rec['held_reason']}{extra}")
    return out + [""]


def write_bundle(r: Run) -> dict[str, Any]:
    final = _jsonl(r.p("authority", "final.jsonl"))
    held = (
        _jsonl(r.p("refine", "held.jsonl"))
        + _jsonl(r.p("gate2", "held.jsonl"))
        + _jsonl(r.p("authority", "held.jsonl"))
    )
    dropped = _jsonl(r.p("refine", "dropped.jsonl")) + _jsonl(r.p("gate2", "dropped.jsonl"))
    (r.p("final.jsonl")).write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in final), encoding="utf-8"
    )
    (r.p("held.jsonl")).write_text(
        "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in held), encoding="utf-8"
    )

    stages: dict[str, Any] = {}
    for s in r.stages():
        if s == "bundle":
            continue
        st = (
            json.loads(r.p(s, "stage.json").read_text(encoding="utf-8"))
            if r.p(s, "stage.json").is_file()
            else {}
        )
        stages[s] = {"status": st.get("status"), **(st.get("counts") or {})}
    failed_slices = {
        s: stages.get(s, {}).get("failed_slices", 0) for s in ("map", "pass1", "pass2") if s in stages
    }
    held_by = dict(sorted(Counter(h["held_reason"] for h in held).items()))
    summary = {
        "project": r.cfg.project,
        "run": r.root.name,
        "today": r.cfg.today,
        "final": len(final),
        "held": held_by,
        "dropped": len(dropped),
        "failed_slices": failed_slices,
        "stages": stages,
    }
    (r.p("summary.json")).write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    p1 = {v["cid"]: v for v in _jsonl(r.p("pass1", "verdicts.jsonl"))}
    p2: dict[str, list[dict[str, Any]]] = {}
    for v in _jsonl(r.p("pass2", "verdicts.jsonl")):
        p2.setdefault(v.get("cid") or "?", []).append(v)
    warns: dict[str, list[str]] = {}
    for stage in ("gate1", "gate2"):
        rep = (
            json.loads(r.p(stage, "report.json").read_text(encoding="utf-8"))
            if r.p(stage, "report.json").is_file()
            else {}
        )
        for row in rep.get("rows", []):
            if row.get("warnings") and row.get("cid"):
                warns[row["cid"]] = sorted(set(warns.get(row["cid"], [])) | set(row["warnings"]))
    meta = json.loads(r.p("export", "export.json").read_text(encoding="utf-8"))
    lines = [
        f"# Curation review: {r.cfg.project} · run {r.root.name}",
        "",
        f"- Export: {meta['items']} items, {meta['live_supersedes']} live `supersedes` links, "
        f"fingerprint `{meta['fingerprint'][:12]}`, taken {meta['taken_at']} ({meta['how']}).",
        f"- **Final links: {len(final)}** (`final.jsonl`). Held: {sum(held_by.values())} {held_by or ''}. "
        f"Dropped: {len(dropped)}.",
        "- Authority allowlist (src `source.path` without its anchor): "
        + ", ".join(f"`{a}`" for a in r.cfg.authority)
        + ".",
    ]
    bad = {k: v for k, v in failed_slices.items() if v}
    if bad:
        lines.append(
            f"- **WARNING: failed worker slices {bad}.** Their candidates or records were NOT judged; "
            "re-run the same command to retry them."
        )
    lines += [
        "- History policy: only present-tense states and instructions are flagged; dated findings and "
        "review records stay as history (the rule is verbatim in every worker brief).",
        "- Approve by applying: `hlm curate --run-dir <this dir> --apply --state <deploy state dir>` "
        "prints the RUNBOOK commands (preview first). Edit `final.jsonl` first to leave a link out.",
        "",
        f"## Final links ({len(final)})",
        "",
    ]
    for k, rec in enumerate(final, 1):
        lines += _section(r, f"F{k}", rec, p1, p2, warns)
    lines += [f"## Held ({len(held)})", ""]
    for k, rec in enumerate(held, 1):
        lines += _section(r, f"H{k}", rec, p1, p2, warns)
    lines += [f"## Dropped ({len(dropped)})", "", "| cid | src → dst | by | reason |", "|---|---|---|---|"]
    for rec in dropped:
        cid = rec.get("cid") or "?"
        if rec.get("dropped_by") == "pass2":
            why = "; ".join(
                f"{v.get('reviewer')} {v['verdict']} {v.get('failed_tests') or ''} "
                f"{_one_line(v.get('reason'), 120)}"
                for v in p2.get(cid, [])
            )
        else:
            why = ", ".join(rec.get("gate_errors") or [])
        lines.append(
            f"| {cid} | {rec.get('src_clue')} → {rec.get('dst_clue')} | {rec.get('dropped_by')} | "
            f"{why.replace('|', '/')} |"
        )
    lines.append("")
    rejected = [
        row
        for stage in ("gate1",)
        for row in (
            json.loads(r.p(stage, "report.json").read_text(encoding="utf-8")).get("rows", [])
            if r.p(stage, "report.json").is_file()
            else []
        )
        if row.get("errors")
    ]
    lines += [
        f"## Rejected by the gate ({len(rejected)})",
        "",
        "Pass-1 supersessions whose quotes, heads or graph failed the deterministic gate (not re-judged).",
        "",
        "| cid | src → dst | gate errors |",
        "|---|---|---|",
    ]
    lines += [
        f"| {row.get('cid')} | {row.get('src')} → {row.get('dst')} | {', '.join(row['errors'])} |"
        for row in rejected
    ]
    lines.append("")
    failed_rows = [
        (s, json.loads(p.read_text(encoding="utf-8")))
        for s in ("map", "pass1", "pass2")
        for p in sorted(r.p(s).glob("*/status.json"))
        if r.p(s).is_dir()
    ]
    failed_rows = [(s, st) for s, st in failed_rows if st.get("status") == "failed"]
    if failed_rows:
        lines += ["## Failed worker slices", ""]
        for s, st in failed_rows:
            errs = _one_line("; ".join(st["errors"]), 300)
            lines.append(f"- {s}/{st['wid']} after {st['attempts']} attempt(s): {errs}")
        lines.append("")
    (r.p("REVIEW.md")).write_text("\n".join(lines), encoding="utf-8")
    return {"final": len(final), "held": sum(held_by.values()), "dropped": len(dropped)}


# ------------------------------------------------------------------ --apply
def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


APPLY_TEMPLATE = r"""#!/usr/bin/env bash
# Generated by `hlm curate --apply` for run @@RUN@@ (project @@PROJECT@@): the RUNBOOK "Curated links"
# procedure. Mode: preview | apply. Both modes stream the file into the api container, run the
# --dry-run preview (PASS: exit 0, len(links) == @@APPROVED@@, event/link counts unchanged) and remove the
# temp file; only `apply` then writes the links (ONE librarian event). Outputs go to @@OUT@@.
set -uo pipefail
mode=${1:-}
case "$mode" in preview | apply) ;; *) echo "usage: apply.sh preview|apply" >&2; exit 64 ;; esac
SSHC=@@SSHC@@
HOST=@@HOST@@
P=@@FILE@@
OUT=@@OUT@@
APPROVED=@@APPROVED@@
SHA=@@SHA@@
mkdir -p "$OUT"
sha_now=$(python3 -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$P")
[[ "$sha_now" == "$SHA" ]] || { echo "ABORT: $P changed after this script was generated; re-run hlm curate --apply"; exit 3; }
n=$(grep -c . "$P")
[[ "$n" == "$APPROVED" ]] || { echo "ABORT: $P has $n records, approved $APPROVED"; exit 3; }
counts() {
  ssh -F "$SSHC" "$HOST" 'cd @@APP@@ && HLM_ENV_FILE=@@ENV@@ bash deploy/scripts/stack.sh exec -T db sh -s' <<'SQL'
psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --tuples-only --no-align --command="SELECT (SELECT count(*) FROM events), (SELECT count(*) FROM links), (SELECT count(*) FROM links l JOIN projects p ON p.project_id = ANY (l.project_ids) WHERE p.slug = '@@PROJECT@@' AND l.rel = 'supersedes' AND l.props->>'by' = 'backfill' AND l.superseded_at = 'infinity')"
SQL
}
cleanup() {
  ssh -F "$SSHC" "$HOST" 'cd @@APP@@ && HLM_ENV_FILE=@@ENV@@ bash deploy/scripts/stack.sh exec -T api rm -f @@RTMP@@' </dev/null && echo "remote temp file removed"
}
ssh -F "$SSHC" "$HOST" "cd @@APP@@ && HLM_ENV_FILE=@@ENV@@ bash deploy/scripts/stack.sh exec -T api sh -c 'umask 077 && cat > @@RTMP@@'" < "$P" || { echo "ABORT: upload failed"; exit 4; }
trap cleanup EXIT
before=$(counts) || { echo "ABORT: counts failed"; exit 4; }
echo "counts before: $before   (events|links|live backfill links of @@PROJECT@@)"
ssh -F "$SSHC" "$HOST" 'cd @@APP@@ && HLM_ENV_FILE=@@ENV@@ bash deploy/scripts/stack.sh exec -T api hlm links backfill --project @@PROJECT@@ --apply --proposals @@RTMP@@ --dry-run --json' </dev/null > "$OUT/preview.json"; rc=$?
echo "preview rc=$rc"
[[ $rc == 0 ]] || { echo "ABORT: preview exit $rc"; cat "$OUT/preview.json"; exit 5; }
python3 - "$OUT/preview.json" "$APPROVED" <<'PY' || { echo "ABORT: preview failed"; exit 5; }
import json, sys
d = json.load(open(sys.argv[1]))
n = len(d["links"])
ok = d["preview"] and n == int(sys.argv[2])
print("preview", "PASS" if ok else "FAIL", n, "of", sys.argv[2], {k: v for k, v in d.items() if k != "links"})
sys.exit(not ok)
PY
after=$(counts)
[[ "$after" == "$before" ]] && echo "counts unchanged: $before" || { echo "ABORT: counts changed by the preview: $after"; exit 6; }
if [[ $mode == apply ]]; then
  ssh -F "$SSHC" "$HOST" 'cd @@APP@@ && HLM_ENV_FILE=@@ENV@@ bash deploy/scripts/stack.sh exec -T api hlm links backfill --project @@PROJECT@@ --apply --proposals @@RTMP@@ --json' </dev/null > "$OUT/apply.json"; rc=$?
  echo "apply rc=$rc"
  python3 -c 'import json, sys; d = json.load(open(sys.argv[1])); print({k: (len(v) if isinstance(v, list) else v) for k, v in d.items()})' "$OUT/apply.json"
  echo "counts after: $(counts)"
  exit $rc
fi
"""  # noqa: E501


def apply_script(
    *,
    run_name: str,
    project: str,
    final: Path,
    out: Path,
    ssh_config: Path,
    ssh_host: str,
    remote_app: str,
    remote_env: str,
) -> str:
    """The apply script text. Every value is validated (slug, host, remote paths) or shell-quoted:
    the project slug and remote paths are pasted into remote command strings."""
    if not SLUG_RE.match(project):
        raise ValueError(f"project slug not safe for the remote commands: {project!r}")
    if not HOST_RE.match(ssh_host):
        raise ValueError(f"ssh host alias not safe: {ssh_host!r}")
    for p in (remote_app, remote_env):
        if not REMOTE_PATH_RE.match(p):
            raise ValueError(f"remote path not safe: {p!r}")
    approved = sum(1 for x in final.read_text(encoding="utf-8").splitlines() if x.strip())
    tag = re.sub(r"[^A-Za-z0-9_-]", "-", run_name)[:60]
    values = {
        "RUN": run_name.replace("\n", " "),
        "PROJECT": project,
        "SSHC": shlex.quote(str(ssh_config)),
        "HOST": ssh_host,
        "FILE": shlex.quote(str(final)),
        "OUT": shlex.quote(str(out)),
        "APPROVED": str(approved),
        "SHA": file_sha256(final),
        "APP": remote_app,
        "ENV": remote_env,
        "RTMP": f"/tmp/curate-{tag}.jsonl",
    }
    text = APPLY_TEMPLATE
    for k, v in values.items():
        text = text.replace(f"@@{k}@@", v)
    assert "@@" not in text
    return text


__all__ = ["APPLY_TEMPLATE", "HISTORY_POLICY_HEADING", "apply_script", "file_sha256", "write_bundle"]
