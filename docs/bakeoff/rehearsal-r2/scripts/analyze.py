import re, sys, json
path, t0, t1 = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])  # t0/t1: load window (write start/end)
units = {"B": 1/2**20, "KiB": 1/1024, "MiB": 1, "GiB": 1024}
def mib(s):
    m = re.match(r"([\d.]+)([KMG]i?B|B)", s); return float(m.group(1)) * units[m.group(2)]
rows = []
for line in open(path):
    m = re.match(r"([\d.]+) load=([\d.]+) ([\d.]+) ([\d.]+) busy=(\d+)% iowait=(\d+)% \| (.*) \| backlog: (.*)", line.strip())
    if not m: continue
    t = float(m.group(1)); svc = {}
    for part in m.group(7).split():
        name, _, rest = part.partition("="); use, _, cpu = rest.partition("|"); u, _, lim = use.partition("/")
        svc[name] = (mib(u), mib(lim), float(cpu.rstrip("%") or 0))
    bl = {}
    for tok in m.group(8).split():
        if "=" in tok and ":" in tok:
            k, _, n = tok.rpartition("="); bl[k] = int(n)
    rows.append((t, float(m.group(2)), int(m.group(5)), int(m.group(6)), svc, bl))
rows = rows[1:]  # first CPU delta is meaningless
win = [r for r in rows if t0 <= r[0] <= t1 + 3]
def peak(rs, name): return max((r[4][name][0] for r in rs if name in r[4]), default=0)
out = {"samples_total": len(rows), "samples_in_write_window": len(win)}
out["write_window"] = {"cpu_busy_mean": round(sum(r[2] for r in win)/max(1,len(win))), "cpu_busy_max": max((r[2] for r in win), default=0), "load1_max": max((r[1] for r in win), default=0)}
out["whole_run"] = {"cpu_busy_max": max(r[2] for r in rows), "load1_max": max(r[1] for r in rows)}
names = ["db", "api", "worker", "librarian", "caddy"]
out["mem_peak_mib"] = {n: {"peak": round(peak(rows, n)), "limit": round(rows[0][4][n][1]) if n in rows[0][4] else None} for n in names}
out["cpu_pct_peak"] = {n: max((r[4][n][2] for r in rows if n in r[4]), default=0) for n in names}
def last_with(prefix):
    ts = [r[0] for r in rows if any(k.startswith(prefix) for k in r[5])]
    return max(ts) if ts else None
out["backlog_peak"] = {}
for r in rows:
    for k, v in r[5].items(): out["backlog_peak"][k] = max(out["backlog_peak"].get(k, 0), v)
for prefix in ("embed", "librarian_write"):
    lw = last_with(prefix)
    nxt = [r[0] for r in rows if lw and r[0] > lw]
    out[f"{prefix}_drained_s_after_last_write"] = round((nxt[0] if nxt else lw) - t1, 1) if lw else 0
print(json.dumps(out, indent=1))
