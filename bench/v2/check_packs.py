#!/usr/bin/env python3
"""Lint the bench v2 task packs, enforce the sealed hold-out exclusion, scan for private data.

    python bench/v2/check_packs.py                 # public + private packs, sealed check if the file exists
    python bench/v2/check_packs.py --pack src/hlmemo/bench/tasks/v2/t5_supersession.json

Sealed exclusion: loads answer_keys and gold_facts of the sealed corpus-B hold-out and rejects every
case whose text contains one of them (case-insensitive, also after punctuation/whitespace folding).
It prints ONLY counts and our own case ids, never sealed content.
Exit code 1 when any lint error, sealed rejection or privacy hit is found.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
PUBLIC_DIR = REPO / "src" / "hlmemo" / "bench" / "tasks" / "v2"  # W2f: packs ship in the package
PRIVATE_DIR = REPO / "docs" / "private" / "bench-v2"
SEALED = REPO / "docs" / "private" / "realdata-hlmemo" / "corpus-b-sealed.jsonl"

TIERS = {"easy", "medium", "hard"}
T5_LABELS = {"current", "superseded", "unrelated"}
T6_REL = {"contradicts", "supersedes", "refines", "compatible", "unrelated"}
TURKISH_ONLY = set("çğıöşüÇĞİÖŞÜ")


def default_packs() -> list[Path]:
    out = sorted(PUBLIC_DIR.glob("*.json"))
    if PRIVATE_DIR.exists():
        out += sorted(PRIVATE_DIR.glob("*.json"))
    return out


def strings(obj) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in strings(v)] + [k for k in obj if isinstance(k, str)]
    if isinstance(obj, list):
        return [s for v in obj for s in strings(v)]
    return []


def fold(s: str) -> str:
    return " ".join(re.sub(r"[^0-9a-zçğıöşüäß]+", " ", s.lower()).split())


def alts(key: str) -> list[str]:
    return [a.strip() for a in key.split("|") if a.strip()]


def any_in(key: str, text: str) -> bool:
    t = text.lower()
    for a in alts(key):
        a = a.lower()
        if re.search(r"\d", a):
            if re.search(r"(?<![0-9a-z])" + re.escape(a) + r"(?![0-9])", t):
                return True
        elif a in t:
            return True
    return False


# ------------------------------------------------------------------ lint ---

def lint_case(family: str, case: dict, pack: dict) -> list[str]:
    e: list[str] = []
    cid = case.get("id", "?")
    for k in ("id", "tier", "source", "rationale", "gold"):
        if k not in case:
            e.append(f"{cid}: missing {k}")
    if case.get("tier") not in TIERS:
        e.append(f"{cid}: bad tier {case.get('tier')!r}")
    g = case.get("gold", {})
    fam = family if family != "T11" else {"supersession": "T5", "relation": "T6", "risk_check_v2": "T9",
                                          "consolidation": "T8", "answer_or_abstain": "T10"}.get(case.get("job"), "?")
    if family == "T11":
        inj = g.get("injection") or {}
        if not inj.get("canary") or not inj.get("checks"):
            e.append(f"{cid}: T11 needs gold.injection.canary + checks")
        if fam == "?":
            e.append(f"{cid}: T11 unknown job {case.get('job')!r}")
    if fam == "T5":
        ids = [s["id"] for s in case.get("snippets", [])]
        if not 3 <= len(ids) <= 6 and family == "T5":
            e.append(f"{cid}: T5 wants 3-6 snippets, has {len(ids)}")
        labels = g.get("labels", {})
        if set(labels) != set(ids) or len(ids) != len(set(ids)):
            e.append(f"{cid}: labels/snippet ids mismatch")
        if any(v not in T5_LABELS for v in labels.values()):
            e.append(f"{cid}: bad T5 label")
        if "current" not in labels.values():
            e.append(f"{cid}: no current snippet")
    elif fam == "T6":
        ids = {i["id"] for i in case.get("items", [])}
        pairs = case.get("pairs", [])
        js = g.get("judgments", [])
        if len(js) != len(pairs):
            e.append(f"{cid}: judgments != pairs")
        for p, j in zip(pairs, js):
            if set(p) - ids or sorted(j.get("pair", [])) != sorted(p):
                e.append(f"{cid}: pair mismatch {p}")
            if j.get("relation") not in T6_REL:
                e.append(f"{cid}: bad relation {j.get('relation')}")
            sid = j.get("superseding_id")
            if (j.get("relation") == "supersedes") != (sid != "none") or (sid != "none" and sid not in p):
                e.append(f"{cid}: superseding_id inconsistent {j}")
    elif fam == "T7":
        passage = case.get("passage", "")
        if not passage:
            e.append(f"{cid}: T7 needs hidden passage")
        if not any(ch in TURKISH_ONLY for ch in case.get("question", "")) and "?" not in case.get("question", ""):
            e.append(f"{cid}: question does not look Turkish")
        for idv in g.get("identifier", []):
            if idv.lower() not in passage.lower():
                e.append(f"{cid}: identifier {idv!r} not in passage")
        if not any(i.lower() in [v.lower() for v in case.get("vocabulary", [])] for i in g.get("identifier", [])):
            e.append(f"{cid}: identifier not in vocabulary")
        for kt in g.get("key_terms", []):
            if not any_in(kt, passage):
                e.append(f"{cid}: key term {kt!r} not in passage")
        for d in g.get("distractors", []):
            if d not in case.get("vocabulary", []):
                e.append(f"{cid}: distractor {d!r} not in vocabulary")
            if any(d.lower() in a.lower() for kt in g.get("key_terms", []) for a in alts(kt)) or \
                    any(d.lower() in i.lower() for i in g.get("identifier", [])):
                e.append(f"{cid}: distractor {d!r} overlaps a gold term")
    elif fam == "T8":
        snips = case.get("snippets", [])
        if family == "T8" and not 12 <= len(snips) <= 25:
            e.append(f"{cid}: T8 wants 12-25 snippets, has {len(snips)}")
        text = " ".join(s["text"] for s in snips) + " " + case.get("topic", "")
        for f in g.get("facts", []):
            for k in f.get("keys", []):
                if not any_in(k, text):
                    e.append(f"{cid}: fact {f.get('id')} key {k!r} not in input")
    elif fam == "T9":
        lib = case.get("lessons") or pack.get("library") or []
        known = {x["id"] for x in lib}
        if unknown := set(g.get("lesson_ids", [])) - known:
            e.append(f"{cid}: unknown lesson ids {unknown}")
        if bool(g.get("lesson_ids")) != bool(g.get("warn")):
            e.append(f"{cid}: warn/lesson_ids inconsistent")
        if len(g.get("lesson_ids", [])) > 3:
            e.append(f"{cid}: >3 gold lessons")
    elif fam == "T10":
        ids = {s["id"] for s in case.get("snippets", [])}
        if g.get("answerable"):
            if not g.get("answer_keys"):
                e.append(f"{cid}: answerable without answer_keys")
            text = " ".join(s["text"] for s in case.get("snippets", []))
            if not any(any_in(k, text) for k in g.get("answer_keys", [])):
                e.append(f"{cid}: no answer key in snippets")
        if set(g.get("evidence_ids", [])) - ids:
            e.append(f"{cid}: evidence ids unknown")
    elif family == "T12":
        if not case.get("document") or not g.get("findings"):
            e.append(f"{cid}: T12 needs document + findings")
    return e


# ---------------------------------------------------------------- sealed ---

def load_sealed(path: Path) -> list[str]:
    keys: list[str] = []
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            keys += [k for k in (row.get("answer_keys") or []) if isinstance(k, str)]
            keys += [k for k in (row.get("gold_facts") or []) if isinstance(k, str)]
    return [k for k in keys if k.strip()]


def sealed_hits(text: str, keys: list[str], folded_keys: list[str]) -> int:
    low, fl = text.lower(), fold(text)
    return sum(1 for k, fk in zip(keys, folded_keys) if k.lower() in low or (fk and fk in fl))


# --------------------------------------------------------------- privacy ---

PRIVACY = [
    ("ipv4", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")),
    ("ipv6", re.compile(r"\b[0-9a-f]{1,4}(?::[0-9a-f]{1,4}){3,7}\b|\b[0-9a-f]{1,4}(?::[0-9a-f]{0,4}){2,6}::[0-9a-f]{0,4}\b", re.I)),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)*\.(?!service\b|timer\b|socket\b)[A-Za-z]{2,}\b")),
    ("api-key", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|sk-or-v1-[0-9a-f]{16,}|hlm_[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("youtube-channel-id", re.compile(r"\bUC[A-Za-z0-9_-]{22}\b")),
    ("long-number-id", re.compile(r"\b\d{10,}\b")),
    ("password-assign", re.compile(r"(?i)\b(?:password|passwd|secret|token)\s*[=:]\s*['\"]?[A-Za-z0-9/+_-]{8,}")),
]
# Explicit allowlist of benign strings that trip the patterns (dates, versions, example addresses).
PRIVACY_ALLOW = re.compile(r"^(?:127\.0\.0\.1|0\.0\.0\.0|10\.0\.0\.0|192\.0\.2\.\d+|198\.51\.100\.\d+|203\.0\.113\.\d+)$")


def privacy_hits(text: str) -> list[str]:
    out = []
    for name, rx in PRIVACY:
        for m in rx.finditer(text):
            if name == "ipv4" and PRIVACY_ALLOW.match(m.group(0)):
                continue
            if name == "ipv6" and not re.search(r"[a-f]", m.group(0), re.I) and m.group(0).count(":") < 3:
                continue  # clock times like 13:00:05
            out.append(name)
    return out


# ------------------------------------------------------------------ main ---

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", action="append", default=[], help="pack file(s); default: all public + private packs")
    ap.add_argument("--sealed", default=str(SEALED), help="sealed hold-out jsonl ('' to skip)")
    args = ap.parse_args()
    packs = [Path(p).resolve() for p in args.pack] or default_packs()
    sealed_keys: list[str] = []
    if args.sealed and Path(args.sealed).exists():
        sealed_keys = load_sealed(Path(args.sealed))
    folded = [fold(k) if len(fold(k)) >= 8 else "" for k in sealed_keys]

    total = lint_n = sealed_n = priv_n = 0
    ids_seen: set[str] = set()
    for p in packs:
        pack = json.loads(p.read_text())
        family = pack.get("family", "?")
        cases = pack.get("cases", [])
        lint_errs, rejected, priv = [], [], []
        units = [(c.get("id", "?"), c) for c in cases]
        units += [(f"library:{x['id']}", x) for x in pack.get("library", [])]
        for cid, c in units:
            text = "\n".join(strings(c))
            if sealed_keys and sealed_hits(text, sealed_keys, folded):
                rejected.append(cid)
            if hits := privacy_hits(text):
                priv.append(f"{cid}({','.join(sorted(set(hits)))})")
        for c in cases:
            if c.get("id") in ids_seen:
                lint_errs.append(f"{c.get('id')}: duplicate id across packs")
            ids_seen.add(c.get("id"))
            lint_errs += lint_case(family, c, pack)
        tiers = {t: sum(1 for c in cases if c.get("tier") == t) for t in ("easy", "medium", "hard")}
        extra = ""
        if family == "T9":
            silent = sum(1 for c in cases if not c["gold"]["warn"])
            extra = f" silent={silent}/{len(cases)} library={len(pack.get('library', []))}"
        if family == "T10":
            neg = sum(1 for c in cases if not c["gold"]["answerable"])
            extra = f" unanswerable={neg}/{len(cases)}"
        print(f"{p.relative_to(REPO)}: {family} {pack.get('pack')} cases={len(cases)} tiers={tiers}{extra} "
              f"lint={len(lint_errs)} sealed_rejected={len(rejected)} privacy={len(priv)}")
        for x in lint_errs:
            print(f"   lint: {x}")
        if rejected:
            print(f"   sealed-rejected case ids: {', '.join(rejected)}")
        for x in priv:
            print(f"   privacy: {x}")
        total += len(cases)
        lint_n += len(lint_errs)
        sealed_n += len(rejected)
        priv_n += len(priv)
    print(f"TOTAL cases={total} lint_errors={lint_n} sealed_rejected={sealed_n} privacy_hits={priv_n} "
          f"(sealed keys loaded: {len(sealed_keys)})")
    return 1 if (lint_n or sealed_n or priv_n) else 0


if __name__ == "__main__":
    sys.exit(main())
