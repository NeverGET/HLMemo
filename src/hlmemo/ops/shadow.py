"""D-272: the librarian shadow week's diagnostics (read-only).

``report``: what blocks a promotion, by category: shadow errors (stage + type, with examples),
skips, every observed attempt (outcomes, latency, tokens, cost), guard downgrades and judgement
flags on both sides, and the supersessions only one side proposed, each with both sides'
judgement and raw answer. ``pair``: one pair, surgically: the exact relate input of each job that
asked it (rebuilt from the immutable versions), the live and the shadow answer for it, and the
verifier's verdicts. Nothing here calls a model or writes anything.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from psycopg import AsyncConnection

from hlmemo.db import librarian_queries as lq
from hlmemo.librarian.tasks.write_review import PAIRS_PER_CALL, relate_payload

#: body characters per item in a labelling sheet (``with_texts``)
SHEET_CHARS = 4000


def _calls(req: dict[str, Any]) -> list[dict[str, Any]]:
    """The live audit's calls: flat for one call, ``calls[]`` for several."""
    if req.get("calls"):
        return list(req["calls"])
    return [req] if req.get("output") is not None and req.get("task") else []


def _chunks(asked: list[list[Any]]) -> list[tuple[str, list[str]]]:
    """The relate calls a job made, in order: per subject (first-seen order), its candidates in
    chunks of ``PAIRS_PER_CALL`` (write_review._relate)."""
    by_subject: dict[str, list[str]] = {}
    for subj, cand, _cross in asked:
        by_subject.setdefault(subj, []).append(cand)
    return [
        (subj, cands[i : i + PAIRS_PER_CALL])
        for subj, cands in by_subject.items()
        for i in range(0, len(cands), PAIRS_PER_CALL)
    ]


def _result(call: dict[str, Any], cand: str) -> dict[str, Any] | None:
    for r in (call.get("output") or {}).get("results") or []:
        if isinstance(r, dict) and str(r.get("id")) == cand:
            return {**r, "profile": call.get("profile"), "prompt_version": call.get("prompt_version")}
    return None


def _live_answers(req: dict[str, Any], asked: list[list[Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """(subject, candidate) -> the live relate answer. Live call audits do not name their subject,
    so each expected chunk takes the next relate call whose answered ids all belong to it (a failed
    call leaves its chunk unmatched instead of shifting the rest)."""
    relate = [c for c in _calls(req) if c.get("task") == "relate"]
    out: dict[tuple[str, str], dict[str, Any]] = {}
    i = 0
    for subj, cands in _chunks(asked):
        for j in range(i, len(relate)):
            ids = {
                str(r.get("id"))
                for r in (relate[j].get("output") or {}).get("results") or []
                if isinstance(r, dict)
            }
            if ids and ids <= set(cands):
                for cand in cands:
                    res = _result(relate[j], cand)
                    if res is not None:
                        out[(subj, cand)] = res
                i = j + 1
                break
    return out


def _shadow_answer(calls: list[dict[str, Any]], subj: str, cand: str) -> dict[str, Any] | None:
    for c in calls:
        if c.get("task") == "relate" and c.get("subject") == subj and cand in (c.get("candidates") or []):
            return _result(c, cand)
    return None


def _shadow_verdict(calls: list[dict[str, Any]], subj: str, cand: str) -> dict[str, Any] | None:
    """The shadow verifier's raw verdict for the pair (its item id is named in the call audit)."""
    for c in calls:
        if c.get("task") != "relate_verify":
            continue
        for item, pr in (c.get("pairs") or {}).items():
            if pr == [subj, cand]:
                for r in (c.get("output") or {}).get("results") or []:
                    if isinstance(r, dict) and str(r.get("id")) == item:
                        return {**r, "profile": c.get("profile")}
    return None


def _side(
    judgement: dict[str, Any] | None, answer: dict[str, Any] | None, *, unanswered: bool = False
) -> dict[str, Any]:
    """One side of a pair: its judgement after the guards, else the plain answer status."""
    status = "not_answered" if unanswered else ("judged" if judgement else "none")
    return {**(judgement or {"relation": "none"}), "status": status, "answer": answer}


def _judgement(judgements: list[dict[str, Any]], subject: str, cand: str) -> dict[str, Any] | None:
    for j in judgements or []:
        if j.get("subject") == subject and str(j.get("id")) == cand:
            return j
    return None


def _live_supersessions(req: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    """(subject, candidate, judgement) of every live contradiction that kept a direction."""
    return [
        (j.get("subject"), str(j.get("id")), j)
        for j in req.get("judgements") or []
        if j.get("relation") == "contradicts"
        and j.get("tier") != "abstain"
        and j.get("supersedes") in ("new", "old")
    ]


def _shadow_supersessions(sh: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    out = []
    for p in sh.get("proposals") or []:
        clues = p.get("clues") or []
        if p.get("kind") == "contradiction" and p.get("supersedes") in ("new", "old") and len(clues) == 2:
            out.append((clues[0], clues[1], p))
    return out


def _pct(values: list[int], q: float) -> int | None:
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))]


async def _rows(conn: AsyncConnection, project_id: int | None, since: str | None) -> list[Any]:
    cur = await conn.execute(
        """
        SELECT e.event_id, e.received_at, p.slug, e.payload->'request'
          FROM events e LEFT JOIN projects p ON p.project_id = e.project_id
         WHERE e.kind = 'librarian' AND e.payload->'request'->>'op' = 'write_review'
           AND e.payload->'request' ? 'shadow'
           AND (%(pid)s::bigint IS NULL OR e.project_id = %(pid)s)
           AND (%(since)s::timestamptz IS NULL OR e.received_at >= %(since)s::timestamptz)
         ORDER BY e.event_id
        """,
        {"pid": project_id, "since": since},
    )
    return list(await cur.fetchall())


async def _items(conn: AsyncConnection, clues: set[str], texts: bool) -> dict[str, dict[str, Any]]:
    vids = sorted({int(c[1:]) for c in clues if c and c[1:].isdigit()})
    if not vids:
        return {}
    cur = await conn.execute(
        "SELECT version_id, kind, title, valid_from, body FROM memory_versions WHERE version_id = ANY(%s)",
        (vids,),
    )
    out = {}
    for vid, kind, title, valid_from, body in await cur.fetchall():
        row = {"kind": kind, "title": title, "valid_from": valid_from.isoformat()}
        if texts:
            row["body"] = (body or "")[:SHEET_CHARS]
        out[f"v{vid}"] = row
    return out


async def report(
    conn: AsyncConnection, project_id: int | None, since: str | None, *, with_texts: bool = False
) -> dict[str, Any]:
    rows = await _rows(conn, project_id, since)
    errors: dict[str, dict[str, Any]] = {}
    skipped: Counter[str] = Counter()
    attempts: dict[str, dict[str, Any]] = {}
    guards: dict[str, Counter[str]] = {"live": Counter(), "shadow": Counter()}
    flags: dict[str, Counter[str]] = {"live": Counter(), "shadow": Counter()}
    live_all: dict[tuple[str, ...], dict[str, Any]] = {}
    shadow_all: dict[tuple[str, ...], dict[str, Any]] = {}
    asked = 0
    n_unanswered = 0
    lineages: set[Any] = set()
    first = last = None
    for event_id, at, slug, req in rows:
        first, last = first or at, at
        sh = req.get("shadow") or {}
        asked += len(sh.get("asked") or [])
        if sh.get("skipped"):
            skipped[str(sh["skipped"])] += 1
        err = sh.get("error")
        if err:
            e = err if isinstance(err, dict) else {"stage": "?", "type": str(err), "message": ""}
            key = f"{e.get('stage')}/{e.get('type')}"
            slot = errors.setdefault(key, {"count": 0, "examples": []})
            slot["count"] += 1
            if len(slot["examples"]) < 5:
                slot["examples"].append({"event_id": event_id, "project": slug, "message": e.get("message")})
        for a in sh.get("attempts") or []:
            key = f"{a.get('profile')}/{a.get('task')}"
            slot = attempts.setdefault(
                key, {"n": 0, "outcomes": Counter(), "lat": [], "in": 0, "cached": 0, "out": 0, "cost": 0.0}
            )
            slot["n"] += 1
            slot["outcomes"][str(a.get("outcome"))] += 1
            if a.get("latency_ms") is not None:
                slot["lat"].append(int(a["latency_ms"]))
            tok = a.get("tokens") or {}
            for k in ("in", "cached", "out"):
                slot[k] += int(tok.get(k) or 0)
            slot["cost"] += float(a.get("cost") or 0)
            if a.get("outcome") == "schema_fail":
                slot.setdefault("failures", [])
                if len(slot["failures"]) < 5:
                    slot["failures"].append(
                        {"event_id": event_id, "error": a.get("error"), "raw": a.get("raw")}
                    )
        for side, block in (("live", req), ("shadow", sh)):
            guards[side].update({k: int(v) for k, v in (block.get("guards") or {}).items()})
            for j in block.get("judgements") or []:
                flags[side].update(j.get("flags") or [])
        asked_pairs = sh.get("asked") or []
        lineages.add(sh.get("lineage"))
        live_ans = _live_answers(req, asked_pairs)
        shadow_calls = sh.get("calls") or []
        unanswered = {tuple(x) for x in sh.get("unanswered") or []}
        ev_live = {tuple(sorted((a, b))): (a, b, j) for a, b, j in _live_supersessions(req)}
        ev_shadow = {tuple(sorted((a, b))): (a, b, p) for a, b, p in _shadow_supersessions(sh)}
        for key in set(ev_live) | set(ev_shadow):
            subj, cand, _ = ev_live.get(key) or ev_shadow[key]
            entry = {
                "event_id": event_id,
                "project": slug,
                "subject": subj,
                "candidate": cand,
                "live": _side(
                    _judgement(req.get("judgements") or [], subj, cand), live_ans.get((subj, cand))
                ),
                "shadow": {
                    **_side(
                        _judgement(sh.get("judgements") or [], subj, cand),
                        _shadow_answer(shadow_calls, subj, cand),
                        unanswered=(subj, cand) in unanswered,
                    ),
                    "verifier": _shadow_verdict(shadow_calls, subj, cand),
                },
            }
            if key in ev_live:
                live_all[(event_id, *key)] = entry
            if key in ev_shadow:
                shadow_all[(event_id, *key)] = entry
        n_unanswered += len(unanswered)
    live_only = [v for k, v in sorted(live_all.items()) if k not in shadow_all]
    shadow_only = [v for k, v in sorted(shadow_all.items()) if k not in live_all]
    if live_only or shadow_only:
        items = await _items(
            conn, {c for d in live_only + shadow_only for c in (d["subject"], d["candidate"])}, with_texts
        )
        for d in live_only + shadow_only:
            d["items"] = {c: items.get(c) for c in (d["subject"], d["candidate"])}
    cur = await conn.execute(
        """
        SELECT profile, task, outcome, count(*), coalesce(sum(cost_usd), 0),
               percentile_disc(0.95) WITHIN GROUP (ORDER BY latency_ms)
          FROM llm_calls
         WHERE lineage = ANY(%(lineages)s::uuid[])  -- exactly the shadow runs reported here
         GROUP BY profile, task, outcome ORDER BY profile, task, outcome
        """,
        {"lineages": sorted(x for x in lineages if x)},
    )
    ledger = [
        {"profile": p, "task": t, "outcome": o, "calls": int(n), "usd": float(u), "p95_ms": lat}
        for p, t, o, n, u, lat in await cur.fetchall()
    ]
    return {
        "jobs": len(rows),
        "from": first.isoformat() if first else None,
        "to": last.isoformat() if last else None,
        "pairs_asked": asked,
        "pairs_unanswered": n_unanswered,
        "shadow_skipped": dict(skipped),
        "shadow_errors": errors,
        "attempts": {
            k: {
                "n": v["n"],
                "outcomes": dict(v["outcomes"]),
                "p50_ms": _pct(v["lat"], 0.5),
                "p95_ms": _pct(v["lat"], 0.95),
                "tokens": {"in": v["in"], "cached": v["cached"], "out": v["out"]},
                "cost": round(v["cost"], 6),
                **({"failures": v["failures"]} if v.get("failures") else {}),
            }
            for k, v in sorted(attempts.items())
        },
        "guards": {k: dict(v) for k, v in guards.items()},
        "flags": {k: dict(v) for k, v in flags.items()},
        "live_supersessions": len(live_all),
        "shadow_supersessions": len(shadow_all),
        "both": [list(k) for k in sorted(set(live_all) & set(shadow_all))],  # (event_id, a, b)
        "live_only": live_only,
        "shadow_only": shadow_only,
        "ledger": ledger,
    }


async def pair(conn: AsyncConnection, a: str, b: str) -> dict[str, Any]:
    """Every job that asked the pair ``a``/``b``: the exact relate input of the call that judged it
    (the subject and that call's chunk of candidates, in order, rebuilt from the immutable versions;
    the rules are named by clue and the system prompt by version), both answers and judgements, the
    shadow verifier's verdict, and why the shadow may have stopped."""
    want = {a, b}
    out: list[dict[str, Any]] = []
    for event_id, at, slug, req in await _rows(conn, None, None):
        sh = req.get("shadow") or {}
        asked = sh.get("asked") or []
        hit = [x for x in asked if {x[0], x[1]} == want]
        if not hit:
            continue
        subj, cand, cross = hit[0]
        chunk = next(c for s_, c in _chunks(asked) if s_ == subj and cand in c)
        crosses = {x[1]: bool(x[2]) for x in asked if x[0] == subj}
        sid = int(subj[1:])
        subjects = await lq.load_subjects(conn, [sid])
        cands = await lq.load_candidates(conn, [int(c[1:]) for c in chunk])
        missing = [c for c in chunk if int(c[1:]) not in cands]
        s_row = subjects.get(sid)
        payload = (
            relate_payload(s_row, [(cands[int(c[1:])], crosses[c]) for c in chunk])
            if s_row is not None and not missing
            else None
        )
        live_calls = [c for c in _calls(req) if c.get("task") == "relate"]
        shadow_calls = sh.get("calls") or []
        unanswered = {tuple(x) for x in sh.get("unanswered") or []}
        out.append(
            {
                "event_id": event_id,
                "at": at.isoformat(),
                "project": slug,
                "subject": subj,
                "candidate": cand,
                "cross": cross,
                "call_chunk": chunk,
                "missing_versions": missing,
                "rules": req.get("rules") or [],
                "prompts": {
                    "live": sorted({str(c.get("prompt_version")) for c in live_calls}),
                    "shadow": sh.get("relate_version"),
                },
                "relate_input": payload,
                "live": _side(
                    _judgement(req.get("judgements") or [], subj, cand),
                    _live_answers(req, asked).get((subj, cand)),
                ),
                "shadow": {
                    **_side(
                        _judgement(sh.get("judgements") or [], subj, cand),
                        _shadow_answer(shadow_calls, subj, cand),
                        unanswered=(subj, cand) in unanswered,
                    ),
                    "verifier": _shadow_verdict(shadow_calls, subj, cand),
                    "error": sh.get("error"),
                    "seconds": sh.get("seconds"),
                },
            }
        )
    return {"pair": [a, b], "jobs": out}


__all__ = ["SHEET_CHARS", "pair", "report"]
