"""E4 (lessons v2, D-222 / D-225): mistake episodes -> clusters -> lesson packets, and the
deterministic check of an E4 lesson.

Input: ``<E4 private dir>/episodes.jsonl``, written by the private mining step. One JSON object per
episode: ``episode_id``, ``slug``, ``group`` (its independence group: forks of one product share a
group), ``source``, ``title``, ``symptom``, ``fix``, ``lesson`` (when/do/avoid), ``stack``,
``versions``, ``evidence`` (verbatim quotes, each with ``date``, ``speaker``, ``slots``),
``first_date``, ``last_date``. No episode content and no project name is tracked in this repository.

Clustering (pure, deterministic, unit-tested on synthetic vectors):

* each episode's lesson + symptom text is embedded with the product's pinned E5 model
  (``hlmemo.core.embedder``, ``query:`` prefix: a symmetric comparison);
* the vectors are mean-centred (``center``: E5 is anisotropic, unrelated texts sit at a high cosine
  similarity; centring removes the shared direction) before the cosine distance;
* agglomerative clustering, average linkage, cosine distance (distances rounded to 1e-6, ties broken
  by the smallest member index, so a run is reproducible bit for bit);
* the merge threshold is tuned ONCE on a held-out split (``holdout_frac`` of the episodes, chosen by
  a seeded hash) by the cosine silhouette over a fixed grid (ties: the smaller threshold), then frozen
  and applied to all episodes. The grid, the split and the scores are recorded in ``clusters.json``;
* eligibility: ``cross_project`` = at least ``min_cross_episodes`` episodes from at least
  ``min_cross_groups`` independence groups; ``project_local`` = one group and at least
  ``min_local_episodes`` episodes (a separate packet type, never cross-project).

The check (``check_lesson``): every quote must lie inside ONE evidence quote of the episode it cites
(whitespace-normalised, case-sensitive); the group count, the recurrence count (distinct cited
episodes) and the first/last seen dates are recomputed by code from the cited episodes; a
cross-project lesson must cite at least two independence groups; invented ids/hashes and versions
are flagged.

E4B (lessons v2b) reuses this module: every episode also carries a ``status`` (``resolved`` only when
its transcript shows the fix verified, by a quote; else ``unknown``) and a ``model_era`` (the dominant
assistant model id of its chunk; ``unknown`` for owner prompts). The threshold is FROZEN from E4 (no
tuning), and an eligible cluster whose members all belong to ONE already-imported E4 lesson packet
(matched by episode ids, merged ids included) is excluded as "already covered". The check also
recounts the lesson's model eras from the cited episodes and allows a status only with its evidence
(``status_supported``).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import al_common as C
import numpy as np

CROSS = "cross_project"
LOCAL = "project_local"
COVERED = "already_covered"
EPISODES_FILE = "episodes.jsonl"
CLUSTERS_FILE = "clusters.json"
COVERED_FILE = "covered.json"
_DEC = 6  # distance rounding (reproducibility across BLAS orders)
E4_FLAGS = frozenset(
    {
        "no_evidence",
        "unknown_source",
        "quote_too_short",
        "quote_not_verbatim",
        "invented_ref",
        "invented_version",
        "under_evidenced",
        "group_count_mismatch",
        "recurrence_mismatch",
        "dates_mismatch",
        "era_mismatch",
        "status_unsupported",
    }
)


# --------------------------------------------------------------------------- paths + input
def episodes_path() -> Path:
    return C.private_dir() / EPISODES_FILE


def clusters_path() -> Path:
    return C.private_dir() / CLUSTERS_FILE


def covered_path() -> Path:
    return C.private_dir() / COVERED_FILE


def load_episodes(path: Path | None = None) -> list[dict[str, Any]]:
    rows = C.read_jsonl(path or episodes_path())
    if not rows:
        raise C.HarnessError(f"no E4 episodes at {path or episodes_path()} (run the private mining step)")
    ids = [r["episode_id"] for r in rows]
    if len(set(ids)) != len(ids):
        raise C.HarnessError("duplicate episode ids in the E4 input")
    return sorted(rows, key=lambda r: r["episode_id"])


def embed_text(ep: dict[str, Any]) -> str:
    les = ep["lesson"]
    return f"When: {les['when']} Do: {les['do']} Avoid: {les['avoid']} Symptom: {ep['symptom']}"


def e5_embed(texts: Sequence[str]) -> np.ndarray:
    """The product embedder (pinned E5, ``$HLM_MODELS_DIR``), ``query:`` prefix, L2-normalised."""
    from hlmemo.core.embedder import Embedder, default_model_dir

    emb = Embedder(default_model_dir())
    try:
        return np.asarray(emb.embed_queries(list(texts)), dtype=np.float64)
    finally:
        emb.close()


# --------------------------------------------------------------------------- clustering (pure)
def cosine_distances(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    x = x / np.clip(np.linalg.norm(x, axis=1, keepdims=True), 1e-12, None)
    d = np.clip(1.0 - x @ x.T, 0.0, 2.0)
    d = (d + d.T) / 2.0
    np.fill_diagonal(d, 0.0)
    return np.round(d, _DEC)


def agglomerate(d: np.ndarray, threshold: float) -> list[list[int]]:
    """Average-linkage agglomerative clustering of the distance matrix ``d``: merge the closest pair
    (ties: smallest member indices) while its distance is <= ``threshold``. Clusters as sorted index
    lists, ordered by their smallest member."""
    n = d.shape[0]
    clusters: dict[int, list[int]] = {i: [i] for i in range(n)}
    dist = {(i, j): float(d[i, j]) for i in range(n) for j in range(i + 1, n)}
    thr = round(float(threshold), _DEC)
    while len(clusters) > 1:
        (a, b), best = min(
            dist.items(), key=lambda kv: (round(kv[1], 9), clusters[kv[0][0]][0], clusters[kv[0][1]][0])
        )
        if round(best, _DEC) > thr:
            break
        na, nb = len(clusters[a]), len(clusters[b])
        merged = sorted(clusters[a] + clusters[b])
        del clusters[b]
        clusters[a] = merged
        for k in list(clusters):
            if k == a:
                continue
            dak = dist.pop((min(a, k), max(a, k)))
            dbk = dist.pop((min(b, k), max(b, k)))
            dist[(min(a, k), max(a, k))] = (na * dak + nb * dbk) / (na + nb)  # Lance-Williams (average)
        dist.pop((min(a, b), max(a, b)))
    return sorted(clusters.values(), key=lambda c: c[0])


def silhouette(d: np.ndarray, clusters: list[list[int]]) -> float | None:
    """Mean cosine silhouette (a singleton scores 0); None for fewer than 2 or n clusters."""
    n = d.shape[0]
    if len(clusters) < 2 or len(clusters) >= n:
        return None
    scores = []
    for own in clusters:
        for i in own:
            if len(own) == 1:
                scores.append(0.0)
                continue
            a = float(np.mean([d[i, j] for j in own if j != i]))
            b = min(float(np.mean(d[i, other])) for other in clusters if other is not own)
            m = max(a, b)
            scores.append(0.0 if m == 0 else (b - a) / m)
    return round(float(np.mean(scores)), _DEC)


def grid_values(grid: dict[str, float]) -> list[float]:
    start, stop, step = float(grid["start"]), float(grid["stop"]), float(grid["step"])
    k = int(math.floor((stop - start) / step + 1e-9))
    return [round(start + i * step, 4) for i in range(k + 1)]


def tune_threshold(d: np.ndarray, grid: list[float]) -> tuple[float, list[dict[str, Any]]]:
    table = []
    for t in grid:
        cl = agglomerate(d, t)
        table.append({"threshold": t, "clusters": len(cl), "silhouette": silhouette(d, cl)})
    scored = [r for r in table if r["silhouette"] is not None]
    if not scored:
        raise C.HarnessError("no threshold of the grid gives a defined silhouette on the held-out split")
    best = min(scored, key=lambda r: (-r["silhouette"], r["threshold"]))
    return best["threshold"], table


def center(x: np.ndarray) -> np.ndarray:
    """Subtract the corpus mean vector (deterministic; no labels involved)."""
    x = np.asarray(x, dtype=np.float64)
    return x - x.mean(axis=0, keepdims=True)


def holdout_split(ids: Sequence[str], frac: float, seed: int | str) -> set[str]:
    order = sorted(ids, key=lambda i: hashlib.sha256(f"{seed}:{i}".encode()).hexdigest())
    m = max(2, math.ceil(frac * len(order)))
    return set(order[:m])


def eligibility(groups: Sequence[str], sel: dict[str, Any]) -> str | None:
    n, g = len(groups), len(set(groups))
    if n >= int(sel["min_cross_episodes"]) and g >= int(sel["min_cross_groups"]):
        return CROSS
    if g == 1 and n >= int(sel["min_local_episodes"]):
        return LOCAL
    return None


def group_count(handles: Sequence[str], group_of: dict[str, str]) -> int:
    return len({group_of[h] for h in handles if h in group_of})


def covered_by(members: Sequence[set[str]], packets: dict[str, list[str]]) -> str | None:
    """The imported lesson packet that already covers a cluster: every member (its id plus its merged
    ids) maps to an episode of that ONE packet. None when any member is new information."""
    old_all = set().union(*map(set, packets.values())) if packets else set()
    olds = [set(m) & old_all for m in members]
    if not members or any(not o for o in olds):
        return None
    union = set().union(*olds)
    for pid in sorted(packets):
        if union <= set(packets[pid]):
            return pid
    return None


def status_supported(status: str, cited: Sequence[dict[str, Any]], current_era: str) -> bool:
    """``unknown`` always; ``resolved`` when every cited episode is resolved; ``historical`` when no
    cited episode is of the current model era; ``active`` only when an episode recurs (starts) after
    another cited episode was resolved."""
    if status == "unknown":
        return True
    if not cited:
        return False
    if status == "resolved":
        return all(s.get("status") == "resolved" for s in cited)
    if status == "historical":
        return all(s.get("model_era") != current_era for s in cited)
    if status == "active":
        return any(
            a.get("status") == "resolved" and b["valid_from"] > a["last_seen"] for a in cited for b in cited
        )
    return False


# --------------------------------------------------------------------------- packets
def _redactor() -> Any:
    from hlmemo.librarian.redact import Redactor

    return Redactor()


def e4_packet(
    packet_id: str,
    packet_type: str,
    episodes: list[dict[str, Any]],
    *,
    cluster: int,
    redactor: Any = None,
    exp: str = C.E4,
    current_era: str | None = None,
) -> dict[str, Any]:
    red = redactor or _redactor()
    srcs = []
    for ep in sorted(episodes, key=lambda e: (e["first_date"], e["episode_id"])):
        quotes = [
            {
                "quote": red.text(q["quote"]),
                "date": q["date"],
                "speaker": q["speaker"],
                "slots": list(q["slots"]),
            }
            for q in ep["evidence"]
        ]
        les = ep["lesson"]
        srcs.append(
            {
                "handle": ep["episode_id"],
                "kind": "episode",
                "project": ep["slug"],
                "group": ep["group"],
                "origin": ep["source"],
                "valid_from": ep["first_date"],
                "last_seen": ep["last_date"],
                "role": "episode",
                "quotable": True,
                "title": red.text(ep["title"]),
                "summary": {
                    "symptom": red.text(ep["symptom"]),
                    "fix": red.text(ep["fix"]),
                    "when": red.text(les["when"]),
                    "do": red.text(les["do"]),
                    "avoid": red.text(les["avoid"]),
                },
                "quotes": quotes,
                "text": "\n".join(q["quote"] for q in quotes),
            }
        )
        if current_era is not None:  # E4B: status and model era of every episode
            srcs[-1]["status"] = ep.get("status", "unknown")
            srcs[-1]["model_era"] = ep.get("model_era", "unknown")
    groups = sorted({s["group"] for s in srcs})
    p: dict[str, Any] = {
        "schema": "al-packet/1",
        "exp": exp,
        "packet_id": packet_id,
        "project": None,
        "sources": srcs,
        "context": {
            "packet_type": packet_type,
            "groups": groups,
            "group_count": len(groups),
            "episodes": len(srcs),
            "first_seen": min(s["valid_from"] for s in srcs),
            "last_seen": max(s["last_seen"] for s in srcs),
        },
        "meta": {"cluster": cluster},
    }
    if current_era is not None:
        p["context"]["current_era"] = current_era
    p["user"] = render_user(p)
    p["user_sha256"] = C.sha256_text(p["user"])
    return p


def render_user(packet: dict[str, Any]) -> str:
    ctx = packet["context"]
    if ctx["packet_type"] == CROSS:
        kind = "CROSS-PROJECT (the lesson must cite episodes of at least 2 independence groups)"
    else:
        kind = "PROJECT-LOCAL CANDIDATE (one independence group; the lesson stays project-local)"
    head = [
        "TASK: lesson synthesis from recurring mistake episodes"
        f" (experiment {packet['exp']}, packet {packet['packet_id']})",
        f"PACKET TYPE: {kind}",
        *([f"CURRENT AGENT MODEL ERA: {ctx['current_era']}"] if ctx.get("current_era") else []),
        f"EPISODES: {ctx['episodes']} · INDEPENDENCE GROUPS ({ctx['group_count']}):"
        f" {', '.join(ctx['groups'])}",
        f"SEEN: {ctx['first_seen']} to {ctx['last_seen']}",
        "",
        f"EPISODES ({ctx['episodes']}):",
    ]
    body = []
    for s in packet["sources"]:
        origin = "a session transcript" if s["origin"] == "transcript" else "the owner's prompts"
        sm = s["summary"]
        era = f" · status {s['status']} · model era {s['model_era']}" if "status" in s else ""
        lines = [
            f"[{s['handle']}] episode · project {s['project']} · independence group {s['group']}"
            f" · from {origin} · seen {s['valid_from']} to {s['last_seen']}{era}",
            "extractor summary (CONTEXT ONLY, never quote it):",
            f"- title: {s['title']}",
            f"- symptom: {sm['symptom']}",
            f"- fix: {sm['fix']}",
            f"- lesson: when {sm['when']} | do {sm['do']} | avoid {sm['avoid']}",
            "evidence quotes (quote ONLY from these lines; one quote = part of ONE line):",
            "<<<",
            *(
                f"- [{q['date']} · {q['speaker']} · {', '.join(q['slots'])}] {q['quote']}"
                for q in s["quotes"]
            ),
            ">>>",
        ]
        body.append("\n".join(lines))
    return "\n".join([*head, "", "\n\n".join(body), "", "Produce the JSON object now."])


def build_e4(
    cfg: dict[str, Any],
    *,
    exp: str = C.E4,
    embed: Callable[[Sequence[str]], np.ndarray] | None = None,
    episodes: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Embed, set the threshold (E4: tuned once on the held-out split; E4B: frozen in the config),
    cluster, exclude already-covered clusters (E4B), write ``clusters.json`` and the packets
    (``packets/<exp>``). ``embed`` is injectable for tests."""
    import al_packets as P

    sel = cfg["selection"][exp]
    eps = episodes if episodes is not None else load_episodes()
    vectors = (embed or e5_embed)([f"{embed_text(e)}" for e in eps])
    if sel.get("center"):
        vectors = center(vectors)
    d_all = cosine_distances(vectors)
    ids = [e["episode_id"] for e in eps]
    if "threshold" in sel:  # frozen (E4B): no tuning, no held-out split
        threshold, table, hold, hidx = float(sel["threshold"]), [], set(), []
    else:
        hold = holdout_split(ids, float(sel["holdout_frac"]), sel["seed"])
        hidx = [i for i, x in enumerate(ids) if x in hold]
        threshold, table = tune_threshold(d_all[np.ix_(hidx, hidx)], grid_values(sel["grid"]))
    clusters = agglomerate(d_all, threshold)
    covered = C.read_json(covered_path())["packets"] if sel.get("exclude_covered") else {}
    current_era = sel.get("current_era")
    rows, packets = [], []
    nx = nl = ncov = 0
    for ci, members in enumerate(clusters, start=1):
        groups = [eps[i]["group"] for i in members]
        kind = eligibility(groups, sel)
        row = {
            "cluster": ci,
            "episodes": [ids[i] for i in members],
            "slugs": sorted({eps[i]["slug"] for i in members}),
            "groups": sorted(set(groups)),
            "group_count": len(set(groups)),
            "eligibility": kind,
        }
        if kind and covered:
            pid = covered_by([{ids[i], *eps[i].get("merged_ids", [])} for i in members], covered)
            if pid:
                row.update(eligibility=COVERED, covered_by=pid, eligible_as=kind)
                ncov += 1
                kind = None
        if kind == CROSS:
            nx += 1
            row["packet_id"] = f"{exp}-X{nx:02d}"
        elif kind == LOCAL:
            nl += 1
            row["packet_id"] = f"{exp}-L{nl:02d}"
        if kind:
            packets.append(
                e4_packet(
                    row["packet_id"],
                    kind,
                    [eps[i] for i in members],
                    cluster=ci,
                    exp=exp,
                    current_era=current_era,
                )
            )
        rows.append(row)
    sizes = [len(c) for c in clusters]
    degenerate = max(sizes) > float(sel.get("max_cluster_share", 1.0)) * len(eps)
    summary = {
        "linkage": "average",
        "distance": "cosine (E5 query embeddings of lesson + symptom)",
        "threshold": threshold,
        "threshold_source": sel.get("threshold_source", "tuned on the held-out split"),
        "holdout_frac": sel.get("holdout_frac"),
        "seed": sel.get("seed"),
        "criterion": sel.get("criterion"),
        "episodes": len(eps),
        "holdout_episodes": len(hidx),
        "clusters": len(clusters),
        "singletons": sum(1 for s in sizes if s == 1),
        "largest_cluster": max(sizes),
        "centered": bool(sel.get("center")),
        "degenerate": degenerate,
        "revision": sel.get("revision"),
        "cross_project": nx,
        "project_local": nl,
        "already_covered": ncov,
        "eligibility": (
            f"cross-project >= {sel['min_cross_episodes']} episodes from >= {sel['min_cross_groups']} groups;"
            f" project-local: 1 group, >= {sel['min_local_episodes']} episodes"
        ),
        "episodes_input_sha256": C.sha256_file(episodes_path()) if episodes is None else None,
    }
    C.write_json(
        clusters_path(),
        {"summary": summary, "grid": table, "holdout": sorted(hold), "clusters": rows},
    )
    if degenerate:  # a guard, not a tuning knob: no packets from one catch-all cluster
        raise C.HarnessError(
            f"degenerate clustering: the largest cluster holds {max(sizes)} of {len(eps)} episodes"
            f" (> {sel.get('max_cluster_share')}); no packets written"
        )
    P._write_packets(exp, packets, {"clustering": summary})
    return packets


# --------------------------------------------------------------------------- the check
def lesson_units(packet_id: str, output: dict[str, Any]) -> list[dict[str, Any]]:
    if output.get("abstain") or not isinstance(output.get("lesson"), dict):
        return []
    return [
        {"uid": f"{packet_id}#lesson", "type": "lesson", "hiding": False, "content": dict(output["lesson"])}
    ]


def _quote_in(quote: str, lines: list[str]) -> str:
    from al_grounding import quote_status

    best = "absent"
    for line in lines:
        st = quote_status(quote, line)
        if st == "verbatim":
            return st
        if st == "too_short":
            best = st
    return best


def check_lesson(unit: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    from al_grounding import PacketIndex, invented_refs, norm_ws

    flags: set[str] = set()
    les = unit["content"]
    src = {s["handle"]: s for s in packet["sources"]}
    group_of = {h: s["group"] for h, s in src.items()}
    checked = []
    cited: set[str] = set()
    for section in ("when", "do", "avoid"):
        claims = [c for c in les.get(section) or [] if isinstance(c, dict)]
        if not claims:
            flags.add("no_evidence")
        for c in claims:
            ev_rows = []
            if not c.get("evidence"):
                flags.add("no_evidence")
            for ev in c.get("evidence") or []:
                h, q = str(ev.get("source", "")).strip(), str(ev.get("quote", ""))
                row: dict[str, Any] = {"source": h, "quote": q}
                if h not in src:
                    flags.add("unknown_source")
                    row["status"] = "unknown_source"
                else:
                    st = _quote_in(q, [x["quote"] for x in src[h]["quotes"]])
                    row["status"] = st
                    if st == "verbatim":
                        cited.add(h)
                    else:
                        flags.add("quote_too_short" if st == "too_short" else "quote_not_verbatim")
                ev_rows.append(row)
            checked.append({"section": section, "text": c.get("text", ""), "evidence": ev_rows})
    groups = sorted({group_of[h] for h in cited})
    if packet["context"]["packet_type"] == CROSS and len(groups) < 2:
        flags.add("under_evidenced")
    if les.get("group_count") != len(groups):
        flags.add("group_count_mismatch")
    if les.get("recurrence_count") != len(cited):
        flags.add("recurrence_mismatch")
    if cited:
        first = min(src[h]["valid_from"] for h in cited)
        last = max(src[h]["last_seen"] for h in cited)
        if les.get("first_seen") != first or les.get("last_seen") != last:
            flags.add("dates_mismatch")
    current_era = packet["context"].get("current_era")
    if current_era is not None:  # E4B: era recount + status evidence
        eras = sorted({str(src[h].get("model_era")) for h in cited})
        if sorted(set(map(str, les.get("model_era") or []))) != eras:
            flags.add("era_mismatch")
        if not status_supported(str(les.get("status")), [src[h] for h in sorted(cited)], current_era):
            flags.add("status_unsupported")
    idx = PacketIndex(packet)
    free = "\n".join(
        [
            str(les.get("title", "")),
            *(c["text"] for c in checked),
            *(str(x) for x in les.get("not_verified_for") or []),
        ]
    )
    refs, _handles = invented_refs(free, idx)
    if refs:
        flags.add("invented_ref")
    hay = norm_ws(idx.all_text + "\n" + packet["user"]).lower()
    versions = (les.get("scope") or {}).get("versions") or []
    if any(norm_ws(str(v.get("version", ""))).lower() not in hay for v in versions if isinstance(v, dict)):
        flags.add("invented_version")
    grounding = sorted(flags & E4_FLAGS)
    return {
        "uid": unit["uid"],
        "type": unit["type"],
        "hiding": False,
        "packet_type": packet["context"]["packet_type"],
        "grounded_det": not grounding,
        "grounding_flags": grounding,
        "structure_flags": [],
        "invented": {"refs": refs, "handles": []},
        "claims": checked,
        "recount": {
            "cited_episodes": len(cited),
            "groups": groups,
            "group_count": len(groups),
            **({"eras": sorted({str(src[h].get("model_era")) for h in cited})} if current_era else {}),
        },
    }


__all__ = [
    "COVERED",
    "CROSS",
    "E4_FLAGS",
    "covered_by",
    "covered_path",
    "status_supported",
    "LOCAL",
    "agglomerate",
    "build_e4",
    "center",
    "check_lesson",
    "clusters_path",
    "cosine_distances",
    "e4_packet",
    "eligibility",
    "embed_text",
    "episodes_path",
    "grid_values",
    "group_count",
    "holdout_split",
    "lesson_units",
    "load_episodes",
    "render_user",
    "silhouette",
    "tune_threshold",
]
