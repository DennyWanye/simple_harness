#!/bin/bash
# slice_pipeline.sh - one SDK slice: implement -> gate -> (verify -> disposition -> re-review) x rounds -> full gate.
# Usage: slice_pipeline.sh <slice-name> <sdk-worktree> <base-commit> <impl-task.md> <verify-task.md>
#        [--lane codex|grok] [--verify-lane codex|grok] [--tests "..."] [--allow <file>]
#        [--max-sentinel N] [--max-rounds N] [--log <file>]
# The pipeline never merges and never pushes. All artifacts live under
#   ${AGENT_TASK_OUT:-$HOME/.cache/simpleharness-agent-tasks}/pipeline/<slice-name>/
# Rounds: round 1 is the first verify (the given verify task book, NEW session).
# A 修后可合 verdict on round k feeds a disposition book to the implementer session;
# the next round re-reviews with the same verifier session against a generated
# re-review book whose report is appended to the previous round's report. Any round
# that yields 可合 leaves the loop for the full gate; rounds exhausted is red.
set -u

# =========================================================================
# Source-safe pure helpers (unit-tested by selftest_gate.sh via
# SLICE_PIPELINE_SOURCE_ONLY=1). They must stay free of side effects.
# =========================================================================
# grep_verdict <summary-file> <task-output-dir>; echoes the verdict line.
# Priority: <task-output-dir>/last.txt (the runner's final message) -> the summary
# -> out.json. The alternation lists 不可合 / 修后可合 before 可合 so a longer
# verdict is never mis-read as the 可合 substring.
grep_verdict() {
  local summary="${1:-}" tout="${2:-}" line=""
  local re='VERDICT:[[:space:]]*(不可合|修后可合|可合)'
  if [ -n "$tout" ] && [ -f "$tout/last.txt" ]; then
    line=$(grep -aE "$re" "$tout/last.txt" 2>/dev/null | tail -1)
    [ -n "$line" ] && { printf '%s\n' "$line"; return 0; }
  fi
  if [ -n "$summary" ] && [ -f "$summary" ]; then
    line=$(grep -aE "$re" "$summary" 2>/dev/null | tail -1)
    [ -n "$line" ] && { printf '%s\n' "$line"; return 0; }
  fi
  if [ -n "$tout" ] && [ -f "$tout/out.json" ]; then
    line=$(grep -aoE "$re" "$tout/out.json" 2>/dev/null | tail -1)
    [ -n "$line" ] && { printf '%s\n' "$line"; return 0; }
  fi
  return 1
}

verdict_kind() {
  # verdict_kind "VERDICT: 修后可合" -> 修后可合  ("" when there is no verdict)
  printf '%s\n' "${1:-}" | grep -aoE '(不可合|修后可合|可合)' | tail -1
}

round_action() {
  # round_action <verdict-kind> <round> <max-rounds>
  # -> green | disposition | red-rounds | red-unmergeable | red-no-verdict
  local kind="${1:-}" round="${2:-1}" maxr="${3:-2}"
  case "$kind" in
    可合) printf 'green\n' ;;
    修后可合)
      if [ "$round" -ge "$maxr" ]; then printf 'red-rounds\n'; else printf 'disposition\n'; fi ;;
    不可合) printf 'red-unmergeable\n' ;;
    *) printf 'red-no-verdict\n' ;;
  esac
}

# Sourced only for the unit tests above: stop before the pipeline body runs.
if [ "${SLICE_PIPELINE_SOURCE_ONLY:-0}" = "1" ]; then
  return 0 2>/dev/null || exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

NAME="${1:-}"; SDK="${2:-}"; BASE="${3:-}"; IMPL_TASK="${4:-}"; VERIFY_TASK="${5:-}"
shift 5 2>/dev/null || true
LANE="codex"; VERIFY_LANE="codex"; TESTS=""; ALLOW=""; MAX_SENTINEL=""; MAX_ROUNDS="2"; LOGFILE=""; RESUME_IMPL=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --lane) LANE="${2:-}"; shift 2 ;;
    --verify-lane) VERIFY_LANE="${2:-}"; shift 2 ;;
    --tests) TESTS="${2:-}"; shift 2 ;;
    --allow) ALLOW="${2:-}"; shift 2 ;;
    --max-sentinel) MAX_SENTINEL="${2:-}"; shift 2 ;;
    --max-rounds) MAX_ROUNDS="${2:-}"; shift 2 ;;
    --resume-impl) RESUME_IMPL="${2:-}"; shift 2 ;;
    --log) LOGFILE="${2:-}"; shift 2 ;;
    *) echo "slice_pipeline.sh: unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [ -z "$NAME" ] || [ -z "$SDK" ] || [ -z "$BASE" ] || [ -z "$IMPL_TASK" ] || [ -z "$VERIFY_TASK" ]; then
  echo "usage: slice_pipeline.sh <slice-name> <sdk-worktree> <base-commit> <impl-task.md> <verify-task.md> [--lane codex|grok] [--verify-lane codex|grok] [--tests \"...\"] [--allow <file>] [--max-sentinel N] [--max-rounds N] [--log <file>]" >&2
  exit 1
fi
case "$MAX_ROUNDS" in
  ''|*[!0-9]*) echo "slice_pipeline.sh: --max-rounds must be a positive integer: $MAX_ROUNDS" >&2; exit 1 ;;
esac
if [ "$MAX_ROUNDS" -lt 1 ]; then
  echo "slice_pipeline.sh: --max-rounds must be >= 1: $MAX_ROUNDS" >&2
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
# Verification reports are written (uncommitted) inside the detached verify worktree; save them before the
# worktree is removed so they can be archived with the slice.
save_verify_artifacts() {
  local dest="$PIPE_DIR/verify-artifacts" list="$PIPE_DIR/verify-report-paths.txt" f
  mkdir -p "$dest"
  # The relative-path list lets a later round copy the saved reports back into
  # the moved verify copy, so the verifier keeps appending to the same file.
  : > "$list"
  git -C "$VERIFY_DIR" ls-files --others --exclude-standard 2>/dev/null | while IFS= read -r f; do
    case "$f" in
      *.md|*.json|*.txt)
        mkdir -p "$dest/$(dirname "$f")"
        cp "$VERIFY_DIR/$f" "$dest/$f"
        printf '%s\n' "$f" >> "$list"
        ;;
    esac
  done
  logline "verify-artifacts" "saved to $dest"
}

# restore_verify_reports <round>: copy the reports saved by the previous round back
# into the (re-added) verify copy at the very same relative paths.
restore_verify_reports() {
  local round="${1:-?}" list="$PIPE_DIR/verify-report-paths.txt" src="$PIPE_DIR/verify-artifacts" f n=0
  if [ -f "$list" ]; then
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      [ -f "$src/$f" ] || continue
      mkdir -p "$VERIFY_DIR/$(dirname "$f")"
      cp "$src/$f" "$VERIFY_DIR/$f"
      n=$((n + 1))
    done < "$list"
  fi
  logline "d-restore-$round" "restored=$n report(s) from $src"
}

# write_review_book <round> <prev-head> <new-head> <report-paths-file>; echoes the book path.
write_review_book() {
  local round="$1" prev="$2" new="$3" paths="$4" book="$PIPE_DIR/review-$1.md"
  {
    echo "# 复核任务书（第 ${round} 轮复核）"
    echo
    echo "同一核验会话续做（不要另开新会话）。"
    echo
    echo "上一轮核验副本 HEAD：$prev"
    echo "当前核验副本 HEAD：$new"
    echo "核验副本目录：$VERIFY_DIR"
    echo
    echo "要求："
    echo "1. 只需看上一轮 HEAD（${prev}）到当前 HEAD（${new}）之间的改动。"
    echo "2. 逐个重做上一轮报告里记录的存活变异，确认都已被杀死。"
    echo "3. 再补做若干新变异。"
    echo "4. 结果追加到原报告的「## 复核 ${round}」小节（原报告已拷回副本的同一路径，不要新建报告）。"
    echo "5. 判定规则与 VERDICT 行格式不变：最终回复里写一行 VERDICT: 可合 / VERDICT: 修后可合 / VERDICT: 不可合。"
    echo
    echo "请只在核验副本目录内工作，不要 merge、不要 push。"
  } > "$book"
  if [ -f "$paths" ]; then
    {
      echo
      echo "上一轮保存的核验报告相对路径（已拷回副本内同一路径）："
      while IFS= read -r f; do
        [ -n "$f" ] && printf -- '- %s\n' "$f"
      done < "$paths"
    } >> "$book"
  fi
  echo "$book"
}
if [ -n "$RESUME_IMPL" ]; then
  # Implementation already committed by an earlier run: skip step a and continue from the gate.
  SESSION="$RESUME_IMPL"
  logline "a-implement" "skipped (resume-impl session=$SESSION)"
else
  logline "a-implement" "start lane=$LANE"
  IMPL_SUM=$(run_lane "$LANE" "${NAME}-impl-1" "$SDK" "$IMPL_TASK" "")
  SESSION=$(extract_session "$IMPL_SUM")
  logline "a-implement" "done session=${SESSION:-?} summary=$IMPL_SUM"
fi

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
# =========================================================================
# c. independent verify in a detached copy; rounds of verify -> disposition -> re-review
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
  logline "c-verify-1" "FAIL path-exists $VERIFY_DIR"
  exit 1
fi
if ! git -C "$SDK" worktree add --detach "$VERIFY_DIR" "$HEAD_NOW" >> "$LOGFILE" 2>&1; then
  logline "c-verify-1" "FAIL worktree-add $VERIFY_DIR"
  echo "PIPELINE RED verify $NAME (could not create verify worktree)"
  exit 3
fi
logline "c-verify-1" "start worktree=$VERIFY_DIR head=$HEAD_NOW"

# Round 1 verifies with the given task book in a NEW session. A 修后可合 verdict feeds a
# disposition book to the implementer session; the next round re-reviews in the SAME
# verifier session with a generated re-review book. Step names carry the round number.
VSESSION=""
ROUND=1
REVIEWED_HEAD="$HEAD_NOW"
VERIFIED=0
while [ "$ROUND" -le "$MAX_ROUNDS" ]; do
  if [ "$ROUND" -eq 1 ]; then
    VBOOK="$VERIFY_TASK"
    RESUME_ARG=""
  else
    VBOOK=$(write_review_book "$ROUND" "$REVIEWED_HEAD" "$HEAD_NOW" "$PIPE_DIR/verify-report-paths.txt")
    RESUME_ARG="$VSESSION"
  fi
  logline "c-verify-$ROUND" "start book=$VBOOK resume=${RESUME_ARG:-none}"
  VERIFY_SUM=$(run_lane "$VERIFY_LANE" "${NAME}-verify-$ROUND" "$VERIFY_DIR" "$VBOOK" "$RESUME_ARG")
  VSESSION=$(extract_session "$VERIFY_SUM")
  VLINE=$(grep_verdict "$VERIFY_SUM" "$TASKS_DIR/${NAME}-verify-$ROUND" || true)
  if [ -z "$VLINE" ]; then
    logline "c-verify-$ROUND" "RED no-verdict summary=$VERIFY_SUM"
    logline "result" "PIPELINE RED verify"
    echo "PIPELINE RED verify $NAME (no VERDICT line in round $ROUND; report: $VERIFY_SUM)"
    exit 3
  fi
  VKIND=$(verdict_kind "$VLINE")
  logline "c-verify-$ROUND" "done verdict=$VKIND session=${VSESSION:-?} summary=$VERIFY_SUM"

  VACTION=$(round_action "$VKIND" "$ROUND" "$MAX_ROUNDS")
  case "$VACTION" in
    green)
      VERIFIED=1
      logline "c-verify-$ROUND" "可合 -> full gate"
      break
      ;;
    red-rounds)
      logline "d-result" "RED rounds-exhausted round=$ROUND/$MAX_ROUNDS verdict=$VKIND"
      logline "result" "PIPELINE RED verify"
      echo "PIPELINE RED verify $NAME (rounds exhausted $ROUND/$MAX_ROUNDS; last: $VLINE; report: $VERIFY_SUM)"
      exit 3
      ;;
    red-unmergeable)
      logline "d-disposition-$ROUND" "RED 不可合 $VLINE"
      logline "result" "PIPELINE RED verify"
      echo "PIPELINE RED verify $NAME ($VLINE; report: $VERIFY_SUM)"
      exit 3
      ;;
    red-no-verdict)
      logline "c-verify-$ROUND" "RED no-verdict summary=$VERIFY_SUM"
      logline "result" "PIPELINE RED verify"
      echo "PIPELINE RED verify $NAME (no VERDICT line in round $ROUND; report: $VERIFY_SUM)"
      exit 3
      ;;
  esac

  # ---- disposition of round $ROUND (修后可合) -------------------------------------
  DISPBOOK="$PIPE_DIR/disposition-$ROUND.md"
  {
    echo "# 处置任务书（核验：修后可合）"
    echo
    echo "这是第 $ROUND 轮处置。"
    echo
    echo "核验报告：$VERIFY_SUM"
    echo "核验结论：$VLINE"
    echo "核验副本目录（未跟踪的 .md 即完整核验报告，先读它）：$VERIFY_DIR"
    echo
    echo "请按核验报告修复问题，使 sdk_gate.sh 全绿后提交（工作树须保持 clean）。"
    echo "不要 merge、不要 push；只在工作目录内改动。"
  } > "$DISPBOOK"
  logline "d-disposition-$ROUND" "start resume=${SESSION:-none}"
  DISP_SUM=$(run_lane "$LANE" "${NAME}-impl-disposition-$ROUND" "$SDK" "$DISPBOOK" "$SESSION")
  SESSION=$(extract_session "$DISP_SUM")
  logline "d-disposition-$ROUND" "done session=${SESSION:-?} summary=$DISP_SUM"

  GATEN="$PIPE_DIR/gate-$((ROUND + 2)).json"
  if ! gate "$GATEN"; then
    BADN=$(failed_items "$GATEN")
    logline "d-gate-$ROUND" "RED $GATEN failed=$BADN"
    logline "result" "PIPELINE RED gate"
    echo "PIPELINE RED gate $NAME (round $ROUND; report: $GATEN failed: $BADN)"
    exit 2
  fi
  logline "d-gate-$ROUND" "GREEN $GATEN"

  # move the detached copy onto the new commit (remove + re-add, avoids checkout/reset)
  REVIEWED_HEAD="$HEAD_NOW"
  NEW_HEAD=$(git -C "$SDK" rev-parse HEAD)
  save_verify_artifacts
  git -C "$SDK" worktree remove --force "$VERIFY_DIR" >> "$LOGFILE" 2>&1 || true
  if ! git -C "$SDK" worktree add --detach "$VERIFY_DIR" "$NEW_HEAD" >> "$LOGFILE" 2>&1; then
    logline "d-move-$ROUND" "FAIL worktree-add $VERIFY_DIR"
    echo "PIPELINE RED verify $NAME (could not move verify worktree)"
    exit 3
  fi
  logline "d-move-$ROUND" "moved worktree=$VERIFY_DIR head=$NEW_HEAD"
  # put the saved report(s) back at the very same path so the verifier appends in place
  restore_verify_reports "$ROUND"
  HEAD_NOW="$NEW_HEAD"
  ROUND=$((ROUND + 1))
done

if [ "$VERIFIED" -ne 1 ]; then
  logline "result" "PIPELINE RED verify"
  echo "PIPELINE RED verify $NAME (no 可合 verdict within $MAX_ROUNDS round(s))"
  exit 3
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

save_verify_artifacts
git -C "$SDK" worktree remove --force "$VERIFY_DIR" >> "$LOGFILE" 2>&1 || true
HEAD_SHORT=$(git -C "$SDK" rev-parse --short HEAD)
logline "result" "PIPELINE GREEN $NAME head=$HEAD_SHORT"
echo "PIPELINE GREEN $NAME head=$HEAD_SHORT"
exit 0
