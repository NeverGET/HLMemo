"""The two arms of the ceiling experiment behind ONE task prompt per experiment (PLAN §3, D-017).

* ``gemini``: the product ``Provider`` with the profile named in ``config.json`` (its key from the
  environment the product already uses, never printed), the ONE task prompt as the system prompt and
  the packet's ``user`` message. Hard spend cap: the whole experiment's spend (every attempt in the
  persistent ledger ``runs/gemini-ledger.jsonl``) never exceeds ``spend_cap_usd``: each attempt
  reserves its worst case in a ``MemoryBudget`` sized to the cap minus what was already spent, and
  a refused reservation stops the run.
* ``opus``: ``claude -p --model <model> --output-format stream-json`` with the SAME system prompt and
  the SAME user message, no tools, no MCP, no settings, in a scratch directory. The raw stdout is
  saved by code before anything parses it (nobody ever retypes model output).

Both arms get one schema retry (the provider's own retry; the same for the CLI arm). Outputs go to
``outputs/<exp>/<run label>/<packet>.json`` with the raw attempts next to them. The runner refuses
to start without a verified pre-registration (``al_prereg.verify``).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import al_common as C

LEDGER = ("runs", "gemini-ledger.jsonl")
RUN_LOG = ("runs", "run-log.jsonl")


def run_label(arm: str, run: int) -> str:
    return f"{arm}-r{run}"


def task_spec(exp: str, cfg: dict[str, Any]) -> Any:
    """The experiment's ONE task (system prompt + JSON schema) as a product ``TaskSpec``."""
    from hlmemo.librarian.prompts import TaskSpec

    md, schema = C.prompt_files(exp)
    system = md.read_text(encoding="utf-8")
    schema_obj = json.loads(schema.read_text(encoding="utf-8"))
    return TaskSpec(
        name=f"al_{exp.lower()}",
        prompt_version=f"al-{C.sha256_text(system)[:12]}",
        schema_version=f"al-{C.sha256_file(schema)[:12]}",
        system=system,
        schema=schema_obj,
        max_tokens=int(cfg["max_tokens"][exp]),
    )


# --------------------------------------------------------------------------- spend
def spent_usd() -> Decimal:
    """Every Gemini attempt of the experiment so far (all experiments, all runs)."""
    total = Decimal(0)
    for row in C.read_jsonl(C.private_dir().joinpath(*LEDGER)):
        total += Decimal(str(row.get("cost_usd") or 0))
    return total


def remaining_usd(cfg: dict[str, Any]) -> Decimal:
    return Decimal(str(cfg["spend_cap_usd"])) - spent_usd()


def worst_case_usd(profile: Any, packets: list[dict[str, Any]], spec: Any) -> Decimal:
    """Worst case of one run: every packet's first attempt at ``max_tokens`` (a schema retry can
    add up to the same again; the per-attempt reservation is the hard guard)."""
    from hlmemo.core.budget import Meter

    meter = Meter()
    total = Decimal(0)
    for pk in packets:
        tokens = meter.count_text(spec.system) + meter.count_text(pk["user"]) + 11
        total += profile.worst_usd(tokens, spec.max_tokens)
    return total


# --------------------------------------------------------------------------- outputs
def out_dir(exp: str, label: str) -> Path:
    return C.pdir("outputs", exp, label)


def _done(exp: str, label: str, packet_id: str) -> bool:
    f = out_dir(exp, label) / f"{packet_id}.json"
    return f.is_file() and C.read_json(f).get("status") == "ok"


def _record(exp: str, label: str, packet: dict[str, Any], rec: dict[str, Any]) -> None:
    rec = {
        "exp": exp,
        "run": label,
        "packet_id": packet["packet_id"],
        "user_sha256": packet["user_sha256"],
        "finished_at": datetime.now(UTC).isoformat(),
        **rec,
    }
    C.write_json(out_dir(exp, label) / f"{packet['packet_id']}.json", rec)
    C.append_jsonl(
        C.private_dir().joinpath(*RUN_LOG),
        {
            k: rec.get(k)
            for k in ("exp", "run", "packet_id", "status", "cost_usd", "latency_ms", "finished_at")
        },
    )


# --------------------------------------------------------------------------- gemini arm
async def run_gemini(
    exp: str,
    packets: list[dict[str, Any]],
    cfg: dict[str, Any],
    *,
    run: int,
    prereg_sha: str,
    retry_failed: bool = False,
    transport: Any = None,
) -> dict[str, Any]:
    from hlmemo.librarian.budget import MemoryBudget
    from hlmemo.librarian.errors import BudgetDeferred, LibrarianError
    from hlmemo.librarian.ledger import MemoryLedger
    from hlmemo.librarian.profiles import named_profile
    from hlmemo.librarian.provider import Provider
    from hlmemo.librarian.redact import Redactor

    arm = cfg["arms"]["gemini"]
    label = run_label("gemini", run)
    C.load_env_file(C.ROOT / arm.get("env_file", ".env"))
    profile = named_profile(arm["profile"])
    if not profile.api_key and transport is None:
        raise C.HarnessError(f"profile {arm['profile']}: its API key is not in the environment")
    spec = task_spec(exp, cfg)
    remaining = remaining_usd(cfg)
    if remaining <= 0:
        raise C.HarnessError(f"spend cap ${cfg['spend_cap_usd']} reached (spent ${spent_usd()})")
    todo = [
        p
        for p in packets
        if not _done(exp, label, p["packet_id"])
        and (retry_failed or not (out_dir(exp, label) / f"{p['packet_id']}.json").is_file())
    ]
    worst = worst_case_usd(profile, todo, spec)
    print(
        f"{label} {exp}: {len(todo)} packets, worst case ${worst:.4f} (first attempts),"
        f" remaining under the cap ${remaining:.4f}"
    )
    ledger = MemoryLedger()
    budget = MemoryBudget(remaining)
    provider = Provider(
        [profile],
        mode="live",
        budget=budget,
        ledger=ledger,
        redactor=Redactor(),
        transport=transport,
        timeout_s=float(arm.get("timeout_s", 240)),
    )
    summary = {"run": label, "ok": 0, "failed": 0, "stopped": False}
    seen_rows = 0
    try:
        for pk in todo:
            attempts: list[dict[str, Any]] = []

            def observe(event: dict[str, Any], sink: list[dict[str, Any]] = attempts) -> None:
                sink.append({k: v for k, v in event.items() if k != "user"})  # raw model text, by code

            rec: dict[str, Any] = {"arm": "gemini", "profile": profile.name, "model_id": profile.model_id}
            t0 = time.monotonic()
            try:
                res = await provider.complete(spec, pk["user"], lineage=str(uuid.uuid4()), observe=observe)
                rec.update(status="ok", output=res.output, latency_ms=res.latency_ms, usage=res.usage)
                summary["ok"] += 1
            except BudgetDeferred as exc:
                rec.update(status="budget_stop", error=str(exc))
                summary["stopped"] = True
            except LibrarianError as exc:
                rec.update(status=type(exc).__name__, error=str(exc)[:500])
                summary["failed"] += 1
            rec.setdefault("latency_ms", int((time.monotonic() - t0) * 1000))
            rec["prereg_sha256"] = prereg_sha
            C.write_json(out_dir(exp, label) / f"{pk['packet_id']}.raw.json", {"attempts": attempts})
            new_rows = ledger.as_dicts()[seen_rows:]
            seen_rows = len(ledger.rows)
            for row in new_rows:  # persisted per call: the cap survives a crash
                C.append_jsonl(
                    C.private_dir().joinpath(*LEDGER),
                    {**row, "exp": exp, "run": label, "packet_id": pk["packet_id"]},
                )
            # every attempt of the call (a schema retry included), as settled by the provider
            rec["cost_usd"] = str(sum((Decimal(str(r["cost_usd"])) for r in new_rows), Decimal(0)))
            rec["attempt_outcomes"] = [r["outcome"] for r in new_rows]
            _record(exp, label, pk, rec)
            if summary["stopped"]:
                print(f"{label}: spend cap reached, run stopped before {pk['packet_id']}")
                break
    finally:
        await provider.aclose()
    summary["spent_total_usd"] = str(spent_usd())
    return summary


# --------------------------------------------------------------------------- opus arm (claude CLI)
def claude_cmd(arm: dict[str, Any], system: str) -> list[str]:
    cmd = [
        arm.get("cli", "claude"), "-p", "--model", arm["model"],
        "--output-format", "stream-json", "--verbose",
        "--system-prompt", system,
        "--tools", "", "--strict-mcp-config", "--disable-slash-commands",
        "--no-session-persistence", "--setting-sources", "",
    ]  # fmt: skip
    if arm.get("effort"):
        cmd += ["--effort", str(arm["effort"])]
    return cmd


def parse_stream(raw: bytes) -> dict[str, Any]:
    """The result event of a ``stream-json`` transcript: the final text, the model, usage, cost."""
    out: dict[str, Any] = {"text": None, "model": None, "is_error": None, "cost_usd": None, "usage": None}
    for line in raw.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            out["model"] = ev.get("model")
        elif ev.get("type") == "result":
            out["text"] = ev.get("result")
            out["is_error"] = ev.get("is_error")
            out["cost_usd"] = ev.get("total_cost_usd")
            out["usage"] = ev.get("usage")
    return out


def run_opus(
    exp: str,
    packets: list[dict[str, Any]],
    cfg: dict[str, Any],
    *,
    run: int,
    prereg_sha: str,
    retry_failed: bool = False,
    runner: Any = None,
) -> dict[str, Any]:
    from hlmemo.librarian.provider import parse_json_object

    arm = cfg["arms"]["opus"]
    label = run_label("opus", run)
    spec = task_spec(exp, cfg)
    cmd = claude_cmd(arm, spec.system)
    summary = {"run": label, "ok": 0, "failed": 0}
    run_cmd = runner or _subprocess_runner
    for pk in packets:
        f = out_dir(exp, label) / f"{pk['packet_id']}.json"
        if _done(exp, label, pk["packet_id"]) or (f.is_file() and not retry_failed):
            continue
        rec: dict[str, Any] = {"arm": "opus", "cli_model": arm["model"], "effort": arm.get("effort")}
        t0 = time.monotonic()
        err = None
        for attempt in (1, 2):
            raw, code = run_cmd(cmd, pk["user"], float(arm.get("timeout_s", 1200)))
            C.write_bytes(out_dir(exp, label) / f"{pk['packet_id']}.attempt{attempt}.stream.jsonl", raw)
            parsed = parse_stream(raw)
            rec["model"] = parsed["model"]
            rec.setdefault("attempts", []).append(
                {"attempt": attempt, "exit": code, "is_error": parsed["is_error"], "usage": parsed["usage"]}
            )
            if code != 0 or parsed["is_error"] or parsed["text"] is None:
                err = f"cli_exit_{code}" if code != 0 else "cli_reported_error"
                continue
            obj, why = parse_json_object(parsed["text"])
            err = why if obj is None else spec.schema_errors(obj)
            if err is None:
                rec.update(status="ok", output=obj)
                break
        if err is not None:
            rec.update(status="schema_fail", error=str(err)[:500])
            summary["failed"] += 1
        else:
            summary["ok"] += 1
        rec["latency_ms"] = int((time.monotonic() - t0) * 1000)
        rec["prereg_sha256"] = prereg_sha
        _record(exp, label, pk, rec)
    return summary


def _subprocess_runner(cmd: list[str], user: str, timeout_s: float) -> tuple[bytes, int]:
    env = dict(os.environ)
    env["HLM_CAPTURE"] = "off"  # the child's own SessionEnd hook is a no-op
    try:
        with tempfile.TemporaryDirectory(prefix="hlm-al-") as tmp:
            proc = subprocess.run(  # noqa: S603
                cmd, input=user.encode("utf-8"), capture_output=True, timeout=timeout_s, cwd=tmp, env=env
            )
    except subprocess.TimeoutExpired as exc:
        return (exc.stdout or b""), 124
    return proc.stdout, proc.returncode


__all__ = [
    "claude_cmd",
    "parse_stream",
    "remaining_usd",
    "run_gemini",
    "run_label",
    "run_opus",
    "spent_usd",
    "task_spec",
    "worst_case_usd",
]
