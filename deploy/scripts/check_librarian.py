#!/usr/bin/env python3
"""R2 librarian checks (D-058 observer, D-062, D-071). One file, four modes; never prints a key.

collect --service librarian|api [--probe] [--wait-heartbeat S]    INSIDE a release container,
    fed on stdin like check_edge.py (`docker compose exec -T SERVICE python - collect ... <
    check_librarian.py`): the effective switch/role/mode/profiles, whether each profile's key is
    SET (a boolean, never the value), the librarian heartbeat with its age and bound (3 x
    HLM_LIBRARIAN_HEARTBEAT_S; a missing/stale one is re-read for up to S seconds, for the first
    heartbeat after cutover) or the api's risk-judge chain. --probe GETs each profile's base URL
    without credentials: reachability only. One JSON line on stdout.
evaluate --llm-env present|absent --librarian FILE --api FILE     on the HOST (remote-deploy.sh,
    after cutover). llm.env present = the R2 configuration, validated strictly (Sol 48): exit 1
    unless the api settings, the librarian settings AND the heartbeat all say enabled=true, role
    observer, and both settings say HLM_LLM_MODE=live; unless the heartbeat is fresh (Sol 49: not
    older than 3 heartbeat intervals, 30 s by default); unless the api's risk-judge chain loads
    and is non-empty; and unless every profile key is set. llm.env absent = R1-style: PASS only
    while everything idles. An unreachable provider is REPORTED, never fatal (jobs wait,
    risk_check answers retrieval-only).
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
#: Sol 49: a heartbeat older than this many heartbeat intervals is stale (default 3 x 10 s = 30 s).
HEARTBEAT_STALE_FACTOR = 3.0
DEFAULT_HEARTBEAT_INTERVAL_S = 10.0


def emit(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, sort_keys=True) + "\n")


def read_heartbeat(path: Any) -> dict[str, Any]:
    """The heartbeat's reported fields plus its age in seconds, or ``{"error": <type>}``."""
    try:
        hb = json.loads(Path(str(path)).read_text(encoding="utf-8"))
        out = {k: hb.get(k) for k in HEARTBEAT_KEYS}
        out["age_s"] = round(time.time() - float(hb.get("ts", 0)), 1)
        return out
    except (OSError, TypeError, ValueError, AttributeError) as exc:
        return {"error": type(exc).__name__}


def wait_for_heartbeat(path: Any, max_age_s: float, wait_s: float) -> tuple[dict[str, Any], float]:
    """Read the heartbeat; while it is missing or older than ``max_age_s``, re-read it every second
    for up to ``wait_s`` (the first heartbeat after cutover). Returns (heartbeat, waited seconds)."""
    start = time.monotonic()
    while True:
        hb = read_heartbeat(path)
        fresh = "error" not in hb and hb["age_s"] <= max_age_s
        waited = time.monotonic() - start
        if fresh or waited >= wait_s:
            return hb, round(waited, 1)
        time.sleep(min(1.0, max(0.0, wait_s - waited)))


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


def collect(service: str, with_probe: bool, wait_heartbeat_s: float = 0.0) -> int:
    from hlmemo.config import get_settings
    from hlmemo.librarian.profiles import describe_chains, profile_chain

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
        # D-094: the per-task fallbacks too (each needs its key and a reachable endpoint)
        extra = [p for p in chain[0].task_fallbacks.values() if p is not None]
        chain = list({p.name: p for p in [*chain, *extra]}.values())
        out["profiles"] = [
            {"name": p.name, "base_url": p.base_url, "key_set": bool(p.api_key)} for p in chain
        ]
        tasks = describe_chains(s).get("tasks", {})
        out["fallbacks"] = {t: e["fallback"] for t, e in tasks.items() if e["source"] == "task"}
    except Exception as exc:  # noqa: BLE001
        chain = []
        out["profiles_error"] = type(exc).__name__
    if with_probe:
        for entry, profile in zip(out.get("profiles", []), chain, strict=False):
            entry["probe"] = probe(profile.base_url)
    if service == "librarian":
        # Read last (after the probes), waiting briefly for a fresh one (first heartbeat after cutover).
        interval = float(s.librarian_heartbeat_s)
        max_age = HEARTBEAT_STALE_FACTOR * interval
        out["heartbeat"], out["heartbeat_waited_s"] = wait_for_heartbeat(
            s.librarian_heartbeat_file, max_age, wait_heartbeat_s
        )
        out.update(heartbeat_interval_s=interval, heartbeat_max_age_s=max_age)
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


#: The R2 configuration (D-058): every source must agree on exactly these values.
R2_ENABLED, R2_MODE, R2_ROLE = True, "live", "observer"


def _heartbeat_bound(lib: dict[str, Any]) -> tuple[float, float]:
    """(interval, max age) from the librarian report; defaults 10 s and 3 x 10 s."""
    interval = float(lib.get("heartbeat_interval_s") or DEFAULT_HEARTBEAT_INTERVAL_S)
    return interval, float(lib.get("heartbeat_max_age_s") or HEARTBEAT_STALE_FACTOR * interval)


def _r2_failures(lib: dict[str, Any], api: dict[str, Any], hb: dict[str, Any]) -> list[str]:
    """llm.env present: the api settings, the librarian settings and the heartbeat must all be
    enabled/live/observer, the api's risk judge must load with a non-empty chain, keys set."""
    failures = []
    for name, rep in (("api", api), ("librarian", lib)):
        if rep.get("enabled") is not R2_ENABLED:
            failures.append(f"{name} settings: HLM_LIBRARIAN_ENABLED={rep.get('enabled')} (R2: true)")
        if rep.get("llm_mode") != R2_MODE:
            failures.append(f"{name} settings: HLM_LLM_MODE={rep.get('llm_mode')} (R2: {R2_MODE})")
        if rep.get("role") != R2_ROLE:
            failures.append(f"{name} settings: HLM_LIBRARIAN_ROLE={rep.get('role')} (R2: {R2_ROLE})")
        if rep.get("profiles_error"):
            failures.append(f"{name}: provider profiles do not load ({rep['profiles_error']})")
        missing = [p.get("name") for p in rep.get("profiles") or [] if not p.get("key_set")]
        if missing:
            failures.append(
                f"{name}: provider key missing for {','.join(map(str, missing))} (install_llm_env.sh)"
            )
    if "error" in hb:
        failures.append(
            f"no librarian heartbeat ({hb['error']};"
            f" waited {lib.get('heartbeat_waited_s', 0)}s after cutover)"
        )
    else:
        if hb.get("enabled") is not R2_ENABLED:
            failures.append(f"heartbeat enabled={hb.get('enabled')} (R2: True)")
        if hb.get("role") != R2_ROLE:
            failures.append(f"heartbeat role={hb.get('role')} (R2: {R2_ROLE})")
        interval, max_age = _heartbeat_bound(lib)
        age = hb.get("age_s")
        if not isinstance(age, (int, float)) or age > max_age:
            failures.append(
                f"heartbeat stale: {age}s old > {max_age:g}s ({HEARTBEAT_STALE_FACTOR:g} x the {interval:g}s"
                f" interval; waited {lib.get('heartbeat_waited_s', 0)}s after cutover): the librarian loop is"
                " stuck, crashing or held by one long job (stack.sh logs librarian)"
            )
    if api.get("risk_judge_error"):
        failures.append(f"api risk-judge configuration error ({api['risk_judge_error']})")
    elif not api.get("risk_judge"):
        failures.append("api risk-judge chain is empty (no qualified profile: risk_check would never judge)")
    return failures


def evaluate(llm_env: str, librarian_path: str, api_path: str) -> int:
    lib, api = _load(librarian_path), _load(api_path)
    failures: list[str] = []
    if lib is None or api is None:
        missing = [n for n, r in (("librarian", lib), ("api", api)) if r is None]
        print(f"RESULT librarian FAIL no usable report from {'/'.join(missing)} (check_librarian.py collect)")
        return 1
    hb = lib.get("heartbeat") or {}
    print(
        f"librarian: llm.env={llm_env} enabled={str(lib.get('enabled')).lower()} role={lib.get('role')} "
        f"mode={lib.get('llm_mode')} profile={lib.get('profile')} fallback={lib.get('fallback')}"
    )
    if lib.get("fallbacks"):  # D-094 per-task overrides (HLM_FALLBACK_PROFILE__<TASK>)
        print("per-task fallbacks: " + " ".join(f"{t}={p or '-'}" for t, p in sorted(lib["fallbacks"].items())))
    if "error" in hb:
        print(f"librarian heartbeat: unreadable ({hb['error']})")
    else:
        print(
            "librarian heartbeat: "
            + " ".join(f"{k}={hb.get(k)}" for k in ("enabled", "role", "breaker_state", "age_s", "ready"))
            + f" (max age {_heartbeat_bound(lib)[1]:g}s, waited {lib.get('heartbeat_waited_s', 0)}s)"
        )
        print(
            "librarian spend: "
            + " ".join(f"{k}={hb.get(k)}" for k in ("spend_hour_usd", "spend_today_usd", "reserved_usd"))
        )
    judge = api.get("risk_judge_error") and f"ERROR {api['risk_judge_error']}"
    print(
        f"api: enabled={str(api.get('enabled')).lower()} role={api.get('role')} mode={api.get('llm_mode')} "
        f"risk_judge={judge or ','.join(api.get('risk_judge') or []) or '-'}"
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
    if (api.get("enabled"), api.get("llm_mode"), api.get("role")) != (
        lib.get("enabled"),
        lib.get("llm_mode"),
        lib.get("role"),
    ):
        failures.append(
            "api and librarian settings differ (HLM_LIBRARIAN_ENABLED/HLM_LLM_MODE/ROLE: llm.env mount)"
        )
    if llm_env == "absent":
        # R1-style: no key, no switch. PASS only while everything idles.
        active = [
            name
            for name, on in (
                ("api", api.get("enabled") is True and api.get("llm_mode") != "off"),
                ("librarian", lib.get("enabled") is True and lib.get("llm_mode") != "off"),
                ("heartbeat", hb.get("enabled") is True),
            )
            if on
        ]
        if active:
            failures.append(
                f"librarian enabled without llm.env ({','.join(active)};"
                " the key and the budget guard live there)"
            )
        else:
            print("librarian: idle (no llm.env): no job leased, no provider call, risk_check retrieval-only")
    else:
        # R2: llm.env present. Anything but enabled/live/observer everywhere is a failed release
        # (to deploy with the librarian off, remove llm.env first: install_llm_env.sh --remove).
        failures += _r2_failures(lib, api, hb)
    if failures:
        print("RESULT librarian FAIL " + "; ".join(failures))
        return 1
    if llm_env == "absent":
        print("RESULT librarian PASS llm.env=absent enabled=false (idle)")
    else:
        print(
            f"RESULT librarian PASS llm.env=present enabled=true mode={R2_MODE} role={R2_ROLE}"
            f" risk_judge={','.join(api['risk_judge'])}"
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
    c.add_argument(
        "--wait-heartbeat",
        type=float,
        default=0.0,
        help="librarian: re-read a missing/stale heartbeat for up to S seconds (first one after cutover)",
    )
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
        return collect(args.service, args.probe, args.wait_heartbeat)
    if args.mode == "evaluate":
        return evaluate(args.llm_env, args.librarian, args.api)
    if args.mode == "job":
        return job(args.version_id, args.wait)
    return observer_gate(args.job, args.audit, args.version_id)


if __name__ == "__main__":
    sys.exit(main())
