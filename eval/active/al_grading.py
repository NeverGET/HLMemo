"""Blind grading kit and pre-registered scorer of the ceiling experiment (PLAN §3).

``build_kit`` pools the gradeable units of every arm run of an experiment (from the grounding
checks), gives each a random code, hides the arm, and writes one shuffled file per reader
(``grading/<exp>/reader-<R>/units.jsonl`` plus an empty ``labels.jsonl``), the per-packet context
files and the reader instructions. The code -> (arm, run, unit, deterministic check) key goes to
``grading-key/<exp>.key.json``, outside the directory the readers receive. E2 also gets the coverage
sheet (each card against its project's must-know facts). E0 takes its units from the E0 packet (the
observer's proposals; no arm).

``build_split`` writes the third reader's file: only the units on which readers A and B disagree.

``score`` applies the bars of the VERIFIED pre-registration (never the live config): on a split the
stricter label counts (correct/grounded/useful need both readers' ``true``; harmful_stale needs
one ``true``); grounded also needs the deterministic check; Gemini's runs are pooled (each run is
also reported). Reader C is reported as adjudication only.
"""

from __future__ import annotations

import random
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import al_common as C

LABELS = ("correct", "grounded", "useful", "harmful_stale")
E0_LABELS = ("correct", "harmful_stale")
#: E4 (lessons v2, D-222): ``harmful`` and ``overgeneralized`` count when EITHER reader says true
E4_LABELS = ("correct", "grounded", "useful", "harmful", "overgeneralized")
STRICT_TRUE = ("correct", "grounded", "useful")  # the stricter value of these is False


def labels_for(exp: str) -> tuple[str, ...]:
    return E0_LABELS if exp == "E0" else E4_LABELS if exp in C.LESSON_EXPS else LABELS


def grading_dir(exp: str) -> Path:
    return C.pdir("grading", exp)


def _code(rng: random.Random, used: set[str]) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    while True:
        code = "".join(rng.choice(alphabet) for _ in range(6))
        if code not in used:
            used.add(code)
            return code


def to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in ("true", "yes", "y", "1"):
        return True
    if s in ("false", "no", "n", "0"):
        return False
    return None


# --------------------------------------------------------------------------- context files
def context_text(packet: dict[str, Any]) -> str:
    parts = [packet["user"]]
    later = (packet.get("grader_context") or {}).get("later_notes") or []
    if later:
        parts.append("\n\nNEWER SESSION NOTES OF THE SAME PROJECT (the librarian did NOT see these):")
        for n in later:
            parts.append(f"\n[{n['handle']}] session_note · valid from {n['valid_from']}")
            parts.append("Decisions:\n" + ("\n".join(f"- {d}" for d in n["decisions"]) or "(none)"))
            parts.append("Uncertain:\n" + ("\n".join(f"- {u}" for u in n["uncertain"]) or "(none)"))
    return "\n".join(parts) + "\n"


# --------------------------------------------------------------------------- kit
def collect_units(exp: str, runs: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(label units, card units)`` of every run's grounding check (``checks/<exp>/<run>.json``)."""
    label_units: list[dict[str, Any]] = []
    cards: list[dict[str, Any]] = []
    for run in runs:
        f = C.private_dir() / "checks" / exp / f"{run}.json"
        if not f.is_file():
            raise C.HarnessError(f"no grounding check for {exp} {run}: run `al.py check --exp {exp}` first")
        chk = C.read_json(f)
        for pid, rec in sorted(chk["packets"].items()):
            for u in rec.get("units") or []:
                row = {"run": run, "arm": run.split("-r")[0], "packet": pid, **u}
                (cards if u["type"] == "card" else label_units).append(row)
    return label_units, cards


def _reader_rows(units: list[dict[str, Any]], code_of: dict[str, str], exp: str) -> list[dict[str, Any]]:
    return [
        {
            "code": code_of[u["key"]],
            "exp": exp,
            "type": u["type"],
            "hiding": u["hiding"],
            "packet": u["packet"],
            "content": u["content"],
        }
        for u in units
    ]


def _write_reader(
    exp: str, reader: str, rows: list[dict[str, Any]], seed: str, labels: Iterable[str]
) -> Path:
    d = grading_dir(exp) / f"reader-{reader}"
    rows = list(rows)
    random.Random(f"{seed}:{exp}:{reader}").shuffle(rows)  # a different order per reader
    C.write_text(d / "units.jsonl", "".join(C.canonical(r) + "\n" for r in rows))
    blank = {k: None for k in labels}
    C.write_text(
        d / "labels.jsonl",
        "".join(C.canonical({"code": r["code"], **blank, "note": ""}) + "\n" for r in rows),
    )
    return d


def build_kit(exp: str, record: dict[str, Any], packets: list[dict[str, Any]]) -> dict[str, Any]:
    """The blind kit of ``exp`` (module doc). ``record``: the verified pre-registration."""
    seed = str(record["grading"]["seed"])
    rng = random.Random(f"{seed}:{exp}:codes")
    used: set[str] = set()
    readers = list(record["grading"]["readers"])
    gdir = grading_dir(exp)
    C.write_text(gdir / "READER-INSTRUCTIONS.md", C.suite_of(exp).reader_template.read_text(encoding="utf-8"))
    key: dict[str, Any] = {"exp": exp, "prereg_sha256": record.get("_sha"), "units": {}, "coverage": {}}
    if exp == "E0":
        data = C.read_json(C.packets_dir("E0") / "units.json")
        rows = []
        for i, u in enumerate(data["units"]):
            code = _code(rng, used)
            key["units"][code] = {"arm": "observer", "run": "observer", "index": i, **u["hidden"]}
            rows.append(
                {"code": code, "exp": "E0", "type": "proposal", "hiding": u["hidden"]["hiding"], **u["view"]}
            )
        for r in readers:
            _write_reader(exp, r, rows, seed, LABELS)
        C.write_json(C.key_dir() / f"{exp}.key.json", key)
        return {"units": len(rows), "readers": readers}
    runs = run_labels(record)
    units, cards = collect_units(exp, runs)
    by_packet = {p["packet_id"]: p for p in packets}
    for pid in sorted({u["packet"] for u in units} | {c["packet"] for c in cards}):
        C.write_text(gdir / "context" / f"{pid}.txt", context_text(by_packet[pid]))
    code_of: dict[str, str] = {}
    for u in sorted(units, key=lambda x: (x["run"], x["uid"])):
        u["key"] = f"{u['run']}|{u['uid']}"
        code = _code(rng, used)
        code_of[u["key"]] = code
        chk = u["check"]
        key["units"][code] = {
            "arm": u["arm"],
            "run": u["run"],
            "packet": u["packet"],
            "uid": u["uid"],
            "type": u["type"],
            "hiding": u["hiding"],
            "grounded_det": chk["grounded_det"],
            "grounding_flags": chk["grounding_flags"],
        }
        if chk.get("packet_type"):
            key["units"][code]["packet_type"] = chk["packet_type"]
    if exp == "E2":  # a card line is judged with its whole card in view
        whole = {(c["run"], c["packet"]): c["content"] for c in cards}
        for u in units:
            if u["type"] == "card_line":
                u["content"] = {**u["content"], "whole_card": whole.get((u["run"], u["packet"]))}
    rows = _reader_rows(units, code_of, exp)
    for r in readers:
        _write_reader(exp, r, rows, seed, labels_for(exp))
    n_cov = 0
    if exp == "E2":
        n_cov = _coverage_kit(exp, cards, rng, used, key, readers, seed)
    C.write_json(C.key_dir() / f"{exp}.key.json", key)
    return {"units": len(rows), "coverage_cards": n_cov, "readers": readers, "runs": runs}


def _coverage_kit(
    exp: str,
    cards: list[dict[str, Any]],
    rng: random.Random,
    used: set[str],
    key: dict[str, Any],
    readers: list[str],
    seed: str,
) -> int:
    from al_grounding import render_card

    mk_dir = C.private_dir() / "mustknow"
    rows = []
    for c in sorted(cards, key=lambda x: (x["run"], x["packet"])):
        mk = mk_dir / f"{c['packet']}.json"
        if not mk.is_file():
            raise C.HarnessError(f"no must-know facts for {c['packet']} ({mk.name})")
        facts = C.read_json(mk)["facts"]
        code = _code(rng, used)
        key["coverage"][code] = {"arm": c["arm"], "run": c["run"], "packet": c["packet"], "facts": len(facts)}
        rows.append(
            {
                "code": code,
                "packet": c["packet"],
                "card": render_card(c["content"]),
                "must_know": [{"id": f["id"], "fact": f["fact"]} for f in facts],
            }
        )
    for r in readers:
        d = grading_dir(exp) / f"reader-{r}"
        shuffled = list(rows)
        random.Random(f"{seed}:{exp}:{r}:coverage").shuffle(shuffled)
        C.write_text(
            d / "coverage.jsonl",
            "".join(
                C.canonical({**row, "covered": {f["id"]: None for f in row["must_know"]}}) + "\n"
                for row in shuffled
            ),
        )
    return len(rows)


def run_labels(record: dict[str, Any]) -> list[str]:
    arms = record["arms"]
    return [f"gemini-r{k}" for k in range(1, int(arms["gemini"]["runs"]) + 1)] + [
        f"opus-r{k}" for k in range(1, int(arms["opus"]["runs"]) + 1)
    ]


# --------------------------------------------------------------------------- labels
def read_labels(exp: str, reader: str) -> dict[str, dict[str, Any]]:
    f = grading_dir(exp) / f"reader-{reader}" / "labels.jsonl"
    return {str(r["code"]): r for r in C.read_jsonl(f)}


def read_coverage(exp: str, reader: str) -> dict[str, dict[str, bool | None]]:
    f = grading_dir(exp) / f"reader-{reader}" / "coverage.jsonl"
    return {
        str(r["code"]): {k: to_bool(v) for k, v in (r.get("covered") or {}).items()} for r in C.read_jsonl(f)
    }


def stricter(values: list[bool | None], label: str) -> bool | None:
    """The stricter of the readers' values: ``False`` wins for correct/grounded/useful, ``True``
    wins for harmful_stale. ``None`` when any reader left it empty."""
    if not values or any(v is None for v in values):
        return None
    return all(values) if label in STRICT_TRUE else any(values)


def split_codes(
    a: dict[str, dict[str, Any]], b: dict[str, dict[str, Any]], labels: Iterable[str]
) -> list[str]:
    return sorted(
        code
        for code in a
        if code in b and any(to_bool(a[code].get(k)) != to_bool(b[code].get(k)) for k in labels)
    )


def build_split(exp: str, record: dict[str, Any]) -> dict[str, Any]:
    ra, rb = record["grading"]["readers"][:2]
    labels = labels_for(exp)
    a, b = read_labels(exp, ra), read_labels(exp, rb)
    codes = set(split_codes(a, b, labels))
    rows = [r for r in C.read_jsonl(grading_dir(exp) / f"reader-{ra}" / "units.jsonl") if r["code"] in codes]
    reader = record["grading"]["split_reader"]
    _write_reader(exp, reader, rows, str(record["grading"]["seed"]), labels)
    return {"split_units": len(rows), "reader": reader}


# --------------------------------------------------------------------------- scoring
def _rate(num: int, den: int) -> float | None:
    return None if den == 0 else round(num / den, 4)


def final_labels(
    key: dict[str, Any], readers: list[dict[str, dict[str, Any]]], labels: tuple[str, ...]
) -> tuple[dict[str, dict[str, bool]], list[str]]:
    """Per code the stricter label of the readers; ``missing`` lists codes with an empty label."""
    out: dict[str, dict[str, bool]] = {}
    missing: list[str] = []
    for code, meta in key["units"].items():
        row: dict[str, bool] = {}
        for lab in labels:
            v = stricter([to_bool(r.get(code, {}).get(lab)) for r in readers], lab)
            if v is None:
                missing.append(code)
                break
            row[lab] = v
        else:
            if "grounded" in row:
                row["grounded"] = row["grounded"] and bool(meta.get("grounded_det", True))
            out[code] = row
    return out, sorted(set(missing))


def metrics(codes: list[str], key: dict[str, Any], final: dict[str, dict[str, bool]]) -> dict[str, Any]:
    rows = [(key["units"][c], final[c]) for c in codes if c in final]
    hiding = [f for m, f in rows if m.get("hiding")]
    other = [f for m, f in rows if not m.get("hiding")]
    n = len(rows)
    return {
        "units": n,
        "hiding_units": len(hiding),
        "correct": _rate(sum(f["correct"] for _, f in rows), n),
        "grounded": _rate(sum(f.get("grounded", False) for _, f in rows), n),
        "grounded_det": _rate(sum(bool(m.get("grounded_det")) for m, _ in rows), n),
        "useful": _rate(sum(f.get("useful", False) for _, f in rows), n),
        "harmful_stale_hiding": sum(f["harmful_stale"] for f in hiding),
        "harmful_stale_other_rate": _rate(sum(f["harmful_stale"] for f in other), len(other)) or 0.0,
    }


def metrics_e4(codes: list[str], key: dict[str, Any], final: dict[str, dict[str, bool]]) -> dict[str, Any]:
    rows = [(key["units"][c], final[c]) for c in codes if c in final]
    n = len(rows)
    return {
        "units": n,
        "correct": _rate(sum(f["correct"] for _, f in rows), n),
        "grounded": _rate(sum(f.get("grounded", False) for _, f in rows), n),
        "grounded_det": _rate(sum(bool(m.get("grounded_det")) for m, _ in rows), n),
        "useful": _rate(sum(f.get("useful", False) for _, f in rows), n),
        "harmful": sum(f["harmful"] for _, f in rows),
        "overgeneralized_rate": _rate(sum(f["overgeneralized"] for _, f in rows), n) or 0.0,
    }


def passes_e4(m: dict[str, Any], bars: dict[str, Any], exp: str = C.E4) -> tuple[bool, list[str]]:
    fails = []
    if not m["units"]:
        return False, ["no units"]
    if m["harmful"] > bars["harmful_max"]:
        fails.append(f"harmful {m['harmful']}")
    if m["overgeneralized_rate"] > bars["overgeneralized_max_rate"]:
        fails.append(f"overgeneralized {m['overgeneralized_rate']}")
    if m["grounded"] < bars["grounded_min"]:
        fails.append(f"grounded {m['grounded']}")
    if m["correct"] < bars["correct_min"]:
        fails.append(f"correct {m['correct']}")
    if m["useful"] < bars["useful_min"][exp]:
        fails.append(f"useful {m['useful']}")
    return not fails, fails


def coverage(
    key: dict[str, Any], readers: list[dict[str, dict[str, bool | None]]], arm_runs: set[str]
) -> dict:
    per_card = []
    missing = 0
    for code, meta in key["coverage"].items():
        if meta["run"] not in arm_runs:
            continue
        ids = set().union(*(set(r.get(code, {})) for r in readers)) if readers else set()
        covered = 0
        for fid in ids:
            v = stricter([r.get(code, {}).get(fid) for r in readers], "correct")
            if v is None:
                missing += 1
            covered += int(bool(v))
        per_card.append(covered / max(1, meta["facts"]))
    return {"cards": len(per_card), "coverage": _rate_f(per_card), "missing_labels": missing}


def _rate_f(values: list[float]) -> float | None:
    return None if not values else round(sum(values) / len(values), 4)


def passes(
    m: dict[str, Any], bars: dict[str, Any], exp: str, cov: float | None = None
) -> tuple[bool, list[str]]:
    if exp in C.LESSON_EXPS:
        return passes_e4(m, bars, exp)
    fails = []
    if not m["units"]:
        fails.append("no units")
    else:
        if m["harmful_stale_hiding"] > bars["harmful_stale_hiding_max"]:
            fails.append(f"harmful-stale on hiding units {m['harmful_stale_hiding']}")
        if m["harmful_stale_other_rate"] > bars["harmful_stale_other_max_rate"]:
            fails.append(f"harmful-stale rate {m['harmful_stale_other_rate']}")
        if m["grounded"] < bars["grounded_min"]:
            fails.append(f"grounded {m['grounded']}")
        if m["correct"] < bars["correct_min"]:
            fails.append(f"correct {m['correct']}")
        if m["useful"] < bars["useful_min"][exp]:
            fails.append(f"useful {m['useful']}")
    if exp in bars["coverage_min"] and (cov is None or cov < bars["coverage_min"][exp]):
        fails.append(f"coverage {cov}")
    return not fails, fails


def verdict(exp: str, gem: dict[str, Any], opus: dict[str, Any], bars: dict[str, Any]) -> dict[str, Any]:
    g_ok, g_fails = passes(gem, bars, exp, gem.get("coverage"))
    o_ok, o_fails = passes(opus, bars, exp, opus.get("coverage"))
    margins = {}
    for metric in bars["opus_margin_metrics"]:
        if gem.get(metric) is not None and opus.get(metric) is not None:
            margins[metric] = round(opus[metric] - gem[metric], 4)
    margin_fails = [f"Opus +{v} on {k}" for k, v in margins.items() if v > bars["opus_margin_max"]]
    if not opus["units"] and g_ok:
        decision = "INCOMPLETE (no Opus units: the margin bar cannot be checked)"
    elif g_ok and not margin_fails:
        decision = "GO-Gemini"
    elif o_ok:
        decision = "GO-Opus-only (owner call)"
    else:
        decision = "NO-GO"
    return {
        "decision": decision,
        "gemini_fails": g_fails + margin_fails,
        "opus_fails": o_fails,
        "opus_minus_gemini": margins,
    }


def score(exp: str, record: dict[str, Any], *, allow_incomplete: bool = False) -> dict[str, Any]:
    key = C.read_json(C.key_dir() / f"{exp}.key.json")
    if key.get("prereg_sha256") not in (None, record.get("_sha")):
        raise C.HarnessError("the grading key was built under another pre-registration")
    g = record["grading"]
    readers = [read_labels(exp, r) for r in g["readers"]]
    split = read_labels(exp, g["split_reader"])
    labels = labels_for(exp)
    final, missing = final_labels(key, readers, labels)
    if missing and not allow_incomplete:
        raise C.HarnessError(
            f"{len(missing)} units lack a label from readers {g['readers']} (e.g. {missing[:5]})"
        )
    bars = record["bars"]
    out: dict[str, Any] = {"exp": exp, "prereg_sha256": record.get("_sha"), "missing_labels": len(missing)}
    out["amendments"] = record.get("_amendments", [])
    if exp == "E0":
        out.update(_score_e0(key, final, bars))
    else:
        groups: dict[str, list[str]] = {}
        for code, m in key["units"].items():
            groups.setdefault(m["run"], []).append(code)
            groups.setdefault(m["arm"], []).append(code)
            if m.get("packet_type"):
                groups.setdefault(f"{m['arm']}|{m['packet_type']}", []).append(code)
        measure = metrics_e4 if exp in C.LESSON_EXPS else metrics
        out["by_group"] = {name: measure(codes, key, final) for name, codes in sorted(groups.items())}
        if exp == "E2":
            cov_readers = [read_coverage(exp, r) for r in g["readers"]]
            runs = {m["run"] for m in key["coverage"].values()}
            for name in list(out["by_group"]):
                arm_runs = {r for r in runs if r == name or r.split("-r")[0] == name}
                cov = coverage(key, cov_readers, arm_runs)
                out["by_group"][name]["coverage"] = cov["coverage"]
                out["by_group"][name]["coverage_cards"] = cov["cards"]
        empty = measure([], key, final)
        out["verdict"] = verdict(
            exp, out["by_group"].get("gemini", empty), out["by_group"].get("opus", empty), bars
        )
    out["split"] = _split_report(key, readers, split, labels)
    return out


def _score_e0(key: dict[str, Any], final: dict[str, dict[str, bool]], bars: dict[str, Any]) -> dict[str, Any]:
    strata: dict[str, list[str]] = {}
    for code, m in key["units"].items():
        strata.setdefault(m["stratum"], []).append(code)
    res = {}
    for name, codes in sorted(strata.items()):
        rows = [final[c] for c in codes if c in final]
        res[name] = {
            "units": len(rows),
            "precision": _rate(sum(r["correct"] for r in rows), len(rows)),
            "false_invalidations": sum(r["harmful_stale"] for r in rows),
            "hiding_units": sum(1 for c in codes if key["units"][c].get("hiding")),
        }
    e0 = bars["e0"]
    cur = res.get("curated", {"units": 0, "precision": None, "false_invalidations": 0})
    if not cur["units"]:
        decision = "NO CURATED UNITS"
    elif (
        cur["precision"] >= e0["precision_go"] and cur["false_invalidations"] <= e0["false_invalidations_max"]
    ):
        decision = "AL2 promotion becomes a measurement exercise"
    elif cur["precision"] < e0["precision_floor"]:
        decision = "AL2 stays observer"
    else:
        decision = "between the bars (AL2 stays observer until more labels)"
    return {"by_stratum": res, "verdict": {"decision": decision}}


def _split_report(
    key: dict[str, Any],
    readers: list[dict[str, dict[str, Any]]],
    split: dict[str, dict[str, Any]],
    labels: tuple,
) -> dict[str, Any]:
    if len(readers) < 2:
        return {}
    codes = split_codes(readers[0], readers[1], labels)
    agree = 0
    judged = 0
    for code in codes:
        if code not in split:
            continue
        for lab in labels:
            c = to_bool(split[code].get(lab))
            s = stricter([to_bool(r.get(code, {}).get(lab)) for r in readers], lab)
            if c is None or s is None:
                continue
            judged += 1
            agree += int(c == s)
    return {
        "split_units": len(codes),
        "reader_c_labels": judged,
        "reader_c_agrees_with_stricter": _rate(agree, judged),
    }


def render_score(res: dict[str, Any]) -> str:
    lines = [f"# {res['exp']} score", ""]
    if res["exp"] in C.LESSON_EXPS:
        lines.append(
            "| group | units | correct | grounded | grounded_det | useful | harmful | overgeneralized |"
        )
        lines.append("|---|---|---|---|---|---|---|---|")
        for name, m in res["by_group"].items():
            lines.append(
                f"| {name} | {m['units']} | {m['correct']} | {m['grounded']} | {m['grounded_det']} |"
                f" {m['useful']} | {m['harmful']} | {m['overgeneralized_rate']} |"
            )
    elif res["exp"] == "E0":
        for name, m in res["by_stratum"].items():
            lines.append(
                f"- {name}: units {m['units']}, precision {m['precision']}, false invalidations"
                f" {m['false_invalidations']} (hiding units {m['hiding_units']})"
            )
    else:
        lines.append(
            "| group | units | hiding | correct | grounded | grounded_det | useful | hs_hiding | hs_other"
            " | coverage |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for name, m in res["by_group"].items():
            lines.append(
                f"| {name} | {m['units']} | {m['hiding_units']} | {m['correct']} | {m['grounded']} |"
                f" {m['grounded_det']} | {m['useful']} | {m['harmful_stale_hiding']} |"
                f" {m['harmful_stale_other_rate']} | {m.get('coverage', '')} |"
            )
    if res.get("amendments"):
        lines += ["", "Pre-registration amendments applied:"]
        lines += [
            f"- AMENDMENT-{a['n']} ({a['amended_at']}): {a['field']} {a['old']} -> {a['new']}: {a['reason']}"
            for a in res["amendments"]
        ]
    v = res["verdict"]
    lines += ["", f"**Verdict: {v['decision']}**"]
    for k in ("gemini_fails", "opus_fails", "opus_minus_gemini"):
        if v.get(k):
            lines.append(f"- {k}: {v[k]}")
    if res.get("split"):
        lines.append(f"- split: {res['split']}")
    return "\n".join(lines) + "\n"


__all__ = [
    "E4_LABELS",
    "LABELS",
    "labels_for",
    "metrics_e4",
    "passes_e4",
    "build_kit",
    "build_split",
    "context_text",
    "coverage",
    "final_labels",
    "metrics",
    "passes",
    "render_score",
    "score",
    "split_codes",
    "stricter",
    "to_bool",
    "verdict",
]
