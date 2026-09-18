#!/bin/bash
# Run one Grok Build headless task as a coding agent.
# Usage: grok_task.sh <name> <cwd> <prompt-file> [effort=high] [max-turns=80] [resume-session-id]
# Output: $OUT/<name>/{out.json,err.log,prompt.md}; last line of stdout = summary line.
set -u
NAME="$1"; CWD="$2"; PROMPT_FILE="$3"; EFFORT="${4:-high}"; MAXT="${5:-80}"; RESUME="${6:-}"
OUT="${AGENT_TASK_OUT:-$HOME/.cache/simpleharness-agent-tasks/grok}/$NAME"
mkdir -p "$OUT"
cp "$PROMPT_FILE" "$OUT/prompt.md"
RULES='Rules: 1) Never run git stash, git checkout, git reset --hard, or git push. 2) Never print, cat, or copy API keys, tokens, .env files, llm_runtime*.json, or ~/.grok/auth.json. 3) Work only inside the given working directory. 4) Write all project record documents (journal, notes, reports) in Chinese; code and code comments in English. 5) Tests first: write the failing test before the fix. 6) Do not delete files unless the task says so. 7) End your final reply with a section "## 结果" listing files changed, tests run with pass/fail counts, and anything left undone.'
ARGS=(-p "$(cat "$PROMPT_FILE")" --cwd "$CWD" --always-approve --output-format json --max-turns "$MAXT" --effort "$EFFORT" --disable-web-search -m grok-4.6 --rules "$RULES" --deny "Bash(git push*)" --deny "Bash(git stash*)" --deny "Bash(git checkout*)" --deny "Bash(git reset --hard*)" --deny "Bash(*auth.json*)" --deny "Bash(*llm_runtime*)" --deny "Read(**/auth.json)" --deny "Read(**/llm_runtime*)" --deny "Read(**/.env*)" --deny "Read(**/*.env)")
if [ -n "$RESUME" ]; then ARGS+=(-r "$RESUME"); fi
START=$(date +%s)
grok "${ARGS[@]}" > "$OUT/out.json" 2> "$OUT/err.log"
RC=$?
END=$(date +%s)
python3 - "$OUT/out.json" "$RC" "$((END-START))" <<'EOF'
import json, sys
p, rc, secs = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    j = json.load(open(p))
    u = j.get("usage", {})
    print(f"GROK_TASK rc={rc} secs={secs} stop={j.get('stopReason')} turns={j.get('num_turns')} "
          f"tokens={u.get('total_tokens')} out={u.get('output_tokens')} cost_usd={j.get('total_cost_usd')} session={j.get('sessionId')}")
    print("---- text (tail) ----")
    print((j.get("text") or "")[-4000:])
except Exception as e:
    print(f"GROK_TASK rc={rc} secs={secs} parse_error={e}")
    print(open(p).read()[-2000:])
EOF
