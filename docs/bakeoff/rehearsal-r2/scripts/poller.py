import ssl, sys, time, urllib.request
ctx = ssl.create_default_context(cafile=sys.argv[2])
out = open(sys.argv[1], "a", buffering=1)
while True:
    t = time.time()
    try:
        with urllib.request.urlopen("https://localhost:19443/ready", timeout=2, context=ctx) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:
        code = 0
    out.write(f"{t:.3f} {code}\n")
    time.sleep(0.25)
