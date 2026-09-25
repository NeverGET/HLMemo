#!/usr/bin/env bash
# R2 (D-058, D-066): install the librarian/risk-judge llm.env on the production host.
#
#   deploy/scripts/install_llm_env.sh --state DIR [--key-file FILE] [--profile P] [--fallback F] [--reset-operator-values]
#   deploy/scripts/install_llm_env.sh --state DIR --remove
#
# Builds llm.env from deploy/llm.env.example with HLM_LIBRARIAN_ENABLED=true,
# HLM_LIBRARIAN_ROLE=observer, HLM_PROFILE=openrouter-gpt6-luna, the template's fallbacks (D-094:
# HLM_FALLBACK_PROFILE and the per-task HLM_FALLBACK_PROFILE__<TASK> lines; --fallback replaces the
# default one only) and the template's budget guard (D-058 development defaults). Every profile the
# file names must exist. The key variable names come from those
# profiles (HLM_LLM_API_KEY = "env:NAME", D-017; R2: OPENROUTER_API_KEY) and ONLY those are
# read from --key-file (default: the repository's .env). The key travels to the host on ssh STDIN,
# never in argv, a log or this script's output, and is written atomically to <env dir>/llm.env
# (default /etc/hlmemo/llm.env, next to $HLM_REMOTE_ENV), 0600, owned by the deploy user.
# D-121: an install PRESERVES what the operator set by hand in the installed llm.env: the spend caps
# (HLM_LLM_BUDGET_HOUR/DAY/MONTH_USD) and every key variable (*_API_KEY, *_TOKEN, ...) keep their
# installed values (names are printed, never values); --reset-operator-values replaces them with the
# template's caps and the --key-file key. The guard itself stays on (HLM_LLM_BUDGET_DISABLED=false).
# Idempotent: identical content is left alone ("unchanged", no backup); a different previous file
# is first copied to llm.env.bak-<UTC stamp> (0600; the newest 3 are kept). --remove deletes
# llm.env and its backups (they hold keys). Containers read the file only when (re)created.
# D-116 #3 (review 75): on a host with a deployed release (release-state.json next to the app
# checkout, $HLM_REMOTE_DIR, default /opt/hlmemo/app) the install is ONE durable, resumable step
# under the SAME deploy lock as deploy/rollback: journal (release-state.json env_switch) -> write
# llm.env -> recreate librarian AND api together -> check_librarian.py evaluate --release r3 against
# the file on disk -> clear the journal. A kill at any point leaves the journal: deploy and rollback
# refuse until install_llm_env.sh is re-run, which finishes the step (the same file is "unchanged",
# both services are recreated again and checked), so api and librarian never stay on different
# env releases. A checkout that predates R3 is refused (D-108: deploy R3 first, then its env).
# SSH: host alias hlm-deploy from <state>/ssh_config (written by first_deploy.sh), like hlm_ops.sh.
set -Eeuo pipefail

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
usage() { sed -n '4,5p' "${BASH_SOURCE[0]}" | sed 's/^# *//'; }
die() { printf 'install_llm_env: %s\n' "$*" >&2; exit "${2:-64}"; }

state='' key_file=$REPO_ROOT/.env profile=openrouter-gpt6-luna fallback='' mode=install operator=keep
while (($#)); do
  case $1 in
    --state|--key-file|--profile|--fallback)
      (($# >= 2)) || die "$1 needs a value"
      case $1 in
        --state) state=$2 ;; --key-file) key_file=$2 ;; --profile) profile=$2 ;; --fallback) fallback=$2 ;;
      esac
      shift 2 ;;
    --remove) mode=remove; shift ;;
    --reset-operator-values) operator=reset; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
done
[[ -n $state ]] || { usage >&2; die '--state DIR is required (the target host is never guessed)'; }
[[ -n $fallback ]] || fallback=$(sed -n 's/^HLM_FALLBACK_PROFILE=//p' "$REPO_ROOT/deploy/llm.env.example" | tail -n 1)
ssh_config=${HLM_OPS_SSH_CONFIG:-$state/ssh_config}
[[ -f $ssh_config ]] || die "no SSH config at $ssh_config (run first_deploy.sh first)"
remote_env=${HLM_REMOTE_ENV:-/etc/hlmemo/prod.env}
remote_dir=${HLM_REMOTE_DIR:-/opt/hlmemo/app}
target=${HLM_REMOTE_LLM_ENV:-$(dirname "$remote_env")/llm.env}
[[ $target =~ ^/[a-zA-Z0-9_./-]+$ && $target != *..* ]] || die 'remote llm.env path must be absolute and contain no shell metacharacters'
[[ $remote_dir =~ ^/[a-zA-Z0-9_./-]+$ && $remote_env =~ ^/[a-zA-Z0-9_./-]+$ ]] || die 'remote paths must be absolute and contain no shell metacharacters'
for name in "$profile" "$fallback"; do
  [[ $name =~ ^[a-z0-9][a-z0-9_-]*$ && -f $REPO_ROOT/profiles/$name.toml ]] || die "unknown profile: $name (profiles/*.toml)"
done

# The remote side: validates the complete file (end marker), keeps a 0600 backup of a different
# previous file, replaces atomically and prints key NAMES (values of secret-named keys never).
read -r -d '' remote_py <<'PY' || true
import datetime, os, pwd, re, stat, sys
mode, target, operator = sys.argv[1], sys.argv[2], sys.argv[3]
PRESERVE = ("HLM_LLM_BUDGET_HOUR_USD", "HLM_LLM_BUDGET_DAY_USD", "HLM_LLM_BUDGET_MONTH_USD")
SECRET = re.compile(r"(API_KEY|TOKEN|SECRET|PASSWORD)$")
KEEP, END = 3, "# END llm.env (install_llm_env.sh)"
directory, name = os.path.dirname(target), os.path.basename(target)
user = pwd.getpwuid(os.getuid()).pw_name
def fail(msg):
    print(f"install_llm_env (remote): {msg}; nothing changed", file=sys.stderr)
    sys.exit(3)
def backups():
    return sorted(n for n in os.listdir(directory) if n.startswith(name + ".bak-"))
def summary(text):
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.startswith("#"):
            secret = re.search(r"(API_KEY|TOKEN|SECRET|PASSWORD)$", key)
            print(f"  {key}={('<set>' if value else '<empty>') if secret else value}")
try:
    info = os.stat(directory)
except OSError:
    fail(f"{directory} does not exist")
if info.st_uid != os.getuid():
    fail(f"{directory} is not owned by {user} (RUNBOOK: chown the env directory to the deploy user)")
exists = os.path.lexists(target)
if exists and not stat.S_ISREG(os.lstat(target).st_mode):
    fail(f"{target} is not a regular file")
if exists and os.lstat(target).st_uid != os.getuid():
    fail(f"{target} is not owned by {user}")
if mode == "remove":
    gone = [p for p in [target] + [os.path.join(directory, n) for n in backups()] if os.path.lexists(p)]
    for path in gone:
        os.remove(path)
        print(f"removed {path}")
    print("llm.env absent" if not gone else f"removed {len(gone)} file(s)")
    sys.exit(0)
data = sys.stdin.buffer.read()
try:
    text = data.decode("utf-8")
except UnicodeDecodeError:
    fail("llm.env on stdin is not UTF-8")
if text.rstrip("\n").rsplit("\n", 1)[-1] != END or "HLM_LIBRARIAN_ENABLED=" not in text:
    fail("incomplete llm.env on stdin (no end marker)")
if exists and operator != "reset":
    # D-121: the operator's hand edits in the INSTALLED file win (caps and keys), unless reset
    installed = {}
    with open(target, encoding="utf-8", errors="replace") as fh:
        for line in fh.read().splitlines():
            key, sep, value = line.partition("=")
            if sep and not line.startswith("#"):
                installed[key.strip()] = value
    merged, kept = [], []
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and not line.startswith("#") and (key in PRESERVE or SECRET.search(key)):
            if installed.get(key) and installed[key] != value:
                line = f"{key}={installed[key]}"
                kept.append(key)
        merged.append(line)
    text = "\n".join(merged) + "\n"
    data = text.encode("utf-8")
    for key in kept:
        print(f"kept the operator's {key} from the installed llm.env (--reset-operator-values replaces it)")
if exists:
    with open(target, "rb") as fh:
        old = fh.read()
    if old == data:
        os.chmod(target, 0o600)
        print(f"unchanged {target} (0600 {user})")
        summary(text)
        sys.exit(0)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup, n = f"{target}.bak-{stamp}", 1
    while os.path.lexists(backup):
        backup, n = f"{target}.bak-{stamp}-{n}", n + 1
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(old)
        fh.flush()
        os.fsync(fh.fileno())
    print(f"backup {backup} (0600)")
tmp = f"{target}.tmp-{os.getpid()}"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, target)
finally:
    if os.path.lexists(tmp):
        os.remove(tmp)
dfd = os.open(directory, os.O_RDONLY)
try:
    os.fsync(dfd)
finally:
    os.close(dfd)
for old_backup in backups()[:-KEEP]:
    os.remove(os.path.join(directory, old_backup))
    print(f"pruned old backup {old_backup}")
final = os.stat(target)
if stat.S_IMODE(final.st_mode) != 0o600 or final.st_uid != os.getuid():
    fail(f"{target} is not 0600 {user} after the write")
print(f"installed {target} (0600 {user})")
summary(text)
PY
# D-116 #3: the remote step, in bash, under the deploy lock when a release is deployed there.
read -r -d '' remote_sh <<'SH' || true
set -Eeuo pipefail
umask 077
mode=$1 target=$2 app_dir=$3 remote_env=$4 remote_py=$5 operator=$6
parent=$(dirname "$app_dir")
deployed=0
[[ -f $parent/release-state.json && -f $app_dir/deploy/scripts/release_state.py ]] && deployed=1
if ((deployed)); then
  exec 9>"$parent/.deploy.lock"
  flock -n 9 || { echo 'install_llm_env (remote): another deployment is running; nothing changed' >&2; exit 3; }
fi
apply=0
if ((deployed)) && [[ $mode == install ]]; then
  grep -q RELEASE_MANIFESTS "$app_dir/deploy/scripts/check_librarian.py" || {
    echo 'install_llm_env (remote): the deployed release predates R3; deploy R3 first, then install its llm.env (D-108 Order B); nothing changed' >&2
    exit 3
  }
  apply=1
  python3 "$app_dir/deploy/scripts/release_state.py" begin-env-switch "$parent" </dev/null
fi
python3 -c "$remote_py" "$mode" "$target" "$operator"
((apply)) || exit 0
cd "$app_dir"
export HLM_ENV_FILE=$remote_env HLM_LLM_ENV_FILE=$target
echo 'install_llm_env (remote): recreating librarian and api with this llm.env (deploy lock held)'
bash deploy/scripts/stack.sh up -d --no-deps --wait --wait-timeout 300 librarian api </dev/null
reports=$(mktemp -d)
trap 'rm -rf -- "$reports"' EXIT
bash deploy/scripts/stack.sh exec -T librarian python - collect --service librarian --probe --wait-heartbeat 45 \
  < deploy/scripts/check_librarian.py > "$reports/l.json" || true
bash deploy/scripts/stack.sh exec -T api python - collect --service api < deploy/scripts/check_librarian.py > "$reports/a.json" || true
python3 deploy/scripts/check_librarian.py evaluate --llm-env present --llm-env-file "$target" --release r3 \
  --librarian "$reports/l.json" --api "$reports/a.json" </dev/null
python3 deploy/scripts/release_state.py end-env-switch "$parent" </dev/null
echo 'install_llm_env (remote): switch complete (both services run this llm.env, the check passed)'
SH
printf -v remote_cmd 'exec bash -c %q install_llm_env %q %q %q %q %q %q' "$remote_sh" "$mode" "$target" "$remote_dir" "$remote_env" "$remote_py" "$operator"
rssh() { ssh -F "$ssh_config" -o BatchMode=yes hlm-deploy "$@"; }

if [[ $mode == remove ]]; then
  rssh "$remote_cmd" </dev/null
  echo "Running containers keep the key until recreated: stack.sh up -d --no-deps librarian api (RUNBOOK)."
  exit 0
fi

[[ -f $key_file && -r $key_file ]] || die "key file not readable: $key_file (pass --key-file FILE)"
# Build the whole file first (the key stays in this shell's memory; printf is a builtin, so it never
# appears in any process argv), then send it in one piece on ssh stdin.
content=$(python3 - "$REPO_ROOT/deploy/llm.env.example" "$key_file" "$REPO_ROOT/profiles" "$profile" "$fallback" <<'PY'
import os, re, sys, tomllib
example, key_file, profiles, primary, fallback = sys.argv[1:6]
END = "# END llm.env (install_llm_env.sh)"
def fail(msg):
    print(f"install_llm_env: {msg}", file=sys.stderr)
    sys.exit(65)
# D-094: the per-task fallbacks the template sets are kept verbatim; their profiles must exist too
task_profiles = []
with open(example, encoding="utf-8") as fh:
    for line in fh:
        m = re.fullmatch(r"HLM_FALLBACK_PROFILE__[A-Z][A-Z0-9_]*=(.*)", line.strip())
        if m and m.group(1).strip():
            task_profiles.append(m.group(1).strip())
names = []
for profile in dict.fromkeys((primary, fallback, *task_profiles)):
    path = os.path.join(profiles, profile + ".toml")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", profile) or not os.path.isfile(path):
        fail(f"unknown profile in llm.env: {profile} (profiles/*.toml)")
    with open(path, "rb") as fh:
        ref = str(tomllib.load(fh).get("HLM_LLM_API_KEY", ""))
    m = re.fullmatch(r"env:([A-Z][A-Z0-9_]*)", ref)
    if not m:
        fail(f"profile {profile}: HLM_LLM_API_KEY is not an env:NAME reference")
    names.append(m.group(1))
names = list(dict.fromkeys(names))
found = {}
line_re = re.compile(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*")
with open(key_file, encoding="utf-8") as fh:
    for line in fh:
        m = line_re.fullmatch(line.rstrip("\n"))
        if m and m.group(1) in names:  # only the profiles' key variables are ever read
            value = m.group(2)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            found[m.group(1)] = value
for name in names:
    value = found.get(name, "")
    if not value:
        fail(f"{name} is missing or empty in {key_file}")
    if not re.fullmatch(r"[A-Za-z0-9._~+/=:-]{16,512}", value):
        fail(f"{name} in {key_file} has an unexpected length or characters (value not shown)")
settings = {
    "HLM_LIBRARIAN_ENABLED": "true",
    "HLM_LIBRARIAN_ROLE": "observer",
    "HLM_PROFILE": primary,
    "HLM_FALLBACK_PROFILE": fallback,
    # D-111/D-116: the release marker (check_librarian.py evaluate checks this env against the R3
    # manifest: no query rewrite, no per-source cap, the D-094 fallback mapping)
    "HLM_ENV_RELEASE": "r3",
    **{name: found[name] for name in names},
}
out = [
    "# Installed by deploy/scripts/install_llm_env.sh from deploy/llm.env.example (R2: librarian ON,",
    "# observer). 0600, deploy user. Holds the provider key: never copy it into the checkout.",
]
applied = set()
with open(example, encoding="utf-8") as fh:
    for line in fh.read().splitlines():
        key = line.split("=", 1)[0] if "=" in line and not line.startswith("#") else None
        if key in settings:
            line = f"{key}={settings[key]}"
            applied.add(key)
        out.append(line)
out += [f"{k}={v}" for k, v in settings.items() if k not in applied]
out.append(END)
sys.stdout.write("\n".join(out) + "\n")
PY
) || die "llm.env not built; nothing was sent" 65
echo "install_llm_env: $target on hlm-deploy ($ssh_config): profile=$profile fallback=$fallback role=observer enabled=true"
printf '%s\n' "$content" | rssh "$remote_cmd"
unset content
echo "Done. On a deployed R3 host the switch (both services recreated + the --release r3 check) ran under the deploy lock; re-run this command if it was interrupted (RUNBOOK \"R3 release\")."
