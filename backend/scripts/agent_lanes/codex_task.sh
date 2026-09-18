#!/bin/bash
# Run one Codex CLI headless task as a coding agent (sibling of grok_task.sh).
# Usage: codex_task.sh <name> <cwd> <prompt-file> [effort=high] [sandbox=workspace-write] [resume-session-id]
# Output: $OUT/<name>/{events.jsonl,last.txt,err.log,prompt.md}; last block of stdout = summary line + final message tail.
# Note: the model/provider come from ~/.codex/config.toml (managed by cc-switch); this script never reads keys.
set -u
NAME="$1"; CWD="$2"; PROMPT_FILE="$3"; EFFORT="${4:-high}"; SANDBOX="${5:-workspace-write}"; RESUME="${6:-}"
CODEX="${CODEX_BIN:-/Applications/ChatGPT.app/Contents/Resources/codex}"
OUT="${AGENT_TASK_OUT:-$HOME/.cache/simpleharness-agent-tasks/codex}/$NAME"
mkdir -p "$OUT"; cp "$PROMPT_FILE" "$OUT/prompt.md"
RULES='Rules (binding): 1) Never run git stash, git checkout, git reset --hard, git rebase, or git push. 2) Never print, cat, or copy API keys, tokens, .env files, llm_runtime*.json, ~/.grok/auth.json, or ~/.codex/auth.json / config.toml. 3) Work only inside the given working directory. 4) Write all project record documents (journal, notes, reports) in Chinese; code and code comments in English. 5) Tests first: write the failing test before the fix. 6) Do not delete files unless the task says so. 7) End your final reply with a section "## 结果" listing files changed, tests run with pass/fail counts, and anything left undone.'
ARGS=(-s "$SANDBOX" --skip-git-repo-check --json -o "$OUT/last.txt" -C "$CWD" -c "model_reasoning_effort=\"$EFFORT\"")
# git worktrees commit into the main repo's .git — make that writable too
COMMON=$(git -C "$CWD" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)
[ -n "$COMMON" ] && [ -d "$COMMON" ] && ARGS+=(--add-dir "$COMMON")
[ -d "$HOME/.cache/uv" ] && ARGS+=(--add-dir "$HOME/.cache/uv")
PROMPT="$RULES

$(cat "$PROMPT_FILE")"
START=$(date +%s); rm -f "$OUT/last.txt"
if [ -n "$RESUME" ]; then "$CODEX" exec resume "${ARGS[@]}" "$RESUME" "$PROMPT" < /dev/null > "$OUT/events.jsonl" 2> "$OUT/err.log"
else "$CODEX" exec "${ARGS[@]}" "$PROMPT" < /dev/null > "$OUT/events.jsonl" 2> "$OUT/err.log"; fi
RC=$?; END=$(date +%s)
python3 - "$OUT" "$RC" "$((END-START))" <<'PY'
import json, sys, pathlib
out, rc, secs = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
sid, toks, turns = None, None, 0
for line in (out/"events.jsonl").read_text(errors="ignore").splitlines():
    try: e = json.loads(line)
    except Exception: continue
    sid = sid or e.get("thread_id") or e.get("session_id") or (e.get("thread") or {}).get("id")
    if e.get("type","").endswith("turn.completed") or e.get("type")=="turn.completed":
        turns += 1; u = e.get("usage") or {}; toks = (toks or 0) + sum(v for v in u.values() if isinstance(v,int))
last = (out/"last.txt").read_text(errors="ignore") if (out/"last.txt").exists() else ""
print(f"CODEX_TASK rc={rc} secs={secs} turns={turns} tokens={toks} session={sid}")
print("---- text (tail) ----"); print(last[-4000:])
PY
