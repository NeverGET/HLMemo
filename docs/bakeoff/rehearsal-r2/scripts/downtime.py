import sys
rows = [l.split() for l in open(sys.argv[1])]
rows = [(float(t), int(c)) for t, c in rows]
codes = {}
for _, c in rows: codes[c] = codes.get(c, 0) + 1
# longest non-200 window bounded by 200s
best = None; i = 0
while i < len(rows):
    if rows[i][1] != 200:
        j = i
        while j < len(rows) and rows[j][1] != 200: j += 1
        last200 = rows[i-1][0] if i > 0 else None
        next200 = rows[j][0] if j < len(rows) else None
        if last200 and next200:
            w = (next200 - rows[i][0], next200 - last200, rows[i][0], next200, j - i)
            if best is None or w[1] > best[1]: best = w
        i = j
    else: i += 1
print(f"samples={len(rows)} codes={codes}")
if best:
    print(f"longest outage: first-non200={best[2]:.2f} next200={best[3]:.2f} -> {best[0]:.1f}s (upper bound {best[1]:.1f}s, {best[4]} non-200 samples)")
