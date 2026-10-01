#!/usr/bin/env python3
"""Active-librarian ceiling experiment harness (PLAN §3). One entry point, run from the repo root:

    uv run --frozen python eval/active/al.py <command> [...]

Commands, in order (see eval/active/restore.md for the database):

    build --exp E0|E1|E2|E3|all [--dry-run-dir DIR]   packets from the restored hlm_al_ceiling (read-only)
    build --exp E4                                     lessons v2: episodes -> E5 clusters -> packets
    mustknow-kit                                       inputs for the independent must-know writer (E2)
    estimate                                           Gemini cost per experiment from the packet sizes
    prereg [--force] [--exp E4]                        PREREG.md (E4: PREREG-E4.md) + .sha256 (before ANY arm)
    run --exp E1|E2|E3 --arm gemini|opus [--run N] [--packets ID,ID] [--retry-failed]
        [--retry-reason ProviderUnavailable|budget_stop|...]   (repeatable; only packets whose final
                                                                status is that reason; history kept)
    amend --field spend_cap_usd --value 12.00 --reason "..."   documented post-registration cap raise
    check --exp E1|E2|E3                               deterministic grounding of every saved output
    kit --exp E0|E1|E2|E3                              blind grading kit + key (outside the kit)
    split --exp E0|E1|E2|E3                            reader C file: the units readers A and B split on
    score --exp E0|E1|E2|E3 [--allow-incomplete]       the pre-registered go/no-go
    status                                             what exists, spend so far

Private outputs go to $HLM_AL_PRIVATE_DIR (default docs/private/active-librarian/, gitignored).
E4 is a separate suite (config-e4.json, READER-INSTRUCTIONS-E4.md, PREREG-E4.md): its private
directory is $HLM_AL_E4_PRIVATE_DIR (default docs/private/lessons-v2/e4/); E4 is never part of "all".
$HLM_AL_ENV_FILE overrides the provider key file (e.g. the main checkout's .env from a worktree).
The database is $HLM_AL_DSN (default postgresql://hlm:hlm@127.0.0.1:55432/hlm_al_ceiling).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import al_common as C  # noqa: E402


def _print(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n")


def _exps(value: str, allowed: tuple[str, ...]) -> list[str]:
    exps = list(allowed) if value == "all" else [x.strip().upper() for x in value.split(",") if x.strip()]
    if C.E4 in [x.strip().upper() for x in value.split(",")] and value.strip().upper() != C.E4:
        raise C.HarnessError("E4 is a separate suite: run it alone (--exp E4)")
    allowed = (*allowed, C.E4)
    bad = [e for e in exps if e not in allowed]
    if bad:
        raise C.HarnessError(f"unknown experiment(s) {bad}; one of {allowed}")
    return exps


async def cmd_build(args: argparse.Namespace) -> int:
    import al_packets as P

    if args.exp.strip().upper() == C.E4:
        import al_e4

        packets = al_e4.build_e4(C.load_config_for(C.E4))
        summary = C.read_json(al_e4.clusters_path())["summary"]
        _print({"exp": C.E4, "packets": len(packets), **summary})
        return 0
    cfg = C.load_config()
    conn = await C.connect_ro(args.dsn)
    try:
        for exp in _exps(args.exp, C.EXPERIMENTS):
            if exp == "E1":
                out = await P.build_e1(conn, cfg, Path(args.dry_run_dir) if args.dry_run_dir else None)
            else:
                out = await P.BUILDERS[exp](conn, cfg)
            manifest = C.read_json(C.packets_dir(exp) / "manifest.json")
            tokens = [r.get("input_tokens") or 0 for r in manifest.get("packets", [])]
            _print({"exp": exp, "packets_or_units": len(out), "input_tokens_total": sum(tokens) or None})
    finally:
        await conn.close()
    return 0


def cmd_mustknow_kit(_args: argparse.Namespace) -> int:
    import al_packets as P

    prompt = (C.PROMPT_DIR / "mustknow.md").read_text(encoding="utf-8")
    cfg = C.load_config()
    kit = C.pdir("mustknow", "kit")
    n = 0
    for pk in P.load_packets("E2"):
        body = prompt.replace("{N}", str(cfg["selection"]["E2"]["must_know"])).replace(
            "{PACKET}", pk["packet_id"]
        )
        sources = "\n\n".join(P.render_source(s) for s in pk["sources"])
        C.write_text(
            kit / f"{pk['packet_id']}.txt", f"{body}\n\nPROJECT: {pk['project']}\n\nSOURCES:\n\n{sources}\n"
        )
        n += 1
    _print(
        {"kit": str(kit), "packets": n, "write_to": str(C.private_dir() / "mustknow" / "<packet_id>.json")}
    )
    return 0


def cmd_estimate(args: argparse.Namespace) -> int:
    import al_arms as A
    import al_packets as P

    from hlmemo.core.budget import Meter
    from hlmemo.librarian.profiles import named_profile

    suite = C.suite_of(args.exp.strip().upper() if args.exp else None)
    cfg = C.load_config_for(suite.exps[0])
    prof = named_profile(cfg["arms"]["gemini"]["profile"])
    runs = int(cfg["arms"]["gemini"]["runs"])
    meter = Meter()
    out: dict[str, Any] = {"profile": prof.name, "runs": runs, "experiments": {}}
    total_exp = total_worst = Decimal(0)
    for exp in suite.arm_exps:
        packets = P.load_packets(exp)
        spec = A.task_spec(exp, cfg)
        tin = sum(meter.count_text(spec.system) + meter.count_text(p["user"]) + 11 for p in packets)
        tout = int(cfg["expected_output_tokens"][exp]) * len(packets)
        expected = prof.cost_usd(tin, tout)
        worst = A.worst_case_usd(prof, packets, spec)
        out["experiments"][exp] = {
            "packets": len(packets),
            "input_tokens": tin,
            "expected_output_tokens": tout,
            "expected_usd_per_run": round(float(expected), 4),
            "worst_usd_per_run": round(float(worst), 4),
            "expected_usd_all_runs": round(float(expected * runs), 4),
            "worst_usd_all_runs": round(float(worst * runs), 4),
        }
        total_exp += expected * runs
        total_worst += worst * runs
    out["total_expected_usd"] = round(float(total_exp), 4)
    out["total_worst_usd_first_attempts"] = round(float(total_worst), 4)
    out["spend_cap_usd"] = cfg["spend_cap_usd"]
    out["spent_so_far_usd"] = str(A.spent_usd())
    _print(out)
    return 0


def cmd_prereg(args: argparse.Namespace) -> int:
    import al_prereg as R

    path, digest = R.write(force=args.force, suite=C.suite_of(args.exp.strip().upper() if args.exp else None))
    _print({"prereg": str(path), "sha256": digest})
    return 0


def cmd_amend(args: argparse.Namespace) -> int:
    import al_prereg as R

    suite = C.suite_of(args.exp.strip().upper() if args.exp else None)
    path, digest = R.amend(args.field, args.value, args.reason, suite=suite)
    _print({"amendment": str(path), "sha256": digest})
    return 0


def _verified(exp: str | None) -> dict[str, Any]:
    import al_prereg as R

    record, digest = R.verify(exp)
    record["_sha"] = digest
    return record


async def cmd_run(args: argparse.Namespace) -> int:
    import al_arms as A
    import al_packets as P

    exp = _exps(args.exp, C.ARM_EXPERIMENTS)[0]
    record = _verified(exp)  # refuses without a matching pre-registration
    cfg = C.load_config_for(exp)
    packets = P.load_packets(exp)
    if args.packets:
        wanted = {x.strip() for x in args.packets.split(",")}
        packets = [p for p in packets if p["packet_id"] in wanted]
    runs = int(record["arms"][args.arm]["runs"])
    if not 1 <= args.run <= runs:
        raise C.HarnessError(f"--run must be 1..{runs} for {args.arm} (pre-registered)")
    if args.arm == "gemini":
        res = await A.run_gemini(
            exp,
            packets,
            cfg,
            run=args.run,
            prereg_sha=record["_sha"],
            retry_failed=args.retry_failed,
            retry_reasons=args.retry_reason,
        )
    else:
        res = A.run_opus(
            exp,
            packets,
            cfg,
            run=args.run,
            prereg_sha=record["_sha"],
            retry_failed=args.retry_failed,
            retry_reasons=args.retry_reason,
        )
    _print(res)
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    import al_grading as G
    import al_grounding as GR
    import al_packets as P

    for exp in _exps(args.exp, C.ARM_EXPERIMENTS):
        cfg = C.load_config_for(exp)
        record = _verified(exp)
        packets = P.load_packets(exp)
        card_tokens = int(cfg["selection"].get("E2", {}).get("card_tokens", 512))
        for label in G.run_labels(record):
            if not (C.private_dir() / "outputs" / exp / label).is_dir():
                continue
            res = GR.check_run(exp, label, packets, card_tokens=card_tokens)
            C.write_json(C.pdir("checks", exp) / f"{label}.json", res)
            _print({"exp": exp, "run": label, **res["totals"]})
    return 0


def cmd_kit(args: argparse.Namespace) -> int:
    import al_grading as G
    import al_packets as P

    for exp in _exps(args.exp, C.EXPERIMENTS):
        record = _verified(exp)
        packets = [] if exp == "E0" else P.load_packets(exp)
        _print({"exp": exp, **G.build_kit(exp, record, packets)})
    return 0


def cmd_split(args: argparse.Namespace) -> int:
    import al_grading as G

    for exp in _exps(args.exp, C.EXPERIMENTS):
        _print({"exp": exp, **G.build_split(exp, _verified(exp))})
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    import al_grading as G

    for exp in _exps(args.exp, C.EXPERIMENTS):
        res = G.score(exp, _verified(exp), allow_incomplete=args.allow_incomplete)
        C.write_json(C.pdir("scores") / f"{exp}.json", res)
        C.write_text(C.pdir("scores") / f"{exp}.md", G.render_score(res))
        _print({"exp": exp, "verdict": res["verdict"]})
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    import al_arms as A

    base = C.private_dir()
    out: dict[str, Any] = {
        "private_dir": str(base),
        "prereg": (base / "PREREG.md").is_file(),
        "prereg_e4": (base / C.E4_SUITE.prereg).is_file(),
    }
    for exp in C.ALL_EXPERIMENTS:
        m = base / "packets" / exp / "manifest.json"
        out[exp] = {"packets": m.is_file()}
        od = base / "outputs" / exp
        if od.is_dir():
            out[exp]["runs"] = {
                d.name: sum(1 for f in d.glob("*.json") if not f.name.endswith(".raw.json"))
                for d in od.iterdir()
            }
    out["gemini_spent_usd"] = str(A.spent_usd())
    _print(out)
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="al.py", description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--exp", default="all")
    b.add_argument("--dsn", default=None)
    b.add_argument("--dry-run-dir", default=None, help="dry-run capture payloads (*.json) to add to E1")
    sub.add_parser("mustknow-kit")
    e = sub.add_parser("estimate")
    e.add_argument("--exp", default="")
    p = sub.add_parser("prereg")
    p.add_argument("--force", action="store_true")
    p.add_argument("--exp", default="", help="E4: the separate E4 pre-registration")
    r = sub.add_parser("run")
    r.add_argument("--exp", required=True)
    r.add_argument("--arm", required=True, choices=("gemini", "opus"))
    r.add_argument("--run", type=int, default=1)
    r.add_argument("--packets", default="")
    r.add_argument("--retry-failed", action="store_true")
    r.add_argument("--retry-reason", action="append", default=[], metavar="REASON")
    a = sub.add_parser("amend")
    a.add_argument("--field", required=True)
    a.add_argument("--value", required=True)
    a.add_argument("--reason", required=True)
    a.add_argument("--exp", default="", help="E4: amend config-e4.json under PREREG-E4")
    for name in ("check", "kit", "split"):
        s = sub.add_parser(name)
        s.add_argument("--exp", required=True)
    s = sub.add_parser("score")
    s.add_argument("--exp", required=True)
    s.add_argument("--allow-incomplete", action="store_true")
    st = sub.add_parser("status")
    st.add_argument("--exp", default="")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    handlers = {
        "build": cmd_build,
        "mustknow-kit": cmd_mustknow_kit,
        "estimate": cmd_estimate,
        "prereg": cmd_prereg,
        "amend": cmd_amend,
        "run": cmd_run,
        "check": cmd_check,
        "kit": cmd_kit,
        "split": cmd_split,
        "score": cmd_score,
        "status": cmd_status,
    }
    try:
        if str(getattr(args, "exp", "") or "").strip().upper() == C.E4:
            C.use_e4_private()  # E4 never shares the E0-E3 private directory, PREREG or ledger
        res = handlers[args.cmd](args)
        return asyncio.run(res) if asyncio.iscoroutine(res) else int(res)
    except C.HarnessError as exc:
        sys.stderr.write(f"refused: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
