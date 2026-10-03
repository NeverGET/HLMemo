"""A FAKE ``hlm curate`` worker: reads the prompt on STDIN and writes canned, valid outputs.

    python fake_agent.py --answers answers.json

Stage, slice and output paths come from the ``HLM_CURATE_*`` environment. ``answers["fail"]``
maps ``"<stage>:<wid>"`` to the number of attempts that write an INVALID output (1 = the retry
succeeds, 2 = the slice fails). Never calls a model.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def key(a: int, b: int) -> str:
    return f"{min(a, b)}-{max(a, b)}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--answers", required=True)
    args = ap.parse_args()
    prompt = sys.stdin.read()
    stage = os.environ["HLM_CURATE_STAGE"]
    slice_path = Path(os.environ["HLM_CURATE_SLICE"])
    out = Path(os.environ["HLM_CURATE_OUT"])
    attempt = int(os.environ["HLM_CURATE_ATTEMPT"])
    wid = Path(os.environ["HLM_CURATE_WORKDIR"]).name
    # the brief must arrive on STDIN and name this job's files
    if "HISTORY POLICY" not in prompt or str(slice_path) not in prompt or str(out) not in prompt:
        print("prompt missing the brief or the paths", file=sys.stderr)
        return 3
    ans = json.loads(Path(args.answers).read_text(encoding="utf-8"))
    if attempt <= int(ans.get("fail", {}).get(f"{stage}:{wid}", 0)):
        out.write_text('{"not": "the contract"}', encoding="utf-8")
        print(f"fake: invalid output on attempt {attempt}")
        return 0
    sl = json.loads(slice_path.read_text(encoding="utf-8"))
    if stage == "map":
        files = set(sl["files"])
        export = Path(os.environ["HLM_CURATE_EXPORT"])
        file_of = {}
        for f in export.rglob("*.md"):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.startswith("logical_id: "):
                    file_of[int(line.split(": ", 1)[1])] = f.relative_to(export).as_posix()
                    break
        pairs = [p for p in ans["map"]["pairs"] if file_of.get(p["older_logical_id"]) in files]
        res = {"pairs": pairs, "file_fixes": [], "notes": "fake"}
    elif stage == "pass1":
        verdicts = []
        for c in sl["candidates"]:
            a, b = (s["logical_id"] for s in c["subjects"])
            v = dict(ans["pass1"].get(key(a, b)) or ans["pass1_default"])
            v.setdefault("newer_logical_id", None)
            v.setdefault("older_logical_id", None)
            v.setdefault("older_span", None)
            v.setdefault("newer_quote", None)
            verdicts.append({"cid": c["cid"], **v})
        res = {"verdicts": verdicts, "notes": "fake"}
    elif stage == "pass2":
        verdicts = []
        by_reviewer = ans.get("pass2_by_reviewer", {}).get(wid, {})
        for r in sl["records"]:
            k = f"{r['src_logical_id']}-{r['dst_logical_id']}"
            v = dict(by_reviewer.get(k) or ans["pass2"].get(k) or ans["pass2_default"])
            v.setdefault("fix", None)
            verdicts.append({"i": r["i"], **v})
        res = {"verdicts": verdicts, "missed": ""}
    else:
        print(f"unknown stage {stage}", file=sys.stderr)
        return 2
    out.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    print(f"fake {stage}/{wid}: wrote {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
