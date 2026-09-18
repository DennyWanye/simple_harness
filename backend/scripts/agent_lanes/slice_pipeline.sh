#!/bin/bash
# slice_pipeline.sh - one slice: implement -> gate -> independent verify -> disposition -> full gate.
# Usage: slice_pipeline.sh <slice-name> <sdk-worktree> <base-commit> <impl-task.md> <verify-task.md>
#        [--lane codex|grok] [--verify-lane codex|grok] [--tests "..."] [--allow <file>]
#        [--max-sentinel N] [--log <file>]
# The pipeline never merges and never pushes. All artifacts live under
#   ${AGENT_TASK_OUT:-$HOME/.cache/simpleharness-agent-tasks}/pipeline/<slice-name>/
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

NAME="${1:-}"; SDK="${2:-}"; BASE="${3:-}"; IMPL_TASK="${4:-}"; VERIFY_TASK="${5:-}"
shift 5 2>/dev/null || true
LANE="codex"; VERIFY_LANE="codex"; TESTS=""; ALLOW=""; MAX_SENTINEL=""; LOGFILE=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --lane) LANE="${2:-}"; shift 2 ;;
    --verify-lane) VERIFY_LANE="${2:-}"; shift 2 ;;
    --tests) TESTS="${2:-}"; shift 2 ;;
    --allow) ALLOW="${2:-}"; shift 2 ;;
    --max-sentinel) MAX_SENTINEL="${2:-}"; shift 2 ;;
    --log) LOGFILE="${2:-}"; shift 2 ;;
    *) echo "slice_pipeline.sh: unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [ -z "$NAME" ] || [ -z "$SDK" ] || [ -z "$BASE" ] || [ -z "$IMPL_TASK" ] || [ -z "$VERIFY_TASK" ]; then
  echo "usage: slice_pipeline.sh <slice-name> <sdk-worktree> <base-commit> <impl-task.md> <verify-task.md> [--lane codex|grok] [--verify-lane codex|grok] [--tests \"...\"] [--allow <file>] [--max-sentinel N] [--log <file>]" >&2
  exit 1
fi
[ -d "$SDK" ] || { echo "slice_pipeline.sh: sdk worktree not found: $SDK" >&2; exit 1; }
[ -f "$IMPL_TASK" ] || { echo "slice_pipeline.sh: impl task book not found: $IMPL_TASK" >&2; exit 1; }
[ -f "$VERIFY_TASK" ] || { echo "slice_pipeline.sh: verify task book not found: $VERIFY_TASK" >&2; exit 1; }

BASE_DIR="${AGENT_TASK_OUT:-$HOME/.cache/simpleharness-agent-tasks}"
PIPE_DIR="$BASE_DIR/pipeline/$NAME"
TASKS_DIR="$PIPE_DIR/tasks"
mkdir -p "$PIPE_DIR" "$TASKS_DIR"
[ -n "$LOGFILE" ] || LOGFILE="$PIPE_DIR/pipeline.log"
: > "$LOGFILE" 2>/dev/null || true

logline() {
  # logline <step> <result>
  local line
  line="=== [$(date '+%Y-%m-%d %H:%M:%S')] $NAME $1 $2"
  printf '%s\n' "$line" | tee -a "$LOGFILE"
}

lane_script() {
  case "$1" in
    codex) echo "$SCRIPT_DIR/codex_task.sh" ;;
    grok) echo "$SCRIPT_DIR/grok_task.sh" ;;
    *) echo "" ;;
  esac
}

# run_lane <lane> <taskname> <cwd> <prompt-file> <resume-session-or-empty>
# Captures the runner summary to $PIPE_DIR/<taskname>.summary.txt; echoes that path.
run_lane() {
  local lane="$1" tname="$2" cwd="$3" prompt="$4" resume="${5:-}"
  local script summary
  script="$(lane_script "$lane")"
  if [ -z "$script" ]; then echo "run_lane: unknown lane $lane" >&2; return 9; fi
  summary="$PIPE_DIR/$tname.summary.txt"
  if [ "$lane" = "grok" ]; then
    AGENT_TASK_OUT="$TASKS_DIR" "$script" "$tname" "$cwd" "$prompt" "high" "80" "$resume" > "$summary" 2>&1
  else
    AGENT_TASK_OUT="$TASKS_DIR" "$script" "$tname" "$cwd" "$prompt" "high" "workspace-write" "$resume" > "$summary" 2>&1
  fi
  echo "$summary"
}

extract_session() {
  # extract_session <summary-file>
  grep -oE 'session=[^ ]+' "$1" 2>/dev/null | tail -1 | cut -d= -f2
}

# grep_verdict <summary-file> <task-output-dir>; echoes the verdict line (first match).
grep_verdict() {
  local summary="$1" tout="$2" line=""
  line=$(grep -aE 'VERDICT:[[:space:]]*(可合|修后可合|不可合)' "$summary" 2>/dev/null | tail -1)
  [ -n "$line" ] && { echo "$line"; return 0; }
  if [ -n "$tout" ] && [ -f "$tout/last.txt" ]; then
    line=$(grep -aE 'VERDICT:[[:space:]]*(可合|修后可合|不可合)' "$tout/last.txt" 2>/dev/null | tail -1)
    [ -n "$line" ] && { echo "$line"; return 0; }
  fi
  if [ -n "$tout" ] && [ -f "$tout/out.json" ]; then
    line=$(grep -aoE 'VERDICT:[[:space:]]*(可合|修后可合|不可合)' "$tout/out.json" 2>/dev/null | tail -1)
    [ -n "$line" ] && { echo "$line"; return 0; }
  fi
  return 1
}

verdict_kind() {
  # verdict_kind "VERDICT: 修后可合" -> 修后可合
  printf '%s\n' "$1" | grep -aoE '(可合|修后可合|不可合)' | tail -1
}

gate() {
  # gate <out-json> [full]
  local out="$1" extra="${2:-}"
  GATE_ARGS=("$SDK" "$BASE" --out "$out")
  [ -n "$TESTS" ] && GATE_ARGS+=(--tests "$TESTS")
  [ -n "$ALLOW" ] && GATE_ARGS+=(--allow "$ALLOW")
  [ -n "$MAX_SENTINEL" ] && GATE_ARGS+=(--max-sentinel "$MAX_SENTINEL")
  [ -n "$extra" ] && GATE_ARGS+=("$extra")
  "$SCRIPT_DIR/sdk_gate.sh" "${GATE_ARGS[@]}" >> "$LOGFILE" 2>&1
}

failed_items() {
  python3 -c "import json,sys;d=json.load(open(sys.argv[1]));print(','.join(i['name'] for i in d['items'] if not i['ok']) or '-')" "$1" 2>/dev/null || echo "?"
}

# =========================================================================
# a. implement (lane, default codex) - capture session
# =========================================================================
logline "a-implement" "start lane=$LANE"
IMPL_SUM=$(run_lane "$LANE" "${NAME}-impl-1" "$SDK" "$IMPL_TASK" "")
SESSION=$(extract_session "$IMPL_SUM")
logline "a-implement" "done session=${SESSION:-?} summary=$IMPL_SUM"

# =========================================================================
# b. gate (no --full); red -> fix book, resume impl, gate again; still red -> exit 2
# =========================================================================
GATE1="$PIPE_DIR/gate-1.json"
if gate "$GATE1"; then
  logline "b-gate-1" "GREEN $GATE1"
else
  BAD1=$(failed_items "$GATE1")
  logline "b-gate-1" "RED $GATE1 failed=$BAD1"
  FIXBOOK="$PIPE_DIR/fix-1.md"
  {
    echo "# 修复任务书（闸门红）"
    echo
    echo "闸门报告：$GATE1"
    echo "失败项：$BAD1"
    echo
    echo "请修复上述失败项，使 sdk_gate.sh 全绿后提交（工作树须保持 clean）。"
    echo "不要 merge、不要 push；只在工作目录内改动。"
  } > "$FIXBOOK"
  logline "b-fix" "start resume=${SESSION:-none}"
  FIX_SUM=$(run_lane "$LANE" "${NAME}-impl-fix-1" "$SDK" "$FIXBOOK" "$SESSION")
  SESSION=$(extract_session "$FIX_SUM")
  logline "b-fix" "done session=${SESSION:-?} summary=$FIX_SUM"
  GATE2="$PIPE_DIR/gate-2.json"
  if ! gate "$GATE2"; then
    BAD2=$(failed_items "$GATE2")
    logline "b-gate-2" "RED $GATE2 failed=$BAD2"
    logline "result" "PIPELINE RED gate"
    echo "PIPELINE RED gate $NAME (report: $GATE2 failed: $BAD2)"
    exit 2
  fi
  logline "b-gate-2" "GREEN $GATE2"
fi

# =========================================================================
# c. independent verify in a detached worktree copy (new session)
# =========================================================================
VERIFY_DIR="${SDK}-verify-${NAME}"
HEAD_NOW=$(git -C "$SDK" rev-parse HEAD)
# make the path available (idempotent): drop a stale registered worktree at that path
git -C "$SDK" worktree prune 2>/dev/null || true
if git -C "$SDK" worktree list --porcelain 2>/dev/null | grep -qx "worktree $VERIFY_DIR"; then
  git -C "$SDK" worktree remove --force "$VERIFY_DIR" 2>/dev/null || true
fi
if [ -e "$VERIFY_DIR" ]; then
  echo "slice_pipeline.sh: verify path already exists and is not a registered worktree: $VERIFY_DIR" >&2
  logline "c-verify" "FAIL path-exists $VERIFY_DIR"
  exit 1
fi
if ! git -C "$SDK" worktree add --detach "$VERIFY_DIR" "$HEAD_NOW" >> "$LOGFILE" 2>&1; then
  logline "c-verify" "FAIL worktree-add $VERIFY_DIR"
  echo "PIPELINE RED verify $NAME (could not create verify worktree)"
  exit 3
fi
logline "c-verify" "start worktree=$VERIFY_DIR head=$HEAD_NOW"
VERIFY_SUM=$(run_lane "$VERIFY_LANE" "${NAME}-verify-1" "$VERIFY_DIR" "$VERIFY_TASK" "")
VSESSION=$(extract_session "$VERIFY_SUM")
VLINE=$(grep_verdict "$VERIFY_SUM" "$TASKS_DIR/${NAME}-verify-1" || true)
if [ -z "$VLINE" ]; then
  logline "c-verify" "RED no-verdict summary=$VERIFY_SUM"
  logline "result" "PIPELINE RED verify"
  echo "PIPELINE RED verify $NAME (no VERDICT line; report: $VERIFY_SUM)"
  exit 3
fi
VKIND=$(verdict_kind "$VLINE")
logline "c-verify" "done verdict=$VKIND session=${VSESSION:-?} summary=$VERIFY_SUM"

# =========================================================================
# d. disposition
# =========================================================================
if [ "$VKIND" = "不可合" ]; then
  logline "d-disposition" "RED 不可合 $VLINE"
  logline "result" "PIPELINE RED verify"
  echo "PIPELINE RED verify $NAME ($VLINE; report: $VERIFY_SUM)"
  exit 3
fi

if [ "$VKIND" = "修后可合" ]; then
  DISPBOOK="$PIPE_DIR/disposition-1.md"
  {
    echo "# 处置任务书（核验：修后可合）"
    echo
    echo "核验报告：$VERIFY_SUM"
    echo "核验结论：$VLINE"
    echo
    echo "请按核验报告修复问题，使 sdk_gate.sh 全绿后提交（工作树须保持 clean）。"
    echo "不要 merge、不要 push；只在工作目录内改动。"
  } > "$DISPBOOK"
  logline "d-disposition" "start resume=${SESSION:-none}"
  DISP_SUM=$(run_lane "$LANE" "${NAME}-impl-disposition-1" "$SDK" "$DISPBOOK" "$SESSION")
  SESSION=$(extract_session "$DISP_SUM")
  logline "d-disposition" "done session=${SESSION:-?} summary=$DISP_SUM"

  GATE3="$PIPE_DIR/gate-3.json"
  if ! gate "$GATE3"; then
    BAD3=$(failed_items "$GATE3")
    logline "d-gate-3" "RED $GATE3 failed=$BAD3"
    logline "result" "PIPELINE RED gate"
    echo "PIPELINE RED gate $NAME (report: $GATE3 failed: $BAD3)"
    exit 2
  fi
  logline "d-gate-3" "GREEN $GATE3"

  # move the detached copy onto the new commit (remove + re-add, avoids checkout/reset)
  NEW_HEAD=$(git -C "$SDK" rev-parse HEAD)
  git -C "$SDK" worktree remove --force "$VERIFY_DIR" >> "$LOGFILE" 2>&1 || true
  if ! git -C "$SDK" worktree add --detach "$VERIFY_DIR" "$NEW_HEAD" >> "$LOGFILE" 2>&1; then
    logline "d-reverify" "FAIL worktree-add $VERIFY_DIR"
    echo "PIPELINE RED verify $NAME (could not move verify worktree)"
    exit 3
  fi
  logline "d-reverify" "start worktree=$VERIFY_DIR head=$NEW_HEAD resume=${VSESSION:-none}"
  REVERIFY_SUM=$(run_lane "$VERIFY_LANE" "${NAME}-verify-2" "$VERIFY_DIR" "$VERIFY_TASK" "$VSESSION")
  RVLINE=$(grep_verdict "$REVERIFY_SUM" "$TASKS_DIR/${NAME}-verify-2" || true)
  if [ -z "$RVLINE" ]; then
    logline "d-reverify" "RED no-verdict summary=$REVERIFY_SUM"
    logline "result" "PIPELINE RED verify"
    echo "PIPELINE RED verify $NAME (no VERDICT line on re-review; report: $REVERIFY_SUM)"
    exit 3
  fi
  RVKIND=$(verdict_kind "$RVLINE")
  logline "d-reverify" "done verdict=$RVKIND summary=$REVERIFY_SUM"
  if [ "$RVKIND" != "可合" ]; then
    logline "d-result" "RED re-verify=$RVKIND"
    logline "result" "PIPELINE RED verify"
    echo "PIPELINE RED verify $NAME (re-review: $RVLINE; report: $REVERIFY_SUM)"
    exit 3
  fi
fi

# =========================================================================
# e. full gate -> GREEN
# =========================================================================
GATE_FULL="$PIPE_DIR/gate-full.json"
if ! gate "$GATE_FULL" "--full"; then
  BADF=$(failed_items "$GATE_FULL")
  logline "e-gate-full" "RED $GATE_FULL failed=$BADF"
  logline "result" "PIPELINE RED gate (full)"
  echo "PIPELINE RED gate $NAME (full; report: $GATE_FULL failed: $BADF)"
  exit 2
fi
logline "e-gate-full" "GREEN $GATE_FULL"

git -C "$SDK" worktree remove --force "$VERIFY_DIR" >> "$LOGFILE" 2>&1 || true
HEAD_SHORT=$(git -C "$SDK" rev-parse --short HEAD)
logline "result" "PIPELINE GREEN $NAME head=$HEAD_SHORT"
echo "PIPELINE GREEN $NAME head=$HEAD_SHORT"
exit 0
