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
# `codex exec resume` accepts neither -s, -C nor --add-dir, so sandbox and writable roots go through -c
# overrides (valid for both forms) and the working directory through `cd`.
COMMON=$(git -C "$CWD" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)
ROOTS=""
[ -n "$COMMON" ] && [ -d "$COMMON" ] && ROOTS="\"$COMMON\""
[ -d "$HOME/.cache/uv" ] && ROOTS="${ROOTS:+$ROOTS, }\"$HOME/.cache/uv\""
ARGS=(--skip-git-repo-check --json -o "$OUT/last.txt" -c "model_reasoning_effort=\"$EFFORT\"" -c "sandbox_mode=\"$SANDBOX\"")
[ -n "$ROOTS" ] && ARGS+=(-c "sandbox_workspace_write.writable_roots=[$ROOTS]")
# The day-card endpoint allows only 2 requests in flight; when the local queueing gate (daycard_gate.py)
# is listening, send codex through it so agents queue for a slot instead of dying on HTTP 429.
GATE_PORT="${DAYCARD_GATE_DEV_PORT:-28182}"
if [ "${CODEX_USE_GATE:-auto}" != "0" ] && nc -z 127.0.0.1 "$GATE_PORT" 2>/dev/null; then
  ARGS+=(-c "model_providers.custom.base_url=\"http://127.0.0.1:$GATE_PORT/v1\"")
fi
cd "$CWD" || { echo "CODEX_TASK rc=97 cwd-not-found=$CWD"; exit 97; }
PROMPT="$RULES

$(cat "$PROMPT_FILE")"
START=$(date +%s); rm -f "$OUT/last.txt"; : > "$OUT/events.jsonl"; : > "$OUT/err.log"
# The endpoint rate-limits bursts (HTTP 429) and codex gives up after its own short retries; a slice
# that dies this way loses hours.  Retry here with backoff, resuming the same session when one exists.
MAX_TRIES="${CODEX_TASK_MAX_TRIES:-8}"; TRY=1; SID="$RESUME"; MSG="$PROMPT"
while :; do
  : > "$OUT/attempt.jsonl"
  if [ -n "$SID" ]; then "$CODEX" exec resume "${ARGS[@]}" "$SID" "$MSG" < /dev/null > "$OUT/attempt.jsonl" 2>> "$OUT/err.log"
  else "$CODEX" exec "${ARGS[@]}" "$MSG" < /dev/null > "$OUT/attempt.jsonl" 2>> "$OUT/err.log"; fi
  RC=$?; cat "$OUT/attempt.jsonl" >> "$OUT/events.jsonl"
  [ "$RC" -eq 0 ] && break
  grep -qE '429|Too Many Requests|stream disconnected|502 Bad Gateway|503 Service' "$OUT/attempt.jsonl" || break
  [ "$TRY" -ge "$MAX_TRIES" ] && break
  NEW_SID=$(grep -oE '"thread_id":"[^"]+"' "$OUT/attempt.jsonl" | head -1 | cut -d'"' -f4)
  if grep -q '"turn.completed"\|"item.completed"' "$OUT/attempt.jsonl" && [ -n "${NEW_SID:-$SID}" ]; then
    SID="${NEW_SID:-$SID}"; MSG="$RULES

The previous request was interrupted by server-side rate limiting (HTTP 429). Continue the same task from where you stopped; all earlier instructions still apply. Re-check git status first so you do not redo finished work."
  fi
  WAIT=$(( 45 * TRY + RANDOM % 40 )); echo "codex_task: transient failure (try $TRY/$MAX_TRIES), retry in ${WAIT}s" >> "$OUT/err.log"
  sleep "$WAIT"; TRY=$((TRY+1))
done
rm -f "$OUT/attempt.jsonl"; END=$(date +%s)
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
