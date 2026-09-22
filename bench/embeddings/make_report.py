"""Render results.json -> markdown table rows + paired non-identifier comparison vs e5-small."""
import json, statistics
from math import comb
from pathlib import Path

r = json.loads((Path(__file__).parent / "results.json").read_text())
base = {o["qid"]: o for o in r["local/multilingual-e5-small"]["per"]}


def hit(o):
    return bool(o["rank"] and o["rank"] <= 5)


def mcnemar_p(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2**n)


print("| model | dims | R@5 | R@10 | MRR@10 | TR/DE/EN R@5 | ident R@5 (n=25) | non-ident R@5 (n=75) | non-ident vs e5 (+win/-loss, McNemar p) | corpus cost $ | corpus embed s | query lat ms med/p90 |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|")
for k, v in r.items():
    if "error" in v:
        print(f"| {k} | ERROR {v['error'][:80]} |")
        continue
    a = v["all"]
    lat = sorted(v["lat_ms"])
    med = statistics.median(lat)
    p90 = lat[int(round(0.9 * (len(lat) - 1)))]
    win = sum(1 for o in v["per"] if not o["heavy"] and hit(o) and not hit(base[o["qid"]]))
    loss = sum(1 for o in v["per"] if not o["heavy"] and not hit(o) and hit(base[o["qid"]]))
    wall = f"{v['corpus_wall_s']:.0f}" if v.get("corpus_wall_s") else "n/a (stored)"
    print(
        f"| {k} | {v['dims']} | {a['r5']:.2f} | {a['r10']:.2f} | {a['mrr10']:.3f} | "
        f"{v['tr']['r5']:.2f}/{v['de']['r5']:.2f}/{v['en']['r5']:.2f} | {v['ident']['r5']:.2f} | "
        f"{v['nonident']['r5']:.2f} | +{win}/-{loss}, p={mcnemar_p(win, loss):.3f} | "
        f"{v['corpus_cost']:.3f} | {wall} | {med:.0f}/{p90:.0f} |"
    )
