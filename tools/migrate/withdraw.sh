#!/usr/bin/env bash
# OPERATOR TOOL (protocol R20, RUNBOOK "Librarian pending queue: withdraw"): withdraw the reviewed librarian proposals of
# ONE project in ONE evented, fail-closed step. Nothing in user memory changes; the questions only leave the queue, so a
# later librarian role promotion cannot mass-apply them. Run it only after the owner's OK on the reviewed id list.
#
#   bash tools/migrate/withdraw.sh <slug> <ids-file> <expect> [--status open|accepted_pending] [--owner NAME]
#                                  --reason 'why (recorded in the event)'
#
# Fail closed: (1) the live set of questions in --status must equal the ids file exactly and hold <expect> ids;
# (2) a dry run under the locks must withdraw exactly <expect>; (3) the apply writes ONE event. Any mismatch stops
# before anything is written.
# Environment: HLM_OPS_STATE (the deploy state dir), HLM_OPS_SSH_CONFIG (default $HLM_OPS_STATE/ssh_config),
# HLM_OPS_HOST (default hlm-deploy), HLM_OPS_APP (default /opt/hlmemo/app), HLM_OPS_ENV (default /etc/hlmemo/prod.env).
set -euo pipefail

SLUG=${1:?usage: withdraw.sh <slug> <ids-file> <expect> [--status S] [--owner NAME] --reason TEXT}
IDS=${2:?ids file}
EXPECT=${3:?expected count}
shift 3
STATUS=open OWNER="" REASON=""
while [[ $# -gt 0 ]]; do
  case $1 in
    --status) STATUS=$2; shift 2 ;;
    --owner) OWNER=$2; shift 2 ;;
    --reason) REASON=$2; shift 2 ;;
    *) echo "unknown argument $1" >&2; exit 64 ;;
  esac
done
[[ $SLUG =~ ^[a-z0-9][a-z0-9-]{1,63}$ ]] || { echo "bad slug" >&2; exit 64; }
[[ $EXPECT =~ ^[0-9]+$ && $EXPECT -gt 0 ]] || { echo "expect must be a positive integer" >&2; exit 64; }
[[ $STATUS == open || $STATUS == accepted_pending ]] || { echo "--status must be open or accepted_pending" >&2; exit 64; }
[[ -n $REASON ]] || { echo "--reason is required (it is recorded in the event)" >&2; exit 64; }
[[ $REASON != *"'"* ]] || { echo "--reason must not contain a single quote" >&2; exit 64; }
[[ -f $IDS ]] || { echo "ids file not found: $IDS" >&2; exit 64; }
: "${HLM_OPS_STATE:?set HLM_OPS_STATE to the deploy state dir}"
CFG=${HLM_OPS_SSH_CONFIG:-$HLM_OPS_STATE/ssh_config}
HOST=${HLM_OPS_HOST:-hlm-deploy}
APP=${HLM_OPS_APP:-/opt/hlmemo/app}
ENVF=${HLM_OPS_ENV:-/etc/hlmemo/prod.env}
OPS="cd $APP && HLM_ENV_FILE=$ENVF bash deploy/scripts/stack.sh exec -T api python -m hlmemo.ops librarian"
OWNER_ARG=""
[[ -n $OWNER ]] && OWNER_ARG="--owner $OWNER"
OUT=$(mktemp -d "${TMPDIR:-/tmp}/hlm-withdraw.XXXXXX")
chmod 700 "$OUT"
echo "withdraw: slug=$SLUG status=$STATUS expect=$EXPECT reports=$OUT" >&2

# 1. the live set (read-only) must equal the reviewed file
ssh -F "$CFG" "$HOST" "$OPS audit --project $SLUG --status $STATUS --json" </dev/null >"$OUT/live.json"
python3 - "$OUT/live.json" "$IDS" "$EXPECT" <<'PY'
import json, sys
raw = open(sys.argv[1]).read()
live = {p["question_id"] for p in json.loads(raw[raw.find("{"):])["proposals"]}
ids = {x.strip() for x in open(sys.argv[2]) if x.strip() and not x.startswith("#")}
n = int(sys.argv[3])
ok = len(live) == n and ids == live
print(f"ids {'PASS' if ok else 'FAIL'}: live {len(live)}, file {len(ids)}, expect {n}, equal {ids == live}")
sys.exit(0 if ok else 1)
PY

# 2. dry run under the locks: nothing written
ssh -F "$CFG" "$HOST" "$OPS withdraw --project $SLUG --ids-file - --reason '$REASON' --dry-run --json" <"$IDS" \
  >"$OUT/preview.json"
python3 - "$OUT/preview.json" "$EXPECT" "$STATUS" <<'PY'
import json, sys
raw = open(sys.argv[1]).read()
d = json.loads(raw[raw.find("{"):])
n, st = int(sys.argv[2]), sys.argv[3]
ok = d.get("dry_run") is True and d.get("withdrawn") == n and d.get("by_status") == {st: n}
print(f"preview {'PASS' if ok else 'FAIL'}: {d.get('withdrawn')} {d.get('by_status')}")
sys.exit(0 if ok else 1)
PY

# 3. withdraw: ONE event
# shellcheck disable=SC2086
ssh -F "$CFG" "$HOST" "$OPS withdraw --project $SLUG --ids-file - --reason '$REASON' $OWNER_ARG --json" <"$IDS" \
  >"$OUT/withdraw.json"
python3 - "$OUT/withdraw.json" "$EXPECT" <<'PY'
import json, sys
raw = open(sys.argv[1]).read()
d = json.loads(raw[raw.find("{"):])
ok = d.get("dry_run") is False and d.get("withdrawn") == int(sys.argv[2]) and bool(d.get("event_id"))
print(f"withdraw {'PASS' if ok else 'FAIL'}: {d.get('withdrawn')} event {d.get('event_id')}")
sys.exit(0 if ok else 1)
PY
