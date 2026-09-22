# Opus 5.5 closing review of the final src + deploy rounds (2026-09-23)

## Item status

| Item | Status | file:line | Attack result |
|---|---|---|---|
| D1 declared-length pre-reservation | PARTIAL | middleware.py:286, 302; config.py:165 | Budget is now charged only for bytes received, so the declared-length attack is gone. A new bypass opened: bodies under the 256 KiB grace are never rate-checked, so they are bounded only by the 30 s inactivity timer and the 9000 s total. **Reproduced** (harness in scratchpad/exp/grace.py, using the real middleware with a fake clock): I sent 255 KiB, then 1 byte every 29 s. The reservation of 261,308 B was still held at t=8961 s, before any auth. |
| D2 min-rate too strict | FIXED | middleware.py:302 | The 8 KiB/s floor is checked only when a chunk arrives, and the idle deadline is independent. Legitimate pauses under 30 s survive. |
| RSS / spooling | PARTIAL | middleware.py:211, 292; compose.prod.yaml:37 | Anonymous RSS is bounded. But TMPDIR is unset and the container is read_only, so spools roll into the **64 MiB `/tmp` tmpfs**, which is RAM charged to the api cgroup. Disk (really tmpfs) worst case is 64 MiB total, not 256 MiB. TemporaryFile is unlinked (O_TMPFILE) and tmpfs is wiped on restart, so there is no leak after a crash or cancel. |
| D3 subnet + trusted proxy | FIXED | compose.prod.yaml:161; api.env.example:7; Caddyfile | Caddy has no `trusted_proxies`, so it overwrites XFF with the real peer (checked from Caddy's documented default, not tested live). The api peels right-to-left and only trusts peers in 172.30.39.0/24, and the frontend network is internal with only caddy and api on it. So a client cannot spoof its IP. Residual (Low): the bridge gateway 172.30.39.1 is the host, so local host processes could spoof. The IPv6 userland-proxy shared bucket is documented in RUNBOOK:186. |
| Caddy body limits | FIXED | Caddyfile | 64 MiB on /mcp and 64 KiB on REST. |
| D4 runner protocol + label | FIXED | deploy.sh:106; remote-deploy.sh:186-200 | Refs without `HLM_RUNNER_PROTOCOL=3` are refused before checkout. The label is checked after build, after the DB comes up and after migrate, and a missing or wrong label fails through ERR. I found no path where a mislabeled or unlabeled image starts. |
| bootstrap.sh | OK (Low notes) | bootstrap.sh:190-195 | It fails closed on SSH ports other than 22, on missing SSH_CONNECTION with --admin-cidr, and on sshd -T validation. The 22/tcp rule is added before `ufw enable`. Finalize requires a proven publickey session as the deploy user, and the `00-` drop-in beats cloud-init's `50-`. It is idempotent on re-run. |
| 8 GB sizing | OK | compose / RUNBOOK:73-88 | Limits sum to 5.25 GiB (6 GiB during migrate), leaving about 1.45–2 GiB for the host. That is tight but acceptable. Note: there is no swap, and the BuildKit build is not memory-limited and runs while the stack is live. |

**Worst case per attacker IP (defaults):**
- **Global budget, under-grace path:** about 514 connections × 255 KiB fill the 128 MiB per-IP budget. Two IPs, or one IPv6 /64, fill the 256 MiB global budget with about 1,028 sockets. Each socket needs only about 35 B/s total to stay open, and the data is held for about 2.5 h. Result: every POST with a body gets 503, i.e. a pre-auth DoS of all MCP calls. The cost is about 256 MiB uploaded every 2.5 h (around 230 kbit/s). With the old 300 s total the same attack needed about 7 Mbit/s sustained.
- **tmpfs, rate-lead path:** one IP uploads about 63 MiB fast, then trickles. I reproduced this: 66 MB was held until t=8033 s. The tmpfs is full, so every body over 1 MiB gets 503 "storage unavailable".
- **Sockets:** unbounded. uvicorn has no `limit_concurrency`, and the classic slowloris is limited only by the container's nofile limit.

## New defects

| file:line | Trigger | Observed vs expected | Sev | Deploy-blocking? | Conf |
|---|---|---|---|---|---|
| middleware.py:302 + config.py:165 | Many unauthenticated connections each stay under the 256 KiB grace and send 1 byte every <30 s | Budget is held for up to 9000 s and the global budget exhausts, so every MCP call gets 503. Expected: a time-based floor from the first byte, e.g. reject when `now > started + grace_bytes/min_rate + size/min_rate`, which caps under-grace holds at about 32–64 s. | High | yes | High (reproduced) |
| compose.prod.yaml:37 + middleware.py:292 | Any body over 1 MiB spools to the 64 MiB tmpfs | Two concurrent 38.4 MB contract-max writes: the second gets 503. One IP holds the tmpfs full for about 2.2 h at trivial cost. Expected: tmpfs ≥ global budget (e.g. `size=320m`, counted in mem_limit) or TMPDIR on a volume, plus a sliding-window rate so an early burst cannot buy hours of hold time. | Medium | no (only bodies over 1 MiB are hit), but it is a one-line fix | High |
| app.py:375 | About 1k idle sockets | No `limit_concurrency`, so fd exhaustion is possible. This existed before; the 30 s inactivity timer makes it about 3× cheaper. | Low–Med | no → BACKLOG | Med |
| bootstrap.sh:195 | Re-run with a changed --admin-cidr | `ufw delete … comment` may be rejected. Under `set -e` the script would then stop before 80/443 and `enable`. Any pre-existing provider "allow 22" rule makes --admin-cidr ineffective. | Low | no | Low |
| middleware body replay | Handler reads the full body | The final in-handler copy is no longer charged to the budget. | Low | no | Med |

## Verdict
ANOTHER ROUND NEEDED: the D1 residual (High, pre-auth global-budget DoS via unrated under-grace connections held for 9000 s). The fix is small: a time-based rate floor applied from the first byte (middleware.py:302). The tmpfs sizing (compose.prod.yaml:37) can go in the same change.

Note: the 21-codex-final-deploy.md author report still says main lacks D-039 proxy support. That is stale now that cc67687 is merged. The real client-IP gate should be re-run on main.