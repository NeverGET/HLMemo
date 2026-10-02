#!/usr/bin/env bash
# R4 (R-15, plan §2.3): the read-only pre-push gate for a PUBLIC push of <ref>... Every blob in the
# pushed history becomes public (deleted and now-ignored files included), so the checks cover the
# full history of the refs, not a diff:
#   gitleaks       gitleaks over the FULL history of the refs (`gitleaks git --log-opts`, or
#                  `gitleaks detect --log-opts` on an older gitleaks; detected at run time), default
#                  rules: no .gitleaks.toml/.gitleaksignore, no GITLEAKS_CONFIG*, gitleaks:allow
#                  comments ignored. Findings are listed as rule/file/line/commit, never the secret.
#   private-paths  no docs/private/, deploy/.local/ or .env / .env.* path (templates ending in .example
#                  excepted: .env.example, .env.prod.example) in `git ls-tree -r` of each ref, nor anywhere
#                  in the refs' history.
#   large-blobs    no blob larger than 5 MB in the refs' history.
# --base REF (e.g. origin/main): the two HISTORY checks (private-paths history, large-blobs) skip what
# REF already contains: it is public already, and blocking the push cannot unpublish it. The tree
# checks, key-grep and gitleaks (full history) are unaffected.
#   key-grep       a grep of every tracked file of each ref for key-shaped strings (Google AIza…,
#                  OpenRouter sk-or-…, Anthropic sk-ant-…, GitHub ghp_…, a PEM private key); it lists
#                  ref:path only, never the match.
#   owner-terms    D-220: the public repo must never contain data about the owner's other projects. The
#                  denylist is PRIVATE and gitignored: docs/private/publish-denylist.txt in the repo (one
#                  term per line, `#` comments, case-insensitive literal match; an empty file = no terms =
#                  PASS; a missing file = FAIL). Scans the ADDED lines of the history range (with --base:
#                  only commits not in REF; otherwise the full history), the commit messages of that
#                  range, and the tracked content of each ref. With --base, tree hits whose blob is
#                  already in the base tree at the same path are skipped (already public). It lists
#                  ref:path / commit <sha> with the line NUMBER only, never the term or the line text.
# Prints PASS/FAIL per check and a RESULT line. Exit 0 = every check PASS, 1 = a FAIL, 64 = usage.
# Usage: prepush_check.sh [--repo DIR] [--base REF] <ref>...   e.g. prepush_check.sh --base origin/main main r4-rc
set -uo pipefail

MAX_BLOB_BYTES=5242880
KEY_PATTERN='AIza[0-9A-Za-z_-]{30,}|sk-or-[0-9A-Za-z-]{20,}|sk-ant-[0-9A-Za-z_-]{20,}|ghp_[0-9A-Za-z]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY'
PRIVATE_PATH='^(docs/private/|deploy/\.local/)|(^|/)\.env(\.[^/]*)?$'
ENV_EXAMPLE='(^|/)\.env(\.[^/]*)?\.example$'

usage() {
    echo "usage: prepush_check.sh [--repo DIR] [--base REF] <ref>..." >&2
    exit 64
}

repo=.
base=
while [ $# -gt 0 ]; do
    case "$1" in
        --repo)
            [ $# -ge 2 ] || usage
            repo=$2
            shift 2
            ;;
        --base)
            [ $# -ge 2 ] || usage
            base=$2
            shift 2
            ;;
        -h | --help) usage ;;
        --)
            shift
            break
            ;;
        -*) usage ;;
        *) break ;;
    esac
done
[ $# -ge 1 ] || usage
cd "$repo" 2>/dev/null || {
    echo "error: no such directory: $repo" >&2
    exit 64
}
git rev-parse --git-dir >/dev/null 2>&1 || {
    echo "error: not a git repository: $repo" >&2
    exit 64
}
refs=("$@")
exclude=()
if [ -n "$base" ]; then
    git rev-parse --verify -q "${base}^{commit}" >/dev/null || {
        echo "error: unknown base: $base" >&2
        exit 64
    }
    exclude=("^$base")
fi
for ref in "${refs[@]}"; do
    git rev-parse --verify -q "${ref}^{commit}" >/dev/null || {
        echo "error: unknown ref: $ref" >&2
        exit 64
    }
done

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
failed=0

result() { # <check> <PASS|FAIL> <detail>
    printf '%-4s  %-13s  %s\n' "$2" "$1" "$3"
    [ "$2" = PASS ] || failed=1
}

indent() { sed -e 's/^/        /'; }

check_gitleaks() {
    if ! command -v gitleaks >/dev/null 2>&1; then
        result gitleaks FAIL "gitleaks is not installed (the full-history scan is required)"
        return
    fi
    if [ -e .gitleaks.toml ] || [ -e .gitleaksignore ]; then
        result gitleaks FAIL "a .gitleaks.toml or .gitleaksignore would replace the default rules"
        return
    fi
    local cmd flags help
    help=$(gitleaks --help 2>&1)
    flags=(--no-banner --redact --exit-code 1 --report-format json --report-path "$tmp/gitleaks.json")
    flags+=("--log-opts=--full-history ${refs[*]}")
    case "$help" in *--ignore-gitleaks-allow*) flags+=(--ignore-gitleaks-allow) ;; esac
    mkdir -p "$tmp/noignore"
    case "$help" in *--gitleaks-ignore-path*) flags+=(--gitleaks-ignore-path "$tmp/noignore") ;; esac
    if gitleaks git --help >/dev/null 2>&1; then
        cmd=(gitleaks git "${flags[@]}" .)
    else
        cmd=(gitleaks detect --source . "${flags[@]}")
    fi
    local rc=0
    env -u GITLEAKS_CONFIG -u GITLEAKS_CONFIG_TOML "${cmd[@]}" >"$tmp/gitleaks.log" 2>&1 || rc=$?
    if [ "$rc" -eq 0 ]; then
        result gitleaks PASS "no finding in the full history of: ${refs[*]} (${cmd[1]})"
    elif [ "$rc" -eq 1 ] && [ -s "$tmp/gitleaks.json" ]; then
        result gitleaks FAIL "findings in the history of: ${refs[*]}"
        python3 - "$tmp/gitleaks.json" <<'PY' | indent
import json, sys
for f in json.load(open(sys.argv[1])):
    print(f"{f.get('RuleID')}  {f.get('File')}:{f.get('StartLine')}  commit {str(f.get('Commit'))[:12]}")
PY
    else
        result gitleaks FAIL "gitleaks could not scan (exit $rc)"
    fi
}

check_private_paths() {
    local ref hits=0
    : >"$tmp/private"
    for ref in "${refs[@]}"; do
        git ls-tree -r --name-only "$ref" | grep -E "$PRIVATE_PATH" | grep -vE "$ENV_EXAMPLE" |
            sed -e "s|^|$ref:|" >>"$tmp/private"
    done
    git log --format= --name-only "${refs[@]}" "${exclude[@]}" | grep -E "$PRIVATE_PATH" | grep -vE "$ENV_EXAMPLE" |
        sort -u | sed -e 's|^|history:|' >>"$tmp/private"
    hits=$(wc -l <"$tmp/private" | tr -d ' ')
    if [ "$hits" -eq 0 ]; then
        result private-paths PASS "no docs/private/, deploy/.local/ or .env path in the trees or history"
    else
        result private-paths FAIL "$hits private path(s) in the trees or history"
        indent <"$tmp/private"
    fi
}

check_large_blobs() {
    git rev-list --objects "${refs[@]}" "${exclude[@]}" |
        git cat-file --batch-check='%(objecttype) %(objectname) %(objectsize) %(rest)' |
        awk -v max="$MAX_BLOB_BYTES" '$1 == "blob" && $3 > max { print $3 " bytes  " substr($0, index($0, $4)) }' \
            >"$tmp/large"
    if [ -s "$tmp/large" ]; then
        result large-blobs FAIL "blob(s) over $MAX_BLOB_BYTES bytes in the history"
        indent <"$tmp/large"
    else
        result large-blobs PASS "no blob over $MAX_BLOB_BYTES bytes in the history"
    fi
}

DENYLIST_REL=docs/private/publish-denylist.txt

# Reads "ref<NUL>line<NUL>text" git-grep records on stdin; prints "ref:path:line" (never the text),
# dropping paths whose blob is identical in the base tree (already public).
OWNER_TREE_FILTER='
import subprocess, sys
ref, base = sys.argv[1], sys.argv[2]
seen = {}
def blob(r, path):
    p = subprocess.run(["git", "rev-parse", "-q", "--verify", r + ":" + path], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None
for raw in sys.stdin.buffer:
    parts = raw.rstrip(b"\n").split(b"\0", 2)
    if len(parts) < 3:
        continue
    path = parts[0].decode("utf-8", "replace")[len(ref) + 1:]
    if base:
        if path not in seen:
            mine = blob(ref, path)
            seen[path] = mine is not None and mine == blob(base, path)
        if seen[path]:
            continue
    print(ref + ":" + path + ":" + parts[1].decode())
'

# Reads `git log -p -U0 --format="@@COMMIT <sha>"` on stdin; prints "commit <sha12>  path:line" for each
# ADDED line containing a term (terms file = $1). Hunk-aware, so "+++"/"---" content lines are not headers.
OWNER_LOG_AWK='
BEGIN { while ((getline t < tf) > 0) terms[++n] = tolower(t) }
function hit(s,   i, l) { l = tolower(s); for (i = 1; i <= n; i++) if (index(l, terms[i])) return 1; return 0 }
/^@@COMMIT / { sha = substr($2, 1, 12); orem = nrem = 0; next }
orem > 0 && substr($0, 1, 1) == "-" { orem--; next }
nrem > 0 && substr($0, 1, 1) == "+" { nrem--; if (hit(substr($0, 2))) print "commit " sha "  " file ":" ln; ln++; next }
/^\+\+\+ / { file = substr($0, 5); sub(/^b\//, "", file); next }
/^@@ / {
    split($2, o, ","); split($3, c, ",")
    orem = (o[2] == "" ? 1 : o[2]); nrem = (c[2] == "" ? 1 : c[2]); ln = substr(c[1], 2) + 0; next
}
'

check_owner_terms() {
    local list="$PWD/$DENYLIST_REL" terms="$tmp/terms" ref sha rc
    local range=("${refs[@]}" "${exclude[@]}")
    if [ ! -f "$list" ]; then
        result owner-terms FAIL "denylist missing: create $DENYLIST_REL (one owner project term per line, # comments; an empty file means no terms)"
        return
    fi
    # strip CR, comments, surrounding blanks and empty lines (an empty pattern would match everything)
    tr -d '\r' <"$list" | sed -e 's/#.*//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' | grep -v '^$' >"$terms" || true
    : >"$tmp/owner"
    if [ -s "$terms" ]; then
        git -c core.quotepath=off log -p -U0 --no-color --format='@@COMMIT %H' "${range[@]}" |
            awk -v tf="$terms" "$OWNER_LOG_AWK" >>"$tmp/owner"
        for sha in $(git log --format=%H "${range[@]}"); do
            if git show -s --format=%B "$sha" | grep -qiF -f "$terms"; then
                echo "commit $sha  (message)" >>"$tmp/owner"
            fi
        done
        for ref in "${refs[@]}"; do
            git grep -I -n -i -F -f "$terms" --null "$ref" -- 2>/dev/null |
                python3 -c "$OWNER_TREE_FILTER" "$ref" "$base" >>"$tmp/owner"
            rc=("${PIPESTATUS[@]}") # git grep: 0 = match, 1 = none, >1 = error
            if [ "${rc[0]}" -gt 1 ] || [ "${rc[1]}" -ne 0 ]; then
                result owner-terms FAIL "could not scan the tree of $ref"
                return
            fi
        done
    fi
    if [ -s "$tmp/owner" ]; then
        result owner-terms FAIL "$(sort -u "$tmp/owner" | wc -l | tr -d ' ') owner-term hit(s) (location and line number only; the term is not shown)"
        sort -u "$tmp/owner" | indent
    else
        result owner-terms PASS "no denylisted owner term in the scanned history, messages or trees ($(wc -l <"$terms" | tr -d ' ') term(s))"
    fi
}

check_key_grep() {
    local ref rc bad=0
    : >"$tmp/keys"
    for ref in "${refs[@]}"; do
        rc=0
        git grep -l -I -E -e "$KEY_PATTERN" "$ref" -- >>"$tmp/keys" 2>/dev/null || rc=$?
        [ "$rc" -le 1 ] || bad=1
    done
    if [ "$bad" -ne 0 ]; then
        result key-grep FAIL "git grep could not run"
    elif [ -s "$tmp/keys" ]; then
        result key-grep FAIL "key-shaped string(s) in tracked content (ref:path; the match is not shown)"
        indent <"$tmp/keys"
    else
        result key-grep PASS "no key-shaped string in the tracked content of: ${refs[*]}"
    fi
}

echo "pre-push checks for: ${refs[*]}${base:+ (history checks: not in $base)}"
check_gitleaks
check_private_paths
check_large_blobs
check_key_grep
check_owner_terms
if [ "$failed" -eq 0 ]; then
    echo "RESULT PASS"
    exit 0
fi
echo "RESULT FAIL"
exit 1
