#!/usr/bin/env bash
# Two isolated blind graders for a migration's blind check (TEMPLATE §7, D-216). Each grader works in its own directory
# OUTSIDE the repository that holds ONLY packets/ + READER-INSTRUCTIONS.md + its reader order (no key, no answers, no
# truth set). Grader 1: `claude -p` (file tools confined to its dir, no MCP, HLM_CAPTURE=off). Grader 2: `codex exec`
# (workspace-write in its dir). Outputs are copied back BY CODE into <work>/graders and <work>/grades-<n>.jsonl (0600).
#
#   bash tools/migrate/blindcheck/run_graders.sh <work dir of blindcheck.py> <grader root outside the repo>
# Environment: GRADER1_MODEL (default sonnet), GRADER2_MODEL (default gpt-6-astra), GRADER2_EFFORT (default low).
set -euo pipefail
umask 077
W="$(cd "${1:?work dir}" && pwd)"
ROOT="${2:?grader root (outside the repository)}"
G1=${GRADER1_MODEL:-sonnet}
G2=${GRADER2_MODEL:-gpt-6-astra}
E2=${GRADER2_EFFORT:-low}
for f in packets READER-INSTRUCTIONS.md reader-order-1.txt reader-order-2.txt; do
  [[ -e $W/$f ]] || { echo "missing $W/$f (run blindcheck.py packets)" >&2; exit 64; }
done
mkdir -p "$ROOT" "$W/graders"
for n in 1 2; do
  d="$ROOT/grader-$n"
  rm -rf "$d"; mkdir -p "$d"
  cp -R "$W/packets" "$d/packets"
  cp "$W/READER-INSTRUCTIONS.md" "$W/reader-order-$n.txt" "$d/"
done

prompt() {
  local n="$1"
  cat <<EOF
You are blind grader $n. Work ONLY inside the current directory. Read READER-INSTRUCTIONS.md, then reader-order-$n.txt,
then every packet packets/<code>.json in that order. Grade each packet exactly as READER-INSTRUCTIONS.md says, from the
packet alone. Write grades-$n.jsonl in the current directory: one JSON object per line, in reader order, with exactly the
keys "code", "grade", "why". Allowed grades: answerable packets -> correct | incorrect | superseded_as_current |
contradiction; negative packets (answerable=false) -> abstention | fabricated. Do not open any file outside this directory.
Finish with a self-check (every code exactly once, valid grades) and reply with one line: "grades-$n.jsonl written, <k> lines".
EOF
}

( cd "$ROOT/grader-1" && HLM_CAPTURE=off claude -p "$(prompt 1)" --model "$G1" --restricted --strict-mcp-config \
    --tools "Read,Write,Glob" --allowedTools "Read" "Write" "Glob" --permission-mode acceptEdits \
    --output-format json --max-turns 60 > "$ROOT/grader-1.out.json" 2> "$ROOT/grader-1.err" ) &
p1=$!
( prompt 2 > "$ROOT/grader-2.prompt.md"
  HLM_CAPTURE=off codex exec --skip-git-repo-check -s workspace-write -m "$G2" -c model_reasoning_effort="$E2" \
    -C "$ROOT/grader-2" -o "$ROOT/grader-2.last.md" - < "$ROOT/grader-2.prompt.md" > "$ROOT/grader-2.out.log" 2>&1 ) &
p2=$!
rc1=0; rc2=0
wait "$p1" || rc1=$?
wait "$p2" || rc2=$?
echo "grader-1 rc=$rc1 grader-2 rc=$rc2"
cp "$ROOT/grader-1.out.json" "$ROOT/grader-1.err" "$W/graders/" 2>/dev/null || true
cp "$ROOT/grader-2.prompt.md" "$ROOT/grader-2.out.log" "$ROOT/grader-2.last.md" "$W/graders/" 2>/dev/null || true
for n in 1 2; do
  if [[ -f $ROOT/grader-$n/grades-$n.jsonl ]]; then
    cp "$ROOT/grader-$n/grades-$n.jsonl" "$W/grades-$n.jsonl"
    chmod 600 "$W/grades-$n.jsonl"
    echo "grades-$n.jsonl lines: $(grep -c . "$W/grades-$n.jsonl")"
  else
    echo "grades-$n.jsonl MISSING"
  fi
done
chmod 600 "$W"/graders/* 2>/dev/null || true
