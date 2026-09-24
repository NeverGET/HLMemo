#!/usr/bin/env python3
"""R2 librarian checks (D-058 observer, D-062, D-071). One file, four modes; never prints a key.

collect --service librarian|api [--probe]    INSIDE a release container, fed on stdin like
    check_edge.py (`docker compose exec -T SERVICE python - collect ... < check_librarian.py`):
    the effective switch/role/mode/profiles, whether each profile's key is SET (a boolean, never
    the value), the librarian heartbeat (librarian) or the risk-judge chain (api). --probe GETs
    each profile's base URL without credentials: reachability only. One JSON line on stdout.
evaluate --llm-env present|absent --librarian FILE --api FILE     on the HOST (remote-deploy.sh,
    after cutover). Exit 1 when llm.env enables the librarian but the heartbeat is not enabled
    with the configured role (R2: observer), when the api does not see the same switch (the risk
    judge and the W2b enqueue run there) or a provider key is missing. An unreachable provider
    is REPORTED, never fatal (jobs wait, risk_check answers retrieval-only).
job --version-id V [--wait S]                INSIDE the api container (remote_gates.sh
    --librarian): waits for the librarian_write job(s) of V's source event, then prints their
    status, each job's librarian event (role, outcome, mutation ops, questions) and whether V has
    a version_signals row. Read-only session.
observer-gate --job FILE --audit FILE --version-id V     on the operator WORKSTATION: the
    --librarian gate verdict from `job` and `hlmemo.ops librarian audit --project P --json`:
    jobs done, role observer, zero link/close mutations (invalidations), version_signals present,
    no proposal of the job applied. Prints one `RESULT librarian PASS|FAIL ...` line.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

HEARTBEAT_KEYS = (
    "enabled",
    "role",
    "breaker_state",
    "ready",
    "in_flight",
    "failed_24h",
    "spend_today_usd",
    "spend_hour_usd",
    "reserved_usd",
)
#: librarian event mutation ops that are NOT allowed while the role is observer (D-058, W2b):
#: links and bi-temporal closes (invalidations). Only placement (signal_upsert) may apply.
OBSERVER_ALLOWED_OPS = {"signal_upsert"}


def emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, sort_keys=True) + "\n")


# ------------------------------------------------------------------ collect (in a container)
def probe(url: str, timeout: float = 5.0) -> dict[str, Any]:
    """Reachability of a provider endpoint: any HTTP answer counts; no header carries a key."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "hlmemo-deploy-check/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (profile URL)
            return {"reachable": True, "status": response.status}
    except urllib.error.HTTPError as exc:
        return {"reachable": True, "status": exc.code}
    except Exception as exc:  # noqa: BLE001 - DNS, TLS, timeout, refused: all mean unreachable
        return {"reachable": False, "error": type(exc).__name__}


def collect(service: str, with_probe: bool) -> int:
    from hlmemo.config import get_settings
    from hlmemo.librarian.profiles import profile_chain

    out: dict[str, Any] = {"service": service}
    try:
        s = get_settings()
    except Exception as exc:  # noqa: BLE001 - the type only: a validation message could quote a value
        emit({**out, "error": f"settings: {type(exc).__name__}"})
        return 0
    out.update(
        enabled=bool(s.librarian_enabled),
        role=s.librarian_role,
        llm_mode=s.llm_mode,
        profile=s.profile,
        fallback=s.fallback_profile,
    )
    try:
        chain = profile_chain(s)
        out["profiles"] = [
            {"name": p.name, "base_url": p.base_url, "key_set": bool(p.api_key)} for p in chain
        ]
    except Exception as exc:  # noqa: BLE001
        chain = []
        out["profiles_error"] = type(exc).__name__
    if with_probe:
        for entry, profile in zip(out.get("profiles", []), chain, strict=False):
            entry["probe"] = probe(profile.base_url)
    if service == "librarian":
        path = s.librarian_heartbeat_file
        try:
            hb = json.loads(Path(str(path)).read_text(encoding="utf-8"))
            out["heartbeat"] = {k: hb.get(k) for k in HEARTBEAT_KEYS}
            out["heartbeat"]["age_s"] = round(time.time() - float(hb.get("ts", 0)), 1)
        except (OSError, TypeError, ValueError) as exc:
            out["heartbeat"] = {"error": type(exc).__name__}
    else:
        try:
            from hlmemo.librarian.risk_judge import judge_chain

            out["risk_judge"] = [p.name for p in judge_chain(s)]
        except Exception as exc:  # noqa: BLE001
            out["risk_judge_error"] = type(exc).__name__
    emit(out)
    return 0


# ------------------------------------------------------------------ evaluate (on the host)
def _load(path: str) -> dict[str, Any] | None:
    try:
        text = Path(path).read_text(encoding="utf-8").strip().splitlines()
        data = json.loads(text[-1]) if text else None
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and "error" not in data else None


def evaluate(llm_env: str, librarian_path: str, api_path: str) -> int:
    lib, api = _load(librarian_path), _load(api_path)
    failures: list[str] = []
    if lib is None or api is None:
        missing = [n for n, r in (("librarian", lib), ("api", api)) if r is None]
        print(f"RESULT librarian FAIL no usable report from {'/'.join(missing)} (check_librarian.py collect)")
        return 1
    hb = lib.get("heartbeat") or {}
    on = bool(lib.get("enabled")) and lib.get("llm_mode") != "off"
    print(
        f"librarian: llm.env={llm_env} enabled={str(lib.get('enabled')).lower()} role={lib.get('role')} "
        f"mode={lib.get('llm_mode')} profile={lib.get('profile')} fallback={lib.get('fallback')}"
    )
    if "error" in hb:
        print(f"librarian heartbeat: unreadable ({hb['error']})")
    else:
        print(
            "librarian heartbeat: "
            + " ".join(f"{k}={hb.get(k)}" for k in ("enabled", "role", "breaker_state", "age_s", "ready"))
        )
        print(
            "librarian spend: "
            + " ".join(f"{k}={hb.get(k)}" for k in ("spend_hour_usd", "spend_today_usd", "reserved_usd"))
        )
    print(
        f"api: enabled={str(api.get('enabled')).lower()} mode={api.get('llm_mode')} "
        f"risk_judge={','.join(api.get('risk_judge') or []) or '-'}"
        " (empty: risk_check answers retrieval-only)"
    )
    for profile in lib.get("profiles") or []:
        pr = profile.get("probe")
        if pr is None:
            state = "not probed"
        elif pr.get("reachable"):
            state = f"reachable (HTTP {pr.get('status')})"
        else:
            state = f"UNREACHABLE ({pr.get('error')}); reported only: jobs wait, risk_check is retrieval-only"
        key = "set" if profile.get("key_set") else "MISSING"
        print(f"provider {profile.get('name')} {profile.get('base_url')}: key {key}, {state}")
    if lib.get("profiles_error"):
        failures.append(f"provider profiles do not load ({lib['profiles_error']})")
    if bool(api.get("enabled")) != bool(lib.get("enabled")) or api.get("llm_mode") != lib.get("llm_mode"):
        failures.append("api and librarian see different HLM_LIBRARIAN_ENABLED/HLM_LLM_MODE (llm.env mount)")
    if llm_env == "absent":
        if on:
            failures.append("librarian enabled without llm.env (the key and the budget guard live there)")
        else:
            print("librarian: idle (no llm.env): no job leased, no provider call, risk_check retrieval-only")
    elif not on:
        print("librarian: switched off in llm.env (HLM_LIBRARIAN_ENABLED=false or HLM_LLM_MODE=off): idle")
        if hb.get("enabled") is True:
            failures.append("heartbeat says enabled although llm.env switches the librarian off")
    else:
        if "error" in hb:
            failures.append("no librarian heartbeat")
        else:
            if hb.get("enabled") is not True:
                failures.append(
                    f"heartbeat enabled={hb.get('enabled')} although llm.env enables the librarian"
                )
            if hb.get("role") != lib.get("role"):
                failures.append(f"heartbeat role={hb.get('role')}, configured {lib.get('role')}")
        if lib.get("role") != "observer":
            print(
                f"librarian: role {lib.get('role')} is above observer:"
                " it needs the owner's decision event (D-062)"
            )
        missing = [p.get("name") for p in lib.get("profiles") or [] if not p.get("key_set")]
        if missing:
            failures.append(f"provider key missing for {','.join(map(str, missing))} (install_llm_env.sh)")
    if failures:
        print("RESULT librarian FAIL " + "; ".join(failures))
        return 1
    print(
        f"RESULT librarian PASS llm.env={llm_env} enabled={str(on).lower()}"
        f" role={hb.get('role') or lib.get('role')}"
    )
    return 0


# ------------------------------------------------------------------ job (in the api container)
def job(version_id: int, wait_s: float) -> int:
    import uuid

    import psycopg

    from hlmemo.config import get_settings
    from hlmemo.librarian.events import NS_LIBRARIAN

    s = get_settings()
    out: dict[str, Any] = {"version_id": version_id}
    started = time.monotonic()
    with psycopg.connect(s.db_dsn, autocommit=True, connect_timeout=10) as conn:
        conn.execute("SET default_transaction_read_only = on")
        conn.execute("SET statement_timeout = '10s'")
        row = conn.execute(
            "SELECT source_event_id FROM memory_versions WHERE version_id = %s", (version_id,)
        ).fetchone()
        if row is None:
            emit({**out, "error": "unknown version"})
            return 0
        key = f"librarian_write:{row[0]}"
        out["source_event_id"] = int(row[0])
        while True:
            jobs = conn.execute(
                "SELECT dedupe_key, status, attempts, last_error FROM jobs"
                " WHERE dedupe_key = %s OR dedupe_key LIKE %s ORDER BY job_id",
                (key, key + ":%"),
            ).fetchall()
            settled = bool(jobs) and all(j[1] in ("done", "failed") for j in jobs)
            if settled or time.monotonic() - started >= wait_s:
                break
            time.sleep(2)
        out["waited_s"] = round(time.monotonic() - started, 1)
        out["jobs"] = [{"key": k, "status": st, "attempts": a, "last_error": e} for k, st, a, e in jobs]
        events = []
        for dedupe_key, *_ in jobs:
            ev = conn.execute(
                "SELECT event_id, payload->'resolved' FROM events"
                " WHERE kind = 'librarian' AND request_id = %s",
                (uuid.uuid5(NS_LIBRARIAN, f"job:{dedupe_key}"),),
            ).fetchone()
            if ev is None:
                continue
            resolved = ev[1] or {}
            events.append(
                {
                    "job": dedupe_key,
                    "event_id": int(ev[0]),
                    "role": resolved.get("role"),
                    "outcome": resolved.get("outcome"),
                    "mutations": dict(Counter(str(m.get("op")) for m in resolved.get("mutations") or [])),
                    "questions": len(resolved.get("questions") or []),
                }
            )
        out["events"] = events
        if conn.execute("SELECT to_regclass('version_signals')").fetchone()[0] is None:
            out["version_signals"] = None
        else:
            hit = conn.execute(
                "SELECT 1 FROM version_signals WHERE version_id = %s", (version_id,)
            ).fetchone()
            out["version_signals"] = hit is not None
    emit(out)
    return 0


# ------------------------------------------------------------------ observer-gate (workstation)
def observer_gate(job_path: str, audit_path: str, version_id: int) -> int:
    def fail(msg: str) -> int:
        print(f"RESULT librarian FAIL {msg}")
        return 1

    try:
        report = json.loads(Path(job_path).read_text(encoding="utf-8").strip().splitlines()[-1])
    except (OSError, ValueError, IndexError):
        return fail("no job report from check_librarian.py job")
    if report.get("error"):
        return fail(f"job report: {report['error']}")
    jobs, events = report.get("jobs") or [], report.get("events") or []
    if not jobs:
        return fail(
            f"no librarian_write job for event {report.get('source_event_id')}"
            f" after {report.get('waited_s')}s "
            "(api enqueue off? llm.env must reach the api with HLM_LIBRARIAN_ENABLED=true)"
        )
    pending = [j for j in jobs if j.get("status") != "done"]
    if pending:
        detail = ", ".join(
            f"{j['key']}={j['status']}/{j.get('attempts')} {j.get('last_error') or ''}".strip()
            for j in pending
        )
        return fail(f"job(s) not done after {report.get('waited_s')}s: {detail}")
    if len(events) != len(jobs):
        return fail("a done job has no librarian event (D-062: one event per job)")
    roles = sorted({str(e.get("role")) for e in events})
    ops: Counter[str] = Counter()
    for e in events:
        ops.update(e.get("mutations") or {})
    forbidden = {op: n for op, n in ops.items() if op not in OBSERVER_ALLOWED_OPS}
    if roles != ["observer"]:
        return fail(f"processed in role {','.join(roles)}, expected observer (D-058)")
    if forbidden:
        return fail(f"observer applied links/invalidations: {forbidden}")
    signals = report.get("version_signals")
    if signals is not True:
        absent = " (no version_signals table: release before W2b)" if signals is None else ""
        return fail(
            f"no version_signals row for v{version_id}{absent};"
            f" signal_upsert mutations={ops.get('signal_upsert', 0)}"
        )
    try:
        audit = json.loads(Path(audit_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fail("`hlmemo.ops librarian audit --json` output is not JSON")
    prefix = f"librarian_write:{report.get('source_event_id')}"
    mine = [
        p
        for p in audit.get("proposals") or []
        if str(p.get("job") or "").split(":", 2)[:2] == prefix.split(":")
    ]
    applied = [p.get("question_id") for p in mine if p.get("status") == "applied"]
    batches = {p.get("batch_id") for p in mine}
    applied_batches = [
        b.get("batch_id")
        for b in audit.get("batches") or []
        if b.get("batch_id") in batches and b.get("applied_at")
    ]
    if applied or applied_batches:
        return fail(f"audit shows applied proposals {applied} / batches {applied_batches} in observer mode")
    outcomes = ",".join(sorted({str(e.get("outcome")) for e in events}))
    print(
        f"RESULT librarian PASS v{version_id} {len(jobs)} job(s) done in {report.get('waited_s')}s;"
        " role=observer "
        f"outcome={outcomes}; mutations signal_upsert={ops.get('signal_upsert', 0)} links/closes=0; "
        f"version_signals=yes; audit proposals={len(mine)} "
        f"({', '.join(sorted({str(p.get('status')) for p in mine})) or 'none'}), applied=0"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--service", choices=("librarian", "api"), required=True)
    c.add_argument("--probe", action="store_true")
    e = sub.add_parser("evaluate")
    e.add_argument("--llm-env", choices=("present", "absent"), required=True)
    e.add_argument("--librarian", required=True)
    e.add_argument("--api", required=True)
    j = sub.add_parser("job")
    j.add_argument("--version-id", type=int, required=True)
    j.add_argument("--wait", type=float, default=240.0)
    g = sub.add_parser("observer-gate")
    g.add_argument("--job", required=True)
    g.add_argument("--audit", required=True)
    g.add_argument("--version-id", type=int, required=True)
    args = ap.parse_args(argv)
    if args.mode == "collect":
        return collect(args.service, args.probe)
    if args.mode == "evaluate":
        return evaluate(args.llm_env, args.librarian, args.api)
    if args.mode == "job":
        return job(args.version_id, args.wait)
    return observer_gate(args.job, args.audit, args.version_id)


if __name__ == "__main__":
    sys.exit(main())
