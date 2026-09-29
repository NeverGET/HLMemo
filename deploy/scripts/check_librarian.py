#!/usr/bin/env python3
"""R2/R3 librarian checks (D-058 observer, D-062, D-071, D-111, D-116). One file, four modes; never
prints a key.

collect --service librarian|api [--probe] [--wait-heartbeat S]    INSIDE a release container,
    fed on stdin like check_edge.py (`docker compose exec -T SERVICE python - collect ... <
    check_librarian.py`): the effective switch/role/mode/profiles, whether each profile's key is
    SET (a boolean, never the value), the librarian heartbeat with its age and bound (3 x
    HLM_LIBRARIAN_HEARTBEAT_S; a missing/stale one is re-read for up to S seconds, for the first
    heartbeat after cutover) or the api's risk-judge chain, the llm.env release marker and the
    RELEASE MANIFEST keys (flags and profile names only) the container runs with; R4: the effective
    research settings, the writer profile (its key SET or not, its price_valid_until) and whether
    the research tracer is on. --probe GETs each profile's base URL without credentials:
    reachability only. One JSON line on stdout.
evaluate --llm-env present|absent --librarian FILE --api FILE [--release r3|r4] [--llm-env-file F]
         [--writer-probe api|FILE] [--today YYYY-MM-DD]                          on the HOST
    (remote-deploy.sh, after cutover). llm.env present = the R2 configuration, validated strictly
    (Sol 48): exit 1 unless the api settings, the librarian settings AND the heartbeat all say
    enabled=true, role observer, and both settings say HLM_LLM_MODE=live; unless the heartbeat is
    fresh (Sol 49: not older than 3 heartbeat intervals, 30 s by default); unless the api's
    risk-judge chain loads and is non-empty; and unless every profile key is set. An unreachable
    provider is REPORTED, never fatal (jobs wait, risk_check answers retrieval-only).
    R3/R4 (D-108/D-111/D-116/D-121, review 79 T5; this checkout is CODE_RELEASE r4 and also runs an R3
    env: memory.ask and the Memory Map summaries default OFF): llm.env ABSENT always fails
    (the R1-style idle pass is gone). The llm.env is validated against the RELEASE MANIFEST
    (``RELEASE_MANIFESTS``), never a hard-coded flag: its "off" keys (R3: HLM_QUERY_REWRITE and
    HLM_RETRIEVAL_SOURCE_CAP) must be absent or false in every env; api and librarian must run the
    same value for EVERY manifest key and the release marker; with ``--llm-env-file`` the file on
    disk must be what both run. RELEASE MODE — ``--release rN`` (the post-switch verification,
    D-108 step 4) or a service running an llm.env labelled HLM_ENV_RELEASE=rN of a known manifest
    (install_llm_env.sh writes the template's label) — additionally requires that release's env
    (env_release=rN) with its EXACT profile mapping (D-094; R4 also the research and map-summary
    fallbacks), its "on" switches (R4: HLM_RESEARCH_ENABLED; the summaries are "off"), its
    per-question limits (R4) and the spend guard (D-121: every cap present,
    HLM_LLM_BUDGET_DISABLED=false, month <= 10, day and hour <= month; R4: _BUDGETS_R4, hour <= 3,
    day <= 8, month <= 60). The R3 manifest keeps the R4 switches off, so an R3 env on this image
    never serves memory.ask. R4 mode also pins the answer contract (prose, attribution llm, rerank
    llm), accepts the writer unset or one of R4_WRITERS, tracks the research limits across api,
    librarian and disk, and (``writer_checks``) FAILS when the api runs the research tracer (R-11),
    when the writer profile's price_valid_until has passed or is missing (R-5; a WARNING within 14
    days) or when ``--writer-probe api`` (python -m hlmemo.ops probe-writer in the api container,
    the api's own writer credential) does not pass (R-14; only its ok/profile/status/latency_ms
    fields are printed). With the writer UNSET the research PRIMARY (HLM_PROFILE) writes: the
    probe must then report that profile, and R-5 applies only when its profile file carries a
    price_valid_until. Only an UNLABELLED llm.env is
    the R2 env of the D-108 interim: every switch any manifest keeps off must be off, it passes the
    R2 checks and the result line says so; an unknown label fails.
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
import datetime
import json
import os
import re
import subprocess
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
#: D-111 (6): the release this checkout (and the image built from it) belongs to, and the llm.env
#: marker the R3 install_llm_env.sh writes; collect reports the marker each container runs with
CODE_RELEASE = "r4"
ENV_RELEASE_KEY = "HLM_ENV_RELEASE"
#: D-116: the RELEASE MANIFEST — what the llm.env of each release must say, EXACTLY (review 75
#: #7). R3 ships WITHOUT the query rewrite and the per-source cap ("off": absent or false in every
#: env) and with the D-094 profile mapping ("exact": these profile names and no others). A new
#: release adds its own entry; an env label no entry knows fails.
_D094 = {
    "HLM_PROFILE": "openrouter-gpt6-luna",
    "HLM_FALLBACK_PROFILE": "openrouter-glm53-flash",
    "HLM_FALLBACK_PROFILE__SYNTHESIS": "openrouter",
    "HLM_FALLBACK_PROFILE__QUERY_REWRITE": "openrouter",
    "HLM_FALLBACK_PROFILE__RISK_JUDGE": "openrouter-qwen38-27b-fast",
}
#: D-121: the spend guard stays ON; the operator may lower (or edit) the caps, never lift the month
#: above the owner's target; day and hour never above the month
_BUDGETS = {
    "keys": ("HLM_LLM_BUDGET_HOUR_USD", "HLM_LLM_BUDGET_DAY_USD", "HLM_LLM_BUDGET_MONTH_USD"),
    "disabled_key": "HLM_LLM_BUDGET_DISABLED",
    "month_max_usd": 10.0,
}
#: R4 plan §1.5 / §9 (owner, D-198): the caps of the R4 test month, every one bounded. A SEPARATE
#: entry (R-3): R3's _BUDGETS stays month <= 10, so an R4 cap change never leaks into the R3 manifest
#: and an R3 env never passes with R4 caps
_BUDGETS_R4 = {
    "keys": _BUDGETS["keys"],
    "disabled_key": _BUDGETS["disabled_key"],
    "month_max_usd": 60.0,
    #: the other caps' own upper bounds (the month's is month_max_usd)
    "max_usd": {"HLM_LLM_BUDGET_HOUR_USD": 3.0, "HLM_LLM_BUDGET_DAY_USD": 8.0},
}
#: D-136 / review 79 T5: memory.ask (api) and its Memory Map summaries (librarian); both default OFF
#: in the code, so only an env that says so turns them on
RESEARCH_SWITCHES = ("HLM_RESEARCH_ENABLED", "HLM_MAP_SUMMARY_ENABLED")
#: R4 plan §1.3/§1.5 (D-198): the research writer. The r4 manifest accepts the two Gemini profiles or
#: an UNSET writer (§6.2(b): the research profile, luna, writes; no Gemini key needed)
WRITER_KEY = "HLM_RESEARCH_WRITER_PROFILE"
R4_WRITERS = ("google-gemini38-flash-medium", "google-gemini38-flash-high")
#: R4 §1.2/§1.5: memory.ask's limits that are not pinned by value; tracked like every manifest key
#: (api and librarian run the same value, and the file on disk is what both run)
R4_TRACKED = (
    "HLM_RESEARCH_WRITER_TIMEOUT_S",
    "HLM_RESEARCH_HTTP_TIMEOUT_S",
    "HLM_DETACHED_HOLD_MAX_S",
    "HLM_RESEARCH_TIMEOUT_S",
    "HLM_RESEARCH_PROSE_MAX_TOKENS",
    "HLM_LLM_TIMEOUT_S",
)
#: the research PRIMARY: research_chain = profile_chain(settings, "research"), whose head is
#: settings.profile (HLM_PROFILE); it writes when no writer is set (§6.2(b))
PRIMARY_KEY = "HLM_PROFILE"
#: ... and when HLM_PROFILE's file lists research in disabled_tasks: the research fallback, then the
#: default fallback (the order profile_chain resolves them in)
RESEARCH_FALLBACK_KEYS = ("HLM_FALLBACK_PROFILE__RESEARCH", "HLM_FALLBACK_PROFILE")
#: R4 R-11: the research tracer's directory; it must be unset in the api (any env file)
TRACE_KEY = "HLM_RESEARCH_TRACE_DIR"
#: R4 R-5: a writer profile's prices are trusted until its price_valid_until; WARN this many days ahead
PRICE_WARN_DAYS = 14
#: R4 R-14: the probe's output fields evaluate keeps (never a key, a header or a body)
PROBE_FIELDS = ("ok", "profile", "status", "latency_ms")
PROBE_TIMEOUT_S = 120.0
_PROFILE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_STATUS_TOKEN = re.compile(r"[a-z][a-z0-9_]{0,31}")
RELEASE_MANIFESTS: dict[str, dict[str, Any]] = {
    "r3": {
        # R3 predates memory.ask: an R3 env on a newer image keeps it (and its spend) off
        "off": ("HLM_QUERY_REWRITE", "HLM_RETRIEVAL_SOURCE_CAP", *RESEARCH_SWITCHES),
        "on": (),
        "exact": dict(_D094),
        "limits": {},
        "budgets": _BUDGETS,
    },
    "r4": {
        # R4 plan §1.5 (D-192/D-195): the Memory Map summaries made answers worse; they stay OFF
        "off": ("HLM_QUERY_REWRITE", "HLM_RETRIEVAL_SOURCE_CAP", "HLM_MAP_SUMMARY_ENABLED"),
        # "on": present and true in every env (the release's features, pinned): memory.ask
        "on": ("HLM_RESEARCH_ENABLED",),
        "exact": {
            **_D094,
            # the question waits for a research call: its fallback is the fast profile (D-094 style)
            "HLM_FALLBACK_PROFILE__RESEARCH": "openrouter",
            # the summaries are async: the default fallback, pinned explicitly
            "HLM_FALLBACK_PROFILE__MAP_SUMMARY": "openrouter-glm53-flash",
            # R4 §1.5 (B6): the answer contract the measurements were taken with (the code defaults
            # are claims / sources / off, so the env must say it)
            "HLM_RESEARCH_ANSWER_MODE": "prose",
            "HLM_RESEARCH_ATTRIBUTION": "llm",
            "HLM_RESEARCH_RERANK": "llm",
        },
        # "one_of": the value is one of these (None: absent or empty)
        "one_of": {WRITER_KEY: (None, *R4_WRITERS)},
        # "limits": present, positive and at most this (the per-question runaway guard, addendum 5).
        # R4 §1.5: 0.12 = one worst-case Gemini attempt + one luna fallback + planner/rerank/attribution
        "limits": {"HLM_RESEARCH_MAX_USD": 0.12, "HLM_RESEARCH_MAX_TOKENS": 100_000},
        # "tracked": compared across api, librarian and the file on disk; not pinned by value
        "tracked": R4_TRACKED,
        "budgets": _BUDGETS_R4,
        # R-11 (the api runs without the tracer), R-14 (probe-writer in the api container) and R-5
        # (the writer profile's price_valid_until): checked by _writer_failures
        "writer_checks": True,
    },
}
MANIFEST_KEYS = tuple(
    sorted(
        {
            key
            for m in RELEASE_MANIFESTS.values()
            for key in (
                *m["off"],
                *m["on"],
                *m["exact"],
                *m.get("one_of", {}),
                *m["limits"],
                *m.get("tracked", ()),
                *m["budgets"]["keys"],
                m["budgets"]["disabled_key"],
            )
        }
    )
)
#: an UNLABELLED env (the D-108 interim) keeps off every switch that any release keeps off
INTERIM_OFF = tuple(sorted({key for m in RELEASE_MANIFESTS.values() for key in m["off"]}))
SWITCH_KEYS = frozenset(key for m in RELEASE_MANIFESTS.values() for key in (*m["off"], *m["on"]))
_FALSE = frozenset({"", "0", "false", "no", "off"})


def _norm(key: str, value: object) -> object:
    """One comparable value: a switch by whether it is on, anything else as given (None: absent)."""
    if key in SWITCH_KEYS:
        return value is not None and str(value).strip().lower() not in _FALSE
    return None if value in (None, "") else str(value)


def _disk_env(path: str | None) -> dict[str, str] | None:
    """The manifest keys and the release marker of the llm.env FILE (never any other value)."""
    if not path:
        return None
    out: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        key, sep, value = line.strip().removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if sep and not line.lstrip().startswith("#") and (key in MANIFEST_KEYS or key == ENV_RELEASE_KEY):
            out[key] = (
                value[1:-1] if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"" else value
            )
    return out


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


def _writer_report(name: str) -> dict[str, Any]:
    """R4: the research writer profile as this container loads it: whether its key is SET (a
    boolean) and its price_valid_until (R-5), or the error TYPE (a message could quote a value)."""
    out: dict[str, Any] = {"profile": name}
    try:
        from hlmemo.config import load_profile
        from hlmemo.librarian.profiles import named_profile

        profile = named_profile(name)
        until = load_profile(name).get("price_valid_until")
        out.update(key_set=bool(profile.api_key), price_valid_until=None if until is None else str(until))
    except Exception as exc:  # noqa: BLE001
        out["error"] = type(exc).__name__
    return out


def _research_head(s: Any) -> tuple[bool | None, dict[str, Any] | None]:
    """R4: the head of the research chain, the profile that writes when no writer is set (the rule
    probe-writer follows): HLM_PROFILE, unless its profile file lists ``research`` in
    disabled_tasks; then HLM_FALLBACK_PROFILE__RESEARCH, else HLM_FALLBACK_PROFILE. Returns
    (primary_research_disabled, that profile's report)."""
    name = getattr(s, "profile", None)
    if not name:
        return None, None
    try:
        from hlmemo.librarian.profiles import profile_disabled_tasks, task_fallback_names

        disabled = "research" in profile_disabled_tasks(str(name))
    except Exception as exc:  # noqa: BLE001
        return None, {"profile": str(name), "error": type(exc).__name__}
    if disabled:
        name = task_fallback_names(s).get("research") or getattr(s, "fallback_profile", None)
    return disabled, (_writer_report(str(name)) if name else None)


def collect(service: str, with_probe: bool, wait_heartbeat_s: float = 0.0) -> int:
    from hlmemo.config import get_settings
    from hlmemo.librarian.profiles import describe_chains, named_profile, profile_chain

    out: dict[str, Any] = {"service": service}
    try:
        s = get_settings()
    except Exception as exc:  # noqa: BLE001 - the type only: a validation message could quote a value
        emit({**out, "error": f"settings: {type(exc).__name__}"})
        return 0
    primary_research_disabled, research_head = _research_head(s)
    out.update(
        enabled=bool(s.librarian_enabled),
        role=s.librarian_role,
        llm_mode=s.llm_mode,
        profile=s.profile,
        fallback=s.fallback_profile,
        # D-111: the release marker of the llm.env this container was created with; D-116: the
        # manifest keys it runs with (flags and profile names, never a secret)
        env_release=os.environ.get(ENV_RELEASE_KEY) or None,
        manifest_env={key: os.environ.get(key) for key in MANIFEST_KEYS},
        # review 79 T5: the EFFECTIVE research state this container runs with (defaults included);
        # R4: the answer settings, the limits, the writer (key SET or not, price date) and whether
        # the research tracer is on (R-11: the effective setting from any env file, or the variable)
        research={
            "enabled": bool(getattr(s, "research_enabled", False)),
            "map_summary": bool(getattr(s, "map_summary_enabled", False)),
            "max_usd": getattr(s, "research_max_usd", None),
            "max_tokens": getattr(s, "research_max_tokens", None),
            "answer_mode": getattr(s, "research_answer_mode", None),
            "attribution": getattr(s, "research_attribution", None),
            "rerank": getattr(s, "research_rerank", None),
            "writer_timeout_s": getattr(s, "research_writer_timeout_s", None),
            "http_timeout_s": getattr(s, "research_http_timeout_s", None),
            "detached_hold_max_s": getattr(s, "detached_hold_max_s", None),
            "timeout_s": getattr(s, "research_timeout_s", None),
            "prose_max_tokens": getattr(s, "research_prose_max_tokens", None),
            "writer": (
                _writer_report(str(s.research_writer_profile))
                if getattr(s, "research_writer_profile", None)
                else None
            ),
            # the head of the research chain (HLM_PROFILE, or the research fallback when its file
            # disables research): it writes when no writer is set, and probe-writer probes it
            "primary": research_head,
            "primary_research_disabled": primary_research_disabled,
            "trace_dir_set": bool(getattr(s, "research_trace_dir", None) or os.environ.get(TRACE_KEY)),
        },
    )
    try:
        chain = profile_chain(s)
        # D-094: the per-task fallbacks too (each needs its key and a reachable endpoint)
        extra = [p for p in chain[0].task_fallbacks.values() if p is not None]
        # R4 B5: and the research writer (key_set covers its key; --probe its endpoint)
        if getattr(s, "research_writer_profile", None):
            extra.append(named_profile(str(s.research_writer_profile)))
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


def _budget_problems(env: dict[str, Any], budgets: dict[str, Any]) -> list[str]:
    """D-121: every cap present and positive, the guard enabled (HLM_LLM_BUDGET_DISABLED=false),
    month <= the owner's target, day and hour <= month."""
    problems = []
    disabled = str(env.get(budgets["disabled_key"]) or "").strip().lower()
    if disabled != "false":
        problems.append(
            f"{budgets['disabled_key']}={env.get(budgets['disabled_key']) or '-'} (must be false)"
        )
    caps: dict[str, float] = {}
    for key in budgets["keys"]:
        try:
            caps[key] = float(str(env.get(key)))
        except ValueError:
            problems.append(f"{key}={env.get(key) or '-'} (a positive amount is required)")
            continue
        if not caps[key] > 0:
            problems.append(f"{key}={env.get(key)} (a positive amount is required)")
    hour, day, month = (caps.get(key) for key in budgets["keys"])
    if month is not None and month > budgets["month_max_usd"]:
        problems.append(f"{budgets['keys'][2]}={month:g} (at most {budgets['month_max_usd']:g})")
    for key, top in (budgets.get("max_usd") or {}).items():  # R4: the owner's hour/day bounds too
        if caps.get(key) is not None and caps[key] > top:
            problems.append(f"{key}={caps[key]:g} (at most {top:g})")
    for key, value in ((budgets["keys"][1], day), (budgets["keys"][0], hour)):
        if value is not None and month is not None and value > month:
            problems.append(f"{key}={value:g} (at most the month cap {month:g})")
    return problems


def _running(rep: dict[str, Any]) -> dict[str, Any]:
    """A report's manifest keys plus its release marker."""
    return {**(rep.get("manifest_env") or {}), ENV_RELEASE_KEY: rep.get("env_release")}


def _limit_problems(env: dict[str, Any], limits: dict[str, float]) -> list[str]:
    """Review 79 T5: every per-question limit present, positive and at most the manifest's."""
    problems = []
    for key, top in limits.items():
        try:
            value = float(str(env.get(key)))
        except ValueError:
            problems.append(f"{key}={env.get(key) or '-'} (required, at most {top:g})")
            continue
        if not 0 < value <= top:
            problems.append(f"{key}={env.get(key)} (positive, at most {top:g})")
    return problems


def _manifest_failures(
    lib: dict[str, Any], api: dict[str, Any], mode: str | None, disk: dict[str, str] | None
) -> list[str]:
    """D-111/D-116 with llm.env present, against ``RELEASE_MANIFESTS[mode]`` (``mode`` None: the
    unlabelled D-108 interim): the "off" keys (the interim: every key any release keeps off) are
    absent or false in the api's and the librarian's env; api and librarian match on EVERY manifest
    key and the release marker; the llm.env file on disk is what both run; in release mode both run
    that release's env with the EXACT manifest values, its "on" switches and its limits."""
    manifest = RELEASE_MANIFESTS[mode] if mode is not None else None
    rel = mode or "r2-env interim"
    failures = []
    for name, rep in (("api", api), ("librarian", lib)):
        env = rep.get("manifest_env") or {}
        for key in manifest["off"] if manifest is not None else INTERIM_OFF:
            if _norm(key, env.get(key)):
                failures.append(
                    f"{name} runs {key}={env.get(key)} ({rel} manifest: absent or false, D-116;"
                    " install_llm_env.sh)"
                )
    run_api, run_lib = _running(api), _running(lib)
    for key in (*MANIFEST_KEYS, ENV_RELEASE_KEY):
        if _norm(key, run_api.get(key)) != _norm(key, run_lib.get(key)):
            failures.append(
                f"api and librarian run different llm.env values for {key} (api={run_api.get(key) or '-'},"
                f" librarian={run_lib.get(key) or '-'}): stack.sh up -d --no-deps librarian api"
            )
    if disk is not None:  # the file on disk is what both services were created with
        for name, run in (("api", run_api), ("librarian", run_lib)):
            for key in (*MANIFEST_KEYS, ENV_RELEASE_KEY):
                if key in ("HLM_PROFILE", "HLM_FALLBACK_PROFILE") and key not in disk:
                    continue  # another env file (app.env) may set it
                if _norm(key, disk.get(key)) != _norm(key, run.get(key)):
                    failures.append(
                        f"llm.env on disk differs from what the {name} runs: {key}"
                        f" disk={disk.get(key) or '-'} running={run.get(key) or '-'}"
                        " (recreate librarian api: install_llm_env.sh)"
                    )
    if manifest is None:
        return failures
    if api.get("env_release") != mode:
        failures.append(
            f"api runs llm.env release={api.get('env_release') or '-'}"
            f" ({ENV_RELEASE_KEY}; {mode} manifest: {mode}): install the {mode.upper()} llm.env"
            " (install_llm_env.sh), then stack.sh up -d --no-deps librarian api"
        )
    for name, rep in (("api", api), ("librarian", lib)):
        env = rep.get("manifest_env") or {}
        for key in manifest["on"]:
            if not _norm(key, env.get(key)):
                failures.append(
                    f"{name} runs {key}={env.get(key) or '-'} ({mode} manifest: true, review 79 T5;"
                    " install_llm_env.sh)"
                )
        problems = _limit_problems(env, manifest["limits"])
        if problems:
            failures.append(f"{name} per-question limits violate the {mode} manifest: {'; '.join(problems)}")
    for name, rep in (("api", api), ("librarian", lib)):
        problems = _budget_problems(rep.get("manifest_env") or {}, manifest["budgets"])
        if problems:
            failures.append(f"{name} spend guard violates the {mode} manifest (D-121): {'; '.join(problems)}")
    for name, rep in (("api", api), ("librarian", lib)):
        env = rep.get("manifest_env") or {}
        wrong = [
            f"{key}={env.get(key) or '-'} (expected {value})"
            for key, value in manifest["exact"].items()
            if env.get(key) != value
        ]
        if wrong:
            failures.append(
                f"{name} llm.env differs from the {mode} manifest (the D-094 mapping): {', '.join(wrong)}"
                "; install_llm_env.sh"
            )
        for key, allowed in (manifest.get("one_of") or {}).items():
            if _norm(key, env.get(key)) not in allowed:
                names = ", ".join("unset" if a is None else a for a in allowed)
                failures.append(
                    f"{name} runs {key}={env.get(key) or '-'} ({mode} manifest: one of {names});"
                    " install_llm_env.sh"
                )
    return failures


def run_writer_probe(source: str) -> dict[str, Any]:
    """R4 R-14: ``python -m hlmemo.ops probe-writer`` in the **api** container (``source`` "api": via
    this checkout's stack.sh, with the api's effective writer profile and credential), or a captured
    result (``source`` a file holding ``{"exit": N, "stdout": "..."}``, for tests). Keeps the exit
    status and ONLY the PROBE_FIELDS of the last JSON object on stdout, type-checked: stderr and every
    other field (a key, a header or a body a broken probe might print) are dropped unread."""
    if source == "api":
        scripts = Path(__file__).resolve().parent
        command = [
            "bash",
            str(scripts / "stack.sh"),
            "exec",
            "-T",
            "api",
            "python",
            "-m",
            "hlmemo.ops",
            "probe-writer",
        ]
        try:
            proc = subprocess.run(  # noqa: S603 (a fixed command, no shell)
                command,
                cwd=scripts.parents[1],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_S,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {"exit": None, "error": type(exc).__name__, "result": None}
        exit_code, stdout = proc.returncode, proc.stdout
    else:
        try:
            captured = json.loads(Path(source).read_text(encoding="utf-8"))
            exit_code, stdout = captured.get("exit"), str(captured.get("stdout") or "")
        except (OSError, ValueError, AttributeError) as exc:
            return {"exit": None, "error": type(exc).__name__, "result": None}
    result = None
    for line in reversed(stdout.strip().splitlines()):
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            result = {key: parsed.get(key) for key in PROBE_FIELDS}
            break
    if result is not None:
        if not isinstance(result["ok"], bool):
            result["ok"] = None
        if not (isinstance(result["profile"], str) and _PROFILE_NAME.fullmatch(result["profile"])):
            result["profile"] = None
        for key in ("status", "latency_ms"):
            value = result[key]
            if key == "status" and isinstance(value, str) and _STATUS_TOKEN.fullmatch(value):
                continue  # e.g. "price_expired" (the profile's price date passed: no call made)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                result[key] = None
    return {"exit": exit_code, "result": result}


def _price_date_failures(
    name: str, until: Any, required: bool, today: datetime.date, warnings: list[str]
) -> list[str]:
    """R4 R-5: the writing profile's price_valid_until: FAIL when passed or unparsable (or missing
    when ``required``: the Gemini writers carry it), WARN (appended to ``warnings``) within
    PRICE_WARN_DAYS. A research primary without the field is not checked."""
    if until is None and not required:
        return []
    try:
        valid = datetime.date.fromisoformat(str(until))
    except ValueError:
        return [
            f"writer profile {name}: price_valid_until={until or '-'} is missing or not YYYY-MM-DD"
            " (R-5: prices without a validity date are not trusted)"
        ]
    if valid < today:
        return [
            f"writer profile {name}: price_valid_until {valid} has passed (today {today}): its"
            " prices are stale, so the spend guard would under-book; ship a profile with the new"
            " prices and date (R-5)"
        ]
    if (valid - today).days <= PRICE_WARN_DAYS:
        warnings.append(
            f"writer profile {name}: price_valid_until {valid} is within {PRICE_WARN_DAYS} days"
            f" (today {today}): ship the new prices and date before it passes, or"
            " evaluate --release r4 FAILS (R-5)"
        )
    return []


def _writer_failures(
    api: dict[str, Any], writer_probe: str | None, today: datetime.date
) -> tuple[list[str], list[str]]:
    """R4 (the manifest's "writer_checks"): (failures, warnings) for R-11 (the api runs without the
    research tracer), R-5 (the writer profile's price_valid_until: FAIL when passed or missing, WARN
    within PRICE_WARN_DAYS) and R-14 (probe-writer in the api container: exit 0, ok, the api's own
    writer). With the writer UNSET (§6.2(b)) the research PRIMARY writes (HLM_PROFILE, the head of the
    research chain): probe-writer probes it and must report that name (R-14, still required), and
    R-5 applies only when its profile file carries a price_valid_until (skipped when absent)."""
    failures: list[str] = []
    warnings: list[str] = []
    research = api.get("research")
    if not isinstance(research, dict) or "trace_dir_set" not in research:
        return [
            "the api report has no research state (collect): the tracer and the writer cannot be verified"
        ], []
    if research.get("trace_dir_set") is not False:
        failures.append(
            f"the api runs with {TRACE_KEY} set (R-11: the research tracer writes questions, excerpts and"
            " answers to files; remove it from llm.env/app.env, then stack.sh up -d --no-deps librarian api)"
        )
    env = api.get("manifest_env") or {}
    name, key, price_required = env.get(WRITER_KEY), WRITER_KEY, True
    writer = research.get("writer")
    if not name and not writer:
        # the head of the research chain writes; probe-writer probes it (its rule: HLM_PROFILE, or,
        # when that profile's file disables research, the research fallback); its price date only
        # when its profile carries one
        disabled = research.get("primary_research_disabled")
        if disabled is None:
            failures.append(
                "the api report does not say whether its primary may run research (collect): the"
                " research chain's head is unknown (R-14)"
            )
            return failures, warnings
        key = (
            next((k for k in RESEARCH_FALLBACK_KEYS if env.get(k)), RESEARCH_FALLBACK_KEYS[-1])
            if disabled
            else PRIMARY_KEY
        )
        name, price_required = env.get(key), False
        writer = research.get("primary")
        if not name:
            failures.append(f"the api runs no {key}: the research chain's head is unknown (R-14)")
            return failures, warnings
        print(f"writer: unset (the research primary {name} writes; {key})")
    if not isinstance(writer, dict) or writer.get("profile") != name or writer.get("error"):
        detail = writer.get("error") if isinstance(writer, dict) else None
        failures.append(
            f"the api's writer profile does not load as {key}={name or '-'} says"
            f" ({detail or 'the effective writer differs'}; profiles/<name>.toml,"
            " stack.sh up -d --no-deps librarian api)"
        )
        return failures, warnings
    until = writer.get("price_valid_until")
    print(
        f"writer: {name} key={'set' if writer.get('key_set') else 'MISSING'} price_valid_until={until or '-'}"
    )
    failures += _price_date_failures(name, until, price_required, today, warnings)
    if writer_probe is None:
        failures.append("the writer probe did not run (evaluate --writer-probe api; R-14)")
        return failures, warnings
    probe = run_writer_probe(writer_probe)
    result = probe.get("result") or {}
    shown = " ".join(f"{key}={result.get(key)}" for key in PROBE_FIELDS)
    print(f"writer probe (api container): exit={probe.get('exit')} {shown}")
    if probe.get("error"):
        failures.append(f"the writer probe could not run in the api container ({probe['error']}; R-14)")
    elif probe.get("exit") != 0:
        failures.append(
            f"the writer probe FAILED in the api container (exit {probe.get('exit')}, status"
            f" {result.get('status') or '-'}): the api's writer credential or endpoint does not answer"
            " (hlm_ops.sh ... probe-writer; the probe's output is not shown; R-14)"
        )
    elif result.get("ok") is not True:
        failures.append(f"the writer probe did not report ok (ok={result.get('ok')}; R-14)")
    elif result.get("profile") != name:
        failures.append(
            f"the writer probe used profile {result.get('profile') or '-'}, the api runs {name} (R-14)"
        )
    return failures, warnings


def evaluate(
    llm_env: str,
    librarian_path: str,
    api_path: str,
    release: str | None = None,
    llm_env_file: str | None = None,
    writer_probe: str | None = None,
    today: datetime.date | None = None,
) -> int:
    lib, api = _load(librarian_path), _load(api_path)
    failures: list[str] = []
    warnings: list[str] = []
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
        print(
            "per-task fallbacks: " + " ".join(f"{t}={p or '-'}" for t, p in sorted(lib["fallbacks"].items()))
        )
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
    # D-111: release mode = the explicit post-switch check (--release), or a service that runs an env
    # labelled with a known release (the api's label first); an unlabelled env is the D-108 interim
    labels = [x for x in (api.get("env_release"), lib.get("env_release")) if x in RELEASE_MANIFESTS]
    mode = release if release is not None else (labels[0] if labels else None)
    env = api.get("manifest_env") or {}
    print(
        f"llm.env manifest: env_release={api.get('env_release') or '-'}"
        f" (code {CODE_RELEASE}, check mode {mode or 'r2-env interim'}) "
        + " ".join(f"{key}={env.get(key) or '-'}" for key in MANIFEST_KEYS)
    )
    for name, rep in (("api", api), ("librarian", lib)):
        research = rep.get("research")
        if isinstance(research, dict):  # review 79 T5: the effective state (defaults included)
            print(
                f"{name} research: memory.ask enabled={str(research.get('enabled')).lower()}"
                f" map_summary={str(research.get('map_summary')).lower()}"
                f" max_usd={research.get('max_usd')} max_tokens={research.get('max_tokens')}"
                + "".join(  # R4: the answer settings and limits it runs with
                    f" {key}={research.get(key)}"
                    for key in (
                        "answer_mode",
                        "attribution",
                        "rerank",
                        "prose_max_tokens",
                        "timeout_s",
                        "http_timeout_s",
                        "writer_timeout_s",
                        "detached_hold_max_s",
                        "trace_dir_set",
                    )
                    if key in research
                )
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
        # D-111 (6) / D-116: the R1-style idle pass is gone for R3 — its llm.env is release state
        failures.append(
            f"{CODE_RELEASE.upper()} release: llm.env is required (the {CODE_RELEASE} manifest,"
            " D-111/D-116; install_llm_env.sh)"
        )
    else:
        # R2: llm.env present. Anything but enabled/live/observer everywhere is a failed release
        # (the librarian cannot be switched off for an R3 deployment: its llm.env is release state).
        failures += _r2_failures(lib, api, hb)
        # D-116 #8: only an UNLABELLED env is the D-108 interim; a label no manifest knows fails
        labels = {api.get("env_release"), lib.get("env_release")} - {None}
        unknown = sorted(str(label) for label in labels if label not in RELEASE_MANIFESTS)
        if unknown:
            failures.append(
                f"unknown llm.env release label {','.join(unknown)} ({ENV_RELEASE_KEY}; known:"
                f" {','.join(sorted(RELEASE_MANIFESTS))}; only an unlabelled env is the D-108 interim)"
            )
        failures += _manifest_failures(lib, api, mode, _disk_env(llm_env_file))
        if mode is not None and RELEASE_MANIFESTS[mode].get("writer_checks"):
            today = today or datetime.datetime.now(datetime.UTC).date()
            more, warnings = _writer_failures(api, writer_probe, today)
            failures += more
    for warning in warnings:
        print(f"WARNING {warning}")
    if failures:
        print("RESULT librarian FAIL " + "; ".join(failures))
        return 1
    common = f"enabled=true mode={R2_MODE} role={R2_ROLE} risk_judge={','.join(api['risk_judge'])}"
    if mode is not None and RELEASE_MANIFESTS[mode].get("writer_checks"):
        env = api.get("manifest_env") or {}
        writer = env.get(WRITER_KEY) or f"unset(research primary {env.get(PRIMARY_KEY) or '-'})"
        print(
            f"RESULT librarian PASS llm.env=present release={mode} manifest={mode} {common} writer={writer}"
        )
    elif mode is not None:
        print(f"RESULT librarian PASS llm.env=present release={mode} manifest={mode} {common}")
    else:
        print(
            f"RESULT librarian PASS llm.env=present release=r2-env (D-108 interim: install the"
            f" {CODE_RELEASE.upper()} llm.env, recreate librarian api, then evaluate --release"
            f" {CODE_RELEASE}) {common}"
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
    e.add_argument(
        "--llm-env-file",
        help="the llm.env on disk: it must be what both services run (manifest keys and marker only)",
    )
    e.add_argument(
        "--release",
        choices=tuple(sorted(RELEASE_MANIFESTS)),
        help="that release's mode regardless of the env marker: the post-switch verification (D-108 step 4)",
    )
    e.add_argument(
        "--writer-probe",
        metavar="api|FILE",
        help="R4 (R-14): `api` runs python -m hlmemo.ops probe-writer in the api container through this"
        " checkout's stack.sh (required in r4 mode when a writer is set); FILE: a captured result (tests)",
    )
    e.add_argument(
        "--today",
        type=datetime.date.fromisoformat,
        help="YYYY-MM-DD for the writer's price_valid_until check (default: today, UTC; tests)",
    )
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
        return evaluate(
            args.llm_env,
            args.librarian,
            args.api,
            args.release,
            args.llm_env_file,
            args.writer_probe,
            args.today,
        )
    if args.mode == "job":
        return job(args.version_id, args.wait)
    return observer_gate(args.job, args.audit, args.version_id)


if __name__ == "__main__":
    sys.exit(main())
