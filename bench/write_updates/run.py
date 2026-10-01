"""D-118 client benchmark: does a client LLM fill ``memory.write`` ``updates`` correctly?

The client stand-in is a provider PROFILE (D-017: the model id, base URL, key and prices live in
the profile; this script names none). It sees the real ``memory.query`` / ``memory.write`` tool
descriptions and input schemas (the wording under test), a ``memory.query`` result showing an old
multi-statement memory among distractors, and a new fact from the user; the ``memory_write`` call is
forced (the benchmark measures how ``updates`` are filled, not whether the agent saves at all).

Metrics per run (``scenarios.py``: 30 update scenarios, 10 controls):
* item precision / recall: an update naming the gold memory (clue or its version id);
* span precision / recall: the item is right AND ``old_span`` lies inside the outdated statement and
  contains its key phrase;
* abstention: controls without any update;
* server-apply rate: an update scenario whose correct update passes the server's own deterministic
  checks (``core.write_updates``: the revise guards incl. "replacement verbatim in the carrying body",
  or the supersede grounding) — the end-to-end rate at which the correction would land.

Spend: every attempt is appended to ``calls.jsonl`` immediately (D-112), with the provider's own
``usage.cost``; the run stops before an attempt whose worst case would pass ``--max-usd``.

    OPENROUTER_API_KEY=... uv run python bench/write_updates/run.py --profile openrouter-gpt6-luna \
        --variants shipped,short --reps 3 --max-usd 1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from scenarios import CONTROLS, MEMORIES, SCENARIOS, UPDATES, Scenario, body, clue_vid, query_result  # noqa: E402

from hlmemo.core.budget import Meter  # noqa: E402
from hlmemo.core.write_updates import parse_item, revise_guards, span_occurs  # noqa: E402
from hlmemo.librarian.profiles import named_profile  # noqa: E402
from hlmemo.server.tools import TOOL_BY_NAME  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
BASE_WRITE = (
    "Append 1..50 memory items (new items or revisions with expected_version_id) in one "
    "transaction, idempotent per request_id. Returns the version ids and chunk counts."
)
#: the consult-74 wording (shortest); "shipped" is whatever the server advertises now
SHORT = (
    BASE_WRITE + " Per-item updates: target clue, quoted old_span, mode revise or supersede; revise "
    "replacement must occur verbatim in body. Results: applied, linked, rejected."
)
SYSTEM = (
    "You are a coding agent working on the acme-api project. Your long-term memory is the HLMemo MCP "
    "server (tools memory_query and memory_write, project \"acme-api\"). Save durable project facts "
    "with memory_write."
)
MAX_TOKENS = 2000


def _schema(tool: str) -> dict[str, Any]:
    s = json.loads(json.dumps(TOOL_BY_NAME[tool].input_schema))
    s.pop("$schema", None)
    return s


def tools(variant: str) -> list[dict[str, Any]]:
    write_desc = TOOL_BY_NAME["memory.write"].description if variant == "shipped" else SHORT
    return [
        {
            "type": "function",
            "function": {
                "name": "memory_query",
                "description": TOOL_BY_NAME["memory.query"].description,
                "parameters": _schema("memory.query"),
            },
        },
        {
            "type": "function",
            "function": {"name": "memory_write", "description": write_desc, "parameters": _schema("memory.write")},
        },
    ]


def messages(s: Scenario) -> list[dict[str, Any]]:
    title = MEMORIES[s.topic][0]
    q_args = {"project": "acme-api", "query": title.lower(), "token_budget": 2000}
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"What do we have in memory about {title.lower()}?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_q1",
                    "type": "function",
                    "function": {"name": "memory_query", "arguments": json.dumps(q_args)},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_q1", "content": json.dumps(query_result(s))},
        {"role": "assistant", "content": f"Memory has a note '{title}' with the current setup, plus related notes."},
        {"role": "user", "content": f"{s.fact} Please save that to memory."},
    ]


def score(s: Scenario, args: dict[str, Any] | None) -> dict[str, Any]:
    """Every predicted update judged against the scenario's gold (see the module doc)."""
    items = [it for it in (args or {}).get("items") or [] if isinstance(it, dict)]
    preds = []
    for it in items:
        for u in it.get("updates") or []:
            if not isinstance(u, dict):
                continue
            lid, vid, reason = parse_item(u.get("item"), u.get("expected_version"))
            item_ok = bool(s.is_update and reason is None and lid is None and vid == clue_vid(s.topic))
            span = u.get("old_span") if isinstance(u.get("old_span"), str) else ""
            span_ok = bool(item_ok and span and span in (s.gold_statement or "") and (s.key or "") in span)
            applies, why_not = False, None
            if item_ok:
                if u.get("mode") == "supersede":
                    applies = span_occurs(body(s.topic), span)
                else:
                    chk, why_not = revise_guards(
                        old_body=body(s.topic),
                        old_span=span,
                        replacement=u.get("replacement") if isinstance(u.get("replacement"), str) else None,
                        carrier_body=str(it.get("body") or ""),
                        old_projects=[1],
                        old_scope="all",
                        new_projects=[1],
                        new_scope=str(it.get("device_scope") or "all"),
                    )
                    applies = chk is not None
            preds.append(
                {
                    "item": u.get("item"),
                    "mode": u.get("mode"),
                    "old_span": span,
                    "replacement": u.get("replacement"),
                    "item_ok": item_ok,
                    "span_ok": span_ok,
                    "applies": applies,
                    "server_reason": why_not,
                }
            )
    return {
        "n_updates": len(preds),
        "item_hit": any(p["item_ok"] for p in preds),
        "span_hit": any(p["span_ok"] for p in preds),
        "applies": any(p["applies"] for p in preds),
        "revise_mode": any(p["item_ok"] and p["mode"] == "revise" for p in preds),
        "preds": preds,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ups = [r for r in rows if r["kind"] == "update"]
    ctl = [r for r in rows if r["kind"] == "control"]
    preds = [p for r in rows for p in r["score"]["preds"]]
    n_pred = len(preds)
    return {
        "calls": len(rows),
        "errors": sum(1 for r in rows if r.get("error")),
        "item_precision": round(sum(p["item_ok"] for p in preds) / n_pred, 3) if n_pred else None,
        "item_recall": round(sum(r["score"]["item_hit"] for r in ups) / len(ups), 3) if ups else None,
        "span_precision": round(sum(p["span_ok"] for p in preds) / n_pred, 3) if n_pred else None,
        "span_recall": round(sum(r["score"]["span_hit"] for r in ups) / len(ups), 3) if ups else None,
        "abstention": round(sum(r["score"]["n_updates"] == 0 for r in ctl) / len(ctl), 3) if ctl else None,
        "server_apply_rate": round(sum(r["score"]["applies"] for r in ups) / len(ups), 3) if ups else None,
        "revise_mode_rate": round(sum(r["score"]["revise_mode"] for r in ups) / len(ups), 3) if ups else None,
        "predicted_updates": n_pred,
        "usd": float(sum(Decimal(str(r.get("cost") or 0)) for r in rows)),
        "latency_p50_s": _pct([r["latency_s"] for r in rows], 50),
        "latency_p95_s": _pct([r["latency_s"] for r in rows], 95),
    }


def _pct(xs: list[float], p: int) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))], 2)


class Spend:
    def __init__(self, cap: Decimal) -> None:
        self.cap, self.spent, self.lock = cap, Decimal(0), asyncio.Lock()


async def call(
    client: httpx.AsyncClient, profile: Any, s: Scenario, variant: str, rep: int, spend: Spend, log: Path, meter: Meter
) -> dict[str, Any] | None:
    payload: dict[str, Any] = {
        "model": profile.model_id,
        "messages": messages(s),
        "tools": tools(variant),
        "tool_choice": {"type": "function", "function": {"name": "memory_write"}},
        "max_tokens": MAX_TOKENS,
    }
    if profile.reasoning:
        payload["reasoning"] = profile.reasoning
    for key in ("provider", "usage"):  # routing/privacy/usage settings from the profile (not response_format)
        if key in profile.extra:
            payload[key] = profile.extra[key]
    worst = profile.worst_usd(meter.count(payload["messages"]) + meter.count(payload["tools"]), MAX_TOKENS)
    async with spend.lock:
        if spend.spent + worst > spend.cap:
            return None
        spend.spent += worst  # reserved; settled below
    t0 = time.monotonic()
    row: dict[str, Any] = {"sid": s.sid, "kind": "update" if s.is_update else "control", "variant": variant, "rep": rep}
    args = None
    cost = worst
    try:
        r = await client.post(
            f"{profile.base_url}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {profile.api_key}"},
        )
        data = r.json()
        if r.status_code != 200:
            row["error"] = f"http {r.status_code}: {str(data)[:300]}"
        else:
            usage = data.get("usage") or {}
            cost = Decimal(str(usage.get("cost"))) if usage.get("cost") is not None else worst
            row["usage"] = {k: usage.get(k) for k in ("prompt_tokens", "completion_tokens", "cost")}
            calls = (data["choices"][0]["message"].get("tool_calls") or []) if data.get("choices") else []
            if calls:
                args = json.loads(calls[0]["function"]["arguments"])
            else:
                row["error"] = "no tool call"
    except Exception as exc:  # noqa: BLE001 - recorded, the run continues
        row["error"] = f"{type(exc).__name__}: {exc}"[:300]
    row["latency_s"] = round(time.monotonic() - t0, 2)
    async with spend.lock:
        spend.spent += cost - worst
    row["cost"] = str(cost)
    row["args"] = args
    row["score"] = score(s, args)
    with log.open("a", encoding="utf-8") as fh:  # every attempt, immediately
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--profile", required=True)
    ap.add_argument("--variants", default="shipped")
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--max-usd", type=Decimal, default=Decimal("1"))
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--out", default=None)
    ap.add_argument("--only", default="", help="comma-separated scenario ids (a smoke run)")
    a = ap.parse_args()
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    scenarios = [s for s in SCENARIOS if not only or s.sid in only]
    profile = named_profile(a.profile)
    if not profile.api_key:
        sys.stderr.write(f"profile {a.profile}: no API key in the environment\n")
        return 2
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = Path(a.out or ROOT / "bench" / "results" / f"{stamp}-write-updates-{a.profile}")
    out.mkdir(parents=True, exist_ok=True)
    log = out / "calls.jsonl"
    spend, meter = Spend(a.max_usd), Meter()
    sem = asyncio.Semaphore(a.concurrency)
    variants = [v.strip() for v in a.variants.split(",") if v.strip()]

    async with httpx.AsyncClient(timeout=httpx.Timeout(90.0)) as client:

        async def one(s: Scenario, v: str, rep: int) -> dict[str, Any] | None:
            async with sem:
                return await call(client, profile, s, v, rep, spend, log, meter)

        jobs = [one(s, v, rep) for rep in range(a.reps) for v in variants for s in scenarios]
        rows = [r for r in await asyncio.gather(*jobs) if r is not None]
    summary: dict[str, Any] = {
        "profile": a.profile,
        "scenarios": {
            "updates": sum(1 for s in scenarios if s in UPDATES),
            "controls": sum(1 for s in scenarios if s in CONTROLS),
        },
        "reps": a.reps,
        "complete": len(rows) == a.reps * len(variants) * len(scenarios),
        "variants": {},
    }
    for v in variants:
        vr = [r for r in rows if r["variant"] == v]
        summary["variants"][v] = {
            "all": summarize(vr),
            "per_rep": [summarize([r for r in vr if r["rep"] == k]) for k in range(a.reps)],
        }
    summary["usd_total"] = float(sum(Decimal(r["cost"]) for r in rows))
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["complete"] else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
