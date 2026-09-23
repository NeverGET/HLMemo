"""W2b ``write_review``: placement, contradiction and the cross-project check of the new versions
of one committed ``write`` / ``call_the_day`` / ``import`` event (PHASE2-4-ROADMAP W2b).

Enqueued in the SAME transaction as the event (``librarian/trigger.py``) as
``librarian_write:<event_id>`` (≤ ``MAX_VERSIONS`` versions per job; larger batches are split at
enqueue). Plan (reads in short committed transactions; provider calls outside any transaction):

1. **Privacy** (default-deny, D-062): the subjects pass ``privacy.gate`` at plan time and every
   prompt's items pass it again immediately before EACH provider attempt.
2. **Embeddings**: a subject whose embed job is still in flight hands the job back (``NotReady``,
   no attempt consumed) for up to ``HLM_LIBRARIAN_EMBED_WAIT_S``; after that it runs lexical-only.
3. **Candidates** (``librarian/candidates.py``): the triggering device's CURRENT read grants ∩ the
   enqueue-time ``question`` capability, its visible scopes, never ``device:*``: same-project
   top-8 of compatible kinds + cross-project top-5 lesson/experience/fact, after the
   cosine/lexical drop rule. The candidate set is recorded in the audit payload.
4. **Placement** (``place/v1``, one call for all subjects): importance / stability for the fields
   the client left unset, a topic hint, ≤ 5 tags → ``version_signals`` (``signal_upsert``).
   Session notes, project cards and document chunks get placement only.
5. **Relation** (``relate/v1``): one call per subject and ≤ 8 candidates. D-067 guards
   (``librarian/guards.py``): cited ids only, quote evidence, temporal consistency, calibrated
   confidence tiers, first-class abstention.
6. **Verifier** (``relate_verify/v1``) for every high-impact judgement (contradicts + supersedes,
   cross-project duplicate/refines) — batched ≤ 8 pairs, by default on the OTHER profile of the
   chain (``HLM_LIBRARIAN_VERIFIER``); without agreement the proposal is downgraded or dropped.
7. **Resolution** into proposals (roadmap W2b step 4): duplicate → ``relates_to{dup:true}``;
   refines → ``relates_to``; contradicts+supersedes → ``contradicts`` + ``supersedes`` links and
   the bi-temporal close of the replaced item (``version_close``: ``valid_to`` = the newer
   item's ``valid_from``; nothing deleted). ``auto_ok`` marks the auto-rule class: high
   confidence, verified quotes, verifier agreement, same home project, identical
   ``device_scope``, neither item pinned, no ``experience``, the newer ``valid_from`` ≥ the
   older's. A cross-project duplicate/refinement is a ``widen_scope`` question in every role.

The worker applies the plan (role ladder, CC-3 recheck, one ``librarian`` event).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from hlmemo.core.temporal import fmt_ts
from hlmemo.db import librarian_queries as lq
from hlmemo.librarian import candidates as cands
from hlmemo.librarian import guards, privacy
from hlmemo.librarian.errors import AuthorityLost, NotReady, PrivacyDenied, SchemaFail
from hlmemo.librarian.memory import load_rules
from hlmemo.librarian.prompts import load_task
from hlmemo.librarian.tasks import Plan, Proposal, user_message

OP = "write_review"
MAX_VERSIONS = 4
PAIRS_PER_CALL = 8
NEW_TEXT_CHARS = 3000
OLD_TEXT_CHARS = 1500
PLACE_TEXT_CHARS = 2000

_SUBJECT_OUTCOME = {
    privacy.POLICY_OFF: "policy_off",
    privacy.DEVICE_SCOPED: "skipped_device_scope",
    privacy.NOT_CURRENT: "stale_subject",
}


def _cut(text: str, n: int) -> str:
    return text if len(text) <= n else text[:n] + " …"


def _clue(vid: int) -> str:
    return f"v{vid}"


def verifier_chain(w: Any, answered_profile: str) -> list[Any]:
    """The verifier's profile chain: the OTHER profile(s) first (``cross``), the answering one as
    the last resort; ``self`` (or a one-profile chain) = the answering profile only."""
    chain = list(w.provider.chain)
    same = [p for p in chain if p.name == answered_profile] or chain[:1]
    if getattr(w.settings, "librarian_verifier", "cross") == "self" or len(chain) == 1:
        return same
    return [p for p in chain if p.name != answered_profile] + same


def _link(rel: str, src: Any, dst: Any, props: dict[str, Any], assessed: dict[str, int]) -> dict[str, Any]:
    return {
        "op": "link_insert",
        "rel": rel,
        "src_logical_id": src.logical_id,
        "dst_logical_id": dst.logical_id,
        "dst_version_id": None,
        "valid_from": fmt_ts(src.valid_from),
        "props": dict(props),
        "project_ids": list(src.project_ids),
        "dst_project_ids": list(dst.project_ids),
        "assessed": assessed,
    }


def _close(item: Any, cut: datetime, assessed: dict[str, int]) -> dict[str, Any]:
    """The bi-temporal close of ``item`` at ``cut`` (``valid_to``; nothing deleted)."""
    return {
        "op": "version_close",
        "logical_id": item.logical_id,
        "version_id": item.version_id,
        "valid_to": fmt_ts(cut),
        "project_ids": list(item.project_ids),
        "assessed": assessed,
    }


# --------------------------------------------------------------------------- prompt payloads
# Pure builders shared by the handler and the live gate (eval/live/run_w2b.py), so the gate
# measures exactly the production prompts. Rows need version_id, kind, title, body, valid_from
# (and tags for placement).
def place_payload(rows: list[Any]) -> dict[str, Any]:
    return {
        "items": [
            {
                "id": _clue(r.version_id),
                "kind": r.kind,
                "title": r.title,
                "text": _cut(r.body, PLACE_TEXT_CHARS),
                "tags": list(r.tags),
            }
            for r in rows
        ]
    }


def relate_payload(s: Any, cands: list[tuple[Any, bool]]) -> dict[str, Any]:
    """``cands`` = ``[(candidate row, cross_project)]`` (≤ ``PAIRS_PER_CALL``)."""
    return {
        "new": {
            "id": _clue(s.version_id),
            "kind": s.kind,
            "title": s.title,
            "text": _cut(s.body, NEW_TEXT_CHARS),
            "t_valid": fmt_ts(s.valid_from),
        },
        "existing": [
            {
                "id": _clue(c.version_id),
                "kind": c.kind,
                "title": c.title,
                "text": _cut(c.body, OLD_TEXT_CHARS),
                "t_valid": fmt_ts(c.valid_from),
                "project": "other" if cross else "same",
            }
            for c, cross in cands
        ],
    }


def relate_texts(s: Any, cands: list[tuple[Any, bool]]) -> list[guards.PairText]:
    """What the model saw of each pair (the quote guard checks against exactly this)."""
    return [
        guards.PairText(
            _clue(c.version_id),
            f"{s.title}\n{_cut(s.body, NEW_TEXT_CHARS)}",
            f"{c.title}\n{_cut(c.body, OLD_TEXT_CHARS)}",
            s.valid_from,
            c.valid_from,
        )
        for c, _cross in cands
    ]


def _side(row: Any, is_new: bool, project: str) -> dict[str, Any]:
    return {
        "title": row.title,
        "text": _cut(row.body, NEW_TEXT_CHARS if is_new else OLD_TEXT_CHARS),
        "t_valid": fmt_ts(row.valid_from),
        "project": project,
    }


def verify_payload(pairs: list[tuple[Any, Any, bool]], start: int = 0) -> list[dict[str, Any]]:
    """``relate_verify`` items for ``[(subject, candidate, cross_project)]``: chronological (A never
    later than B; on a tie the new subject is B), ids ``p<start+1>`` …; each side says whether it
    lives in the new item's project (``same``) or another one (``other``, Sol 41 #6)."""
    items = []
    for k, (s, c, cross) in enumerate(pairs):
        new_is_b = s.valid_from >= c.valid_from
        first, second = (c, s) if new_is_b else (s, c)
        items.append(
            {
                "id": f"p{start + k + 1}",
                "A": _side(first, first is s, "same" if first is s or not cross else "other"),
                "B": _side(second, second is s, "same" if second is s or not cross else "other"),
            }
        )
    return items


async def readable_rules(conn: Any, rules: list[dict[str, Any]], allowed: list[int]) -> list[dict[str, Any]]:
    """Working-memory rules whose clue refs all point to versions of projects the triggering device
    may read now: a rule never carries another project's clue ids into a prompt (Sol 41 #1)."""
    refs = sorted({int(r[1:].split(".")[0]) for rule in rules for r in rule.get("refs") or []})
    if not refs:
        return rules
    cur = await conn.execute(
        "SELECT version_id, project_ids FROM memory_versions WHERE version_id = ANY(%s)", (refs,)
    )
    ok = {int(v) for v, pids in await cur.fetchall() if set(pids) <= set(allowed)}
    return [rule for rule in rules if all(int(r[1:].split(".")[0]) in ok for r in rule.get("refs") or [])]


class _Pair:
    __slots__ = ("cand", "cross", "judgement", "judgement_profile", "scored", "subject")

    def __init__(self, subject: lq.SubjectRow, cand: lq.CandRow, cross: bool, scored: cands.Scored) -> None:
        self.subject = subject
        self.cand = cand
        self.cross = cross
        self.scored = scored
        self.judgement: guards.Judgement | None = None
        self.judgement_profile = ""  # the profile that answered the relate call (verifier choice)


class WriteReview:
    op = OP

    async def plan(self, w: Any, job: Any) -> Plan:
        p = job.payload
        caps = p.get("capabilities") or {}
        entries = [e for e in p.get("versions", [])][:MAX_VERSIONS]
        vids = [int(e["version_id"]) for e in entries]
        extra: dict[str, Any] = {
            "event_id": p.get("event_id"),
            "trigger": p.get("trigger"),
            "subjects": [_clue(v) for v in vids],
        }
        if p.get("replan_of"):
            extra["replan_of"] = p["replan_of"]
        verdict, _items = await privacy.gate(w.connect, caps, vids)
        if not verdict.device_ok:
            return Plan(OP, "authority_lost", caps, request_extra=extra)
        denied = {_clue(v): verdict.denied[v] for v in vids if not verdict.allowed(v)}
        if denied:
            extra["denied_subjects"] = denied
        entries = [e for e in entries if verdict.allowed(int(e["version_id"]))]
        if not entries:
            reason = next(iter(denied.values()))
            return Plan(OP, _SUBJECT_OUTCOME.get(reason, "denied"), caps, request_extra=extra)

        device_id = int(caps.get("trigger_device_id") or 0)
        async with await w.connect() as conn:
            subjects = await lq.load_subjects(conn, [int(e["version_id"]) for e in entries])
            cur = await conn.execute("SELECT clock_timestamp()")
            (now,) = await cur.fetchone()
            full = [
                subjects[int(e["version_id"])]
                for e in entries
                if subjects[int(e["version_id"])].kind not in cands.PLACEMENT_ONLY
                and subjects[int(e["version_id"])].kind in cands.COMPATIBLE
            ]
            lexical_only: list[str] = []
            for s in full:
                state = await lq.embedding_state(conn, s.version_id)
                if state == "pending":
                    age = (now - s.recorded_at).total_seconds()
                    if age < w.settings.librarian_embed_wait_s:
                        await conn.commit()
                        raise NotReady(
                            f"E_NOT_READY embeddings of v{s.version_id}",
                            retry_after_s=5.0 if age < 30 else 20.0,
                        )
                if state != "ready":
                    lexical_only.append(_clue(s.version_id))
            trusted, device_class, allowed = await lq.readable_projects(
                conn, device_id, [int(x) for x in caps.get("question") or []]
            )
            if not trusted:
                await conn.commit()
                return Plan(OP, "authority_lost", caps, request_extra=extra)
            scopes = ["all", f"class:{device_class}"]
            pairs, cand_audit, dropped = await self._candidates(conn, full, allowed, scopes)
            rules = await load_rules(
                conn,
                max_rules=w.settings.librarian_memory_rules,
                max_tokens=w.settings.librarian_memory_tokens,
                meter=w.meter,
                redactor=w.provider.redactor,
            )
            rules = await readable_rules(conn, rules, allowed)
            await conn.commit()
        if p.get("owner_note"):  # a custom answer's note: this job only (never working memory)
            note = w.provider.redactor.text(str(p["owner_note"]))[:500]
            rules = [*rules, {"clue": "owner", "rule": f"Owner note for this re-check: {note}", "refs": []}]
        if lexical_only:
            extra["lexical_only"] = lexical_only
        extra["candidates"] = cand_audit
        extra["dropped_by_rule"] = dropped
        extra["rules"] = [r["clue"] for r in rules]
        plan = Plan(OP, "proposed", caps, request_extra=extra)
        counters: dict[str, int] = {}

        async def precheck_for(ids: list[int]) -> Any:
            async def precheck() -> None:  # before EVERY provider attempt (D-062)
                again, _ = await privacy.gate(w.connect, caps, ids)
                if not again.device_ok:
                    raise AuthorityLost("E_AUTHORITY_LOST")
                if not all(again.allowed(v) for v in ids):
                    raise PrivacyDenied("E_PRIVACY_DENIED")

            return precheck

        try:
            placement = await self._placement(w, job, plan, entries, subjects, rules, precheck_for, counters)
            await self._relate(w, job, plan, pairs, rules, precheck_for, counters)
            await self._verify(w, job, plan, pairs, precheck_for, counters)
        except AuthorityLost:
            return Plan(OP, "authority_lost", caps, calls=plan.calls, request_extra=plan.request_extra)

        plan.signals = self._signals(entries, subjects, placement)
        plan.proposals = self._proposals(pairs)
        plan.request_extra["judgements"] = [
            {"subject": _clue(pr.subject.version_id), **pr.judgement.audit()}
            for pr in pairs
            if pr.judgement is not None and (pr.judgement.relation != "none" or pr.judgement.flags)
        ]
        plan.request_extra["guards"] = {k: v for k, v in sorted(counters.items()) if v}
        if not plan.signals and not plan.proposals:
            plan.outcome = "no_change"
        return plan

    # ------------------------------------------------------------------ candidates
    async def _candidates(
        self, conn: Any, full: list[lq.SubjectRow], allowed: list[int], scopes: list[str]
    ) -> tuple[list[_Pair], list[dict[str, Any]], int]:
        pairs: list[_Pair] = []
        audit: list[dict[str, Any]] = []
        seen: set[frozenset[int]] = set()
        dropped_total = 0
        for s in full:
            vecs = await lq.subject_vectors(conn, s.version_id)
            s_terms = cands.subject_terms(s.title, s.body)
            lex_text = " ".join(s_terms[: cands.LEX_TERMS])
            kinds_same = cands.COMPATIBLE[s.kind]
            kinds_cross = tuple(k for k in cands.CROSS_KINDS if k in kinds_same)
            for cross, kinds, top in (
                (False, kinds_same, cands.SAME_TOP),
                (True, kinds_cross, cands.CROSS_TOP),
            ):
                if not kinds:
                    continue
                args = {
                    "cross": cross,
                    "home": s.project_id,
                    "allowed": allowed,
                    "scopes": scopes,
                    "kinds": list(kinds),
                    "self_lid": s.logical_id,
                    "limit": cands.LIST_LIMIT,
                }
                best: dict[int, float] = {}
                for vec in vecs:
                    for vid, cos in await lq.vector_list(conn, vec, **args):
                        best[vid] = max(best.get(vid, -1.0), cos)
                vector = sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))[: cands.LIST_LIMIT]
                lexical = await lq.lexical_list(conn, lex_text, **args)
                fused = cands.fuse(vector, lexical)
                if not fused:
                    continue
                missing = [f.version_id for f in fused if f.cos is None]
                cos_more = await lq.cosines(conn, vecs, missing)
                for f in fused:
                    if f.cos is None and f.version_id in cos_more:
                        f.cos = cos_more[f.version_id]
                rows = await lq.load_candidates(conn, [f.version_id for f in fused])
                cand_terms = {
                    vid: cands.content_terms(cands.subject_terms(r.title, r.body)) for vid, r in rows.items()
                }
                cands.mark_lexical_hits(cands.content_terms(s_terms), cand_terms, fused)
                kept, dropped = cands.select(fused, top)
                dropped_total += len(dropped)
                for f in kept:
                    c = rows[f.version_id]
                    key = frozenset((s.logical_id, c.logical_id))
                    if key in seen:
                        continue
                    seen.add(key)
                    pairs.append(_Pair(s, c, cross, f))
                    audit.append(
                        {
                            "subject": _clue(s.version_id),
                            "id": _clue(c.version_id),
                            "cross": cross,
                            "cos": None if f.cos is None else round(f.cos, 4),
                            "lex": f.lexical_hit,
                        }
                    )
        return pairs, audit, dropped_total

    # ------------------------------------------------------------------ placement
    async def _placement(
        self,
        w: Any,
        job: Any,
        plan: Plan,
        entries: list[dict[str, Any]],
        subjects: dict[int, lq.SubjectRow],
        rules: list[dict[str, Any]],
        precheck_for: Any,
        counters: dict[str, int],
    ) -> dict[str, dict[str, Any]]:
        ids = [int(e["version_id"]) for e in entries]
        items = place_payload([subjects[v] for v in ids])["items"]
        try:
            res = await w.provider.complete(
                load_task("place"),
                user_message("place", {"items": items}, rules),
                job_id=job.job_id,
                lineage=job.lineage,
                precheck=await precheck_for(ids),
            )
        except PrivacyDenied:
            counters["placement_denied"] = counters.get("placement_denied", 0) + 1
            return {}
        except SchemaFail:
            counters["placement_schema_fail"] = counters.get("placement_schema_fail", 0) + 1
            return {}
        plan.calls.append(res.audit(w.provider.redactor))
        out, counts = guards.check_placement(
            res.output, [i["id"] for i in items], redact=w.provider.redactor.text
        )
        for k, v in counts.items():
            counters[f"placement_{k}"] = counters.get(f"placement_{k}", 0) + v
        return out

    # ------------------------------------------------------------------ relation
    async def _relate(
        self,
        w: Any,
        job: Any,
        plan: Plan,
        pairs: list[_Pair],
        rules: list[dict[str, Any]],
        precheck_for: Any,
        counters: dict[str, int],
    ) -> None:
        task = load_task("relate")
        by_subject: dict[int, list[_Pair]] = {}
        for pr in pairs:
            by_subject.setdefault(pr.subject.version_id, []).append(pr)
        for group in by_subject.values():
            s = group[0].subject
            for i in range(0, len(group), PAIRS_PER_CALL):
                chunk = group[i : i + PAIRS_PER_CALL]
                ids = [s.version_id, *(pr.cand.version_id for pr in chunk)]
                payload = relate_payload(s, [(pr.cand, pr.cross) for pr in chunk])
                try:
                    res = await w.provider.complete(
                        task,
                        user_message("relate", payload, rules),
                        job_id=job.job_id,
                        lineage=job.lineage,
                        precheck=await precheck_for(ids),
                    )
                except PrivacyDenied:
                    counters["relate_denied"] = counters.get("relate_denied", 0) + 1
                    continue
                except SchemaFail:
                    counters["relate_schema_fail"] = counters.get("relate_schema_fail", 0) + 1
                    continue
                plan.calls.append(res.audit(w.provider.redactor))
                texts = relate_texts(s, [(pr.cand, pr.cross) for pr in chunk])
                judged, counts = guards.check_relations(res.output, texts, redact=w.provider.redactor.text)
                for k, v in counts.items():
                    counters[k] = counters.get(k, 0) + v
                for pr, j in zip(chunk, judged, strict=True):
                    pr.judgement = j
                    pr.judgement_profile = res.profile

    # ------------------------------------------------------------------ verifier (D-067)
    async def _verify(
        self, w: Any, job: Any, plan: Plan, pairs: list[_Pair], precheck_for: Any, counters: dict[str, int]
    ) -> None:
        todo: list[tuple[_Pair, str]] = []
        for pr in pairs:
            j = pr.judgement
            kind = None if j is None else guards.high_impact(j, cross_project=pr.cross)
            if kind is not None:
                todo.append((pr, kind))
        if not todo:
            return
        task = load_task("relate_verify")
        by_profile: dict[str, list[tuple[_Pair, str]]] = {}
        for pr, kind in todo:
            by_profile.setdefault(pr.judgement_profile, []).append((pr, kind))
        n = 0
        for profile, group in by_profile.items():
            chain = verifier_chain(w, profile)
            for i in range(0, len(group), PAIRS_PER_CALL):
                chunk = group[i : i + PAIRS_PER_CALL]
                items = verify_payload([(pr.subject, pr.cand, pr.cross) for pr, _kind in chunk], n)
                ids: list[int] = [
                    v for pr, _kind in chunk for v in (pr.subject.version_id, pr.cand.version_id)
                ]
                n += len(chunk)
                answers: dict[str, dict[str, Any]] = {}
                try:
                    res = await w.provider.complete(
                        task,
                        user_message("relate_verify", {"pairs": items}),
                        job_id=job.job_id,
                        lineage=job.lineage,
                        chain=chain,
                        precheck=await precheck_for(sorted(set(ids))),
                    )
                    plan.calls.append(res.audit(w.provider.redactor))
                    answers = guards.check_verifications(res.output, [it["id"] for it in items])
                except (PrivacyDenied, SchemaFail):
                    counters["verify_no_answer"] = counters.get("verify_no_answer", 0) + 1
                for it, (pr, kind) in zip(items, chunk, strict=True):
                    assert pr.judgement is not None
                    new_is_b = pr.subject.valid_from >= pr.cand.valid_from
                    guards.apply_verification(pr.judgement, kind, answers.get(it["id"]), new_is_b=new_is_b)
                    counters["verified" if pr.judgement.verification["agreed"] else "verify_disagreed"] = (
                        counters.get(
                            "verified" if pr.judgement.verification["agreed"] else "verify_disagreed", 0
                        )
                        + 1
                    )

    # ------------------------------------------------------------------ plan building
    @staticmethod
    def _signals(
        entries: list[dict[str, Any]],
        subjects: dict[int, lq.SubjectRow],
        placement: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        out = []
        for e in entries:
            vid = int(e["version_id"])
            s = subjects[vid]
            model = placement.get(_clue(vid))
            client_imp = e.get("client_importance")
            importance = client_imp if client_imp is not None else (model or {}).get("importance")
            src = "client" if client_imp is not None else ("librarian" if model else None)
            stability = None if e.get("client_stability") else (model or {}).get("stability")
            hint = (model or {}).get("topic_hint")
            tags = guards.clean_tags((model or {}).get("tags_add"), list(s.tags))
            if importance is None and stability is None and not hint and not tags:
                continue
            out.append(
                {
                    "op": "signal_upsert",
                    "version_id": vid,
                    "importance": importance,
                    "importance_src": src if importance is not None else None,
                    "stability_suggested": stability,
                    "topic_hint": hint,
                    "tags_add": tags,
                    "project_ids": list(s.project_ids),
                }
            )
        return out

    @staticmethod
    def _proposals(pairs: list[_Pair]) -> list[Proposal]:
        out: list[Proposal] = []
        for pr in pairs:
            j = pr.judgement
            if j is None or not j.raised:
                continue
            s, c = pr.subject, pr.cand
            assessed = {str(s.logical_id): s.version_id, str(c.logical_id): c.version_id}
            clues = [_clue(s.version_id), _clue(c.version_id)]
            touched = sorted(set(s.project_ids) | set(c.project_ids))
            same_class = (
                c.project_id == s.project_id
                and c.device_scope == s.device_scope
                and not c.pinned
                and not s.pinned
                and "experience" not in (c.kind, s.kind)
            )
            meta: dict[str, Any] = {
                "relation": j.relation,
                "supersedes": j.supersedes,
                "confidence": j.confidence,
                "tier": j.tier,
                "reason": j.reason,
                "quotes": {"new": j.new_quote, "old": j.old_quote},
                "flags": list(j.flags),
                "verification": j.verification,
                "cos": None if pr.scored.cos is None else round(pr.scored.cos, 4),
                "cross_project": pr.cross,
            }
            props = {"by": "librarian", "relation": j.relation, "confidence": j.confidence}

            if pr.cross and j.relation in ("duplicate", "refines"):
                action = {
                    "op": "widen_scope",
                    "logical_id": c.logical_id,
                    "version_id": c.version_id,
                    "add_project_ids": [s.project_id],
                    "assessed": assessed,
                }
                out.append(Proposal("widen_scope", [action], False, assessed, clues, touched, meta))
            elif j.relation in ("duplicate", "refines"):
                extra = {"dup": True} if j.relation == "duplicate" else {}
                auto = j.tier == "action" and same_class
                out.append(
                    Proposal(
                        "link",
                        [_link("relates_to", s, c, {**props, **extra}, assessed)],
                        auto,
                        assessed,
                        clues,
                        touched,
                        meta,
                    )
                )
            elif j.relation == "contradicts":
                actions = [_link("contradicts", s, c, props, assessed)]
                auto = False
                verified = bool((j.verification or {}).get("agreed"))
                if j.supersedes == "new":
                    actions.append(_link("supersedes", s, c, props, assessed))
                    if s.valid_from > c.valid_from:
                        actions.append(_close(c, s.valid_from, assessed))
                    auto = (
                        j.tier == "action"
                        and verified
                        and same_class
                        and s.valid_from >= c.valid_from
                        and "quote_unverified" not in j.flags
                    )
                elif j.supersedes == "old":
                    actions.append(_link("supersedes", c, s, props, assessed))
                    if c.valid_from > s.valid_from:
                        actions.append(_close(s, c.valid_from, assessed))
                out.append(Proposal("contradiction", actions, auto, assessed, clues, touched, meta))
        return out


__all__ = [
    "MAX_VERSIONS",
    "OP",
    "WriteReview",
    "place_payload",
    "relate_payload",
    "relate_texts",
    "verifier_chain",
    "verify_payload",
]
