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
if [ "$failed" -eq 0 ]; then
    echo "RESULT PASS"
    exit 0
fi
echo "RESULT FAIL"
exit 1
