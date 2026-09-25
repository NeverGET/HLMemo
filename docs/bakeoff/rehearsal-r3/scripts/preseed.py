"""Pre-seed the G-L3 project like the R2 rehearsal (08a): 4 memory.write calls x 40 fact items (~1.5 kB
each), so the timed queries search a non-trivial project. Token from the isolated hlm config."""
import argparse, os, random, sys, uuid
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mcpc import Client

WORDS = ("deploy review migration cache latency budget session card lesson retry queue worker index backup "
         "restore token grant project device policy archive summary decision note sprint release config "
         "Karte Dienst Speicher Abfrage Sitzung Entscheidung proje karar oturum kayıt yedek sürüm ayar").split()

ap = argparse.ArgumentParser()
ap.add_argument("--device", required=True); ap.add_argument("--project", required=True)
ap.add_argument("--batches", type=int, default=4); ap.add_argument("--per", type=int, default=40)
a = ap.parse_args()
c = Client(a.device)
for b in range(a.batches):
    rng = random.Random(1000 + b)
    items = [{"kind": "fact", "title": f"pre-seed {b}.{i}",
              "body": "\n".join(" ".join(rng.choice(WORDS) for _ in range(12)) + "." for _ in range(15))}
             for i in range(a.per)]
    err, p, ms = c.write(a.project, items, client="r3-rehearsal/preseed")
    print(f"pre-seed batch {b}: err={err} versions={len((p or {}).get('versions') or [])} {ms:.0f}ms")
