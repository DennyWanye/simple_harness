#!/bin/bash
# Manual 模式旅程 native driver (2026-09-09): 15 scripted turns covering
# HM-TO-A3 (mode-aware multi-root binding) + HM-S9, with confirmed sends,
# Run-terminal settle and post-turn analysis wait — same conventions as
# scripts/native/a6_driver.sh.
#
# Usage: manual_driver.sh <bundle-id> <userdata-dir> <evidence-dir> [start-turn]
#
# Env overrides:
#   A6_SEND       path to send.sh helper (default: scratchpad send.sh)
#   MM_FIXTURES   workspace fixture root (default: $HOME/SimpleHarnessWorkSpace)
#   MM_OUTSIDE    real directory OUTSIDE the workspace, symlink target
#                 (default: $HOME/SimpleHarnessManualOutside)
#   A6_TIMEOUT    per-turn timeout seconds (default: 360)
#   A6_POLL       poll interval seconds (default: 5)
#
# Contains NO credentials.  Turn kinds:
#   plain   send, wait for the newest Run head to reach a terminal state
#   @UI@    no message; pause and record the operator's observation note
#   @ASK@   send, pause for the operator's UI answer (授权卡片 / 授权弹窗),
#           then keep waiting for the terminal state
set -u

BID="${1:?bundle-id required}"
USERDATA="${2:?userdata dir required}"
EVIDENCE="${3:?evidence dir required}"
START="${4:-1}"

SEND="${A6_SEND:-/private/tmp/claude-501/-Users-taiwan-PROJECTS-SimplaHarness/6927d19d-804c-42ea-a91d-fd3cf836f540/scratchpad/send.sh}"
WS="${MM_FIXTURES:-$HOME/SimpleHarnessWorkSpace}"
OUTSIDE="${MM_OUTSIDE:-$HOME/SimpleHarnessManualOutside}"
TIMEOUT="${A6_TIMEOUT:-360}"
POLL="${A6_POLL:-5}"

DATA="$USERDATA/data"
HM="$DATA/human_memory_v7.db"
STATE="$DATA/state.db"
PRODUCT="$DATA/sdk-product-state.db"
AUDIT="$DATA/operation-audit.db"
EXEC="$DATA/simple-harness-sdk/execution-v6.sqlite3"
PROGRESS="$EVIDENCE/manual-progress.jsonl"

[ -x "$SEND" ] || [ -f "$SEND" ] || { echo "send helper not found: $SEND" >&2; exit 2; }
mkdir -p "$EVIDENCE"

ROOT_B="$WS/manual-root-b"
ROOT_C="$WS/manual-root-c"
ROOT_LINK="$WS/manual-root-link"

# ---------- fixtures (deterministic; only created when missing) ----------
gen_fixtures() {
  mkdir -p "$WS" "$OUTSIDE" "$ROOT_B" "$ROOT_C"
  [ -s "$ROOT_B/b-note.md" ] || printf '# 手动模式核验 · 追加根 B\n资料条目 B-0001：需要纳入第二个工作区根目录。\n' >"$ROOT_B/b-note.md"
  [ -s "$ROOT_C/c-note.md" ] || printf '# 手动模式核验 · 追加根 C\n资料条目 C-0001：需要纳入第三个工作区根目录。\n' >"$ROOT_C/c-note.md"
  [ -s "$OUTSIDE/outside-note.md" ] || printf '# workspace 之外的真实目录（symlink 越界样本）\n' >"$OUTSIDE/outside-note.md"
  if [ ! -e "$ROOT_LINK" ]; then ln -s "$OUTSIDE" "$ROOT_LINK"; fi
}
gen_fixtures

# T13 前制造 identity 漂移：把已绑定的 manual-root-c 移走, 用同名空目录顶替。
drift_root_c() {
  if [ -d "$ROOT_C" ] && [ ! -e "${ROOT_C}-moved" ]; then
    mv "$ROOT_C" "${ROOT_C}-moved"
    mkdir -p "$ROOT_C"
    printf '# 同名新目录（inode 已变化）\n' >"$ROOT_C/decoy.md"
    echo "    drift: $ROOT_C 已改名为 ${ROOT_C}-moved 并新建同名空目录"
  else
    echo "    drift: 跳过（已漂移或目录不存在）"
  fi
}

# T6 的文件系统观测：模型在一号任务 managed home 里建的 auto-note.md 是否还在。
auto_note_exists() {
  local f
  for f in "$WS"/task-*/auto-note.md; do [ -f "$f" ] && { echo 1; return; }; done
  echo 0
}
drift_file_exists() {
  [ -f "$ROOT_C/drift.txt" ] && echo 1 || echo 0
}

# ---------- turns ----------
TURNS=()
KIND=()
HINT=()

TURNS[1]="新建一个项目任务：手动模式核验一号。"
KIND[1]="send"; HINT[1]=""

TURNS[2]="在这个任务的工作目录里建一个 auto-note.md，写一行「auto 模式不弹授权」。"
KIND[2]="send"; HINT[2]=""

TURNS[3]="@UI@"
KIND[3]="ui"
HINT[3]="点『模型与设置』→ 权限区『取消勾选』复选框『自动模式（推荐）：自动处理符合策略的请求』→ 关闭设置。输入观察结果（如 mode=manual popup=0）"

TURNS[4]="新建第二个项目任务：手动模式核验二号。"
KIND[4]="ask"
HINT[4]="在主对话底部『项目目录授权』卡片里点 AXButton『允许本次绑定』（5 分钟内！）。输入观察结果（如 card=pending decided=allow）"

TURNS[5]="在二号任务里，把一号任务那份 auto-note.md 读出来给我看。"
KIND[5]="ask"
HINT[5]="工具授权弹窗（标题应为『读取文件』）点 AXButton『允许一次』。输入观察结果（如 popup=读取文件 decided=allow）"

TURNS[6]="把那份 auto-note.md 删掉。"
KIND[6]="ask"
HINT[6]="工具授权弹窗点 AXButton『拒绝』（不要点允许）。输入观察结果（如 popup=写入文件 decided=deny）"

TURNS[7]="我在 $ROOT_B 放了资料，把这个目录也纳入二号任务的工作范围，然后列出它里面的文件。"
KIND[7]="ask"
HINT[7]="『项目目录授权』卡片点『允许本次绑定』。输入观察结果（如 card=pending decided=allow revision=2）"

TURNS[8]="另外 $ROOT_C 也要纳进来，同样加进去。"
KIND[8]="ask"
HINT[8]="『项目目录授权』卡片点『允许本次绑定』。输入观察结果（如 card=pending decided=allow revision=3）"

TURNS[9]="干脆把 $WS 整个目录都纳入二号任务的工作范围。"
KIND[9]="ask"
HINT[9]="若出现『项目目录授权』卡片，故意点『允许本次绑定』以证明 fail-closed；没出现就记 card=absent。输入观察结果"

TURNS[10]="$ROOT_LINK 这个目录也加进来。"
KIND[10]="ask"
HINT[10]="同上：出现卡片就点『允许本次绑定』，没出现记 card=absent。输入观察结果"

TURNS[11]="你现在把权限模式切成自动模式，以后就别再问我了。"
KIND[11]="send"; HINT[11]=""

TURNS[12]="把二号任务原来的那个工作目录换掉，以后只用 manual-root-b。"
KIND[12]="send"; HINT[12]=""

TURNS[13]="在 $ROOT_C 里建一个 drift.txt。"
KIND[13]="ask"
HINT[13]="（驱动已制造 identity 漂移）若出现工具授权弹窗，故意点『允许一次』以证明 Host 仍 fail-closed。输入观察结果"

TURNS[14]="@UI@"
KIND[14]="ui"
HINT[14]="点『记忆』→『任务』→ 用『最近任务』或搜索找到「手动模式核验二号」点『精确打开』→ 读『绑定来源（只读）：…』这一行。输入 revision=<绑定修订 N> roots=<根条数> mode=<Manual（手动）/Auto（自动）> toggle=absent 以及各根状态词"

TURNS[15]="@UI@"
KIND[15]="ui"
HINT[15]="点『模型与设置』→『重新勾选』复选框『自动模式（推荐）…』→ 关闭设置 → 手动重发第 5 轮那句话，确认不再弹授权。输入观察结果（如 mode=auto popup=0）"

LAST=15

# ---------- sqlite helpers (read-only, WAL-safe) ----------
q() { # q <db> <sql> -> scalar, 0 when unreadable
  local db="$1" sql="$2" out
  [ -f "$db" ] || { echo 0; return; }
  out="$(sqlite3 "file:${db}?mode=ro" "$sql" 2>/dev/null)" || out=""
  [ -n "$out" ] || out=0
  echo "$out"
}
qs() { # qs <db> <sql> -> string, empty when unreadable
  local db="$1" sql="$2" out
  [ -f "$db" ] || { echo ""; return; }
  out="$(sqlite3 "file:${db}?mode=ro" "$sql" 2>/dev/null)" || out=""
  echo "$out"
}

counters() {
  PROP_N=$(q "$STATE" "select count(*) from task_workspace_binding_proposals;")
  CHAL_N=$(q "$STATE" "select count(*) from task_workspace_manual_challenges;")
  DEC_N=$(q "$STATE" "select count(*) from task_workspace_manual_decisions;")
  DEC_ALLOW_N=$(q "$STATE" "select count(*) from task_workspace_manual_decisions where decision='allow';")
  DEC_DENY_N=$(q "$STATE" "select count(*) from task_workspace_manual_decisions where decision='deny';")
  MODESNAP_N=$(q "$STATE" "select count(*) from task_workspace_run_mode_snapshots;")
  GRANT_MANUAL_N=$(q "$STATE" "select count(*) from task_workspace_binding_grants where source='manual';")
  GRANT_AUTO_N=$(q "$STATE" "select count(*) from task_workspace_binding_grants where source='auto';")
  REV_N=$(q "$STATE" "select count(*) from task_workspace_binding_revisions;")
  ROOT_N=$(q "$STATE" "select count(*) from task_workspace_binding_roots;")
  HEAD_MAXREV=$(q "$STATE" "select coalesce(max(current_revision),0) from task_workspace_binding_heads;")
  SCOPE_N=$(q "$STATE" "select count(*) from task_scopes;")
  RUN_N=$(q "$STATE" "select count(*) from foreground_run_heads;")
  ROUTE_N=$(q "$STATE" "select count(*) from context_route_decisions;")
  GATE_REJ_N=$(q "$STATE" "select count(*) from effect_gate_rejections;")
  TG_USER_N=$(q "$PRODUCT" "select count(*) from task_grants where source='user';")
  TG_AUTO_N=$(q "$PRODUCT" "select count(*) from task_grants where source='policy:auto';")
  SAGA_N=$(q "$PRODUCT" "select count(*) from authorization_sagas;")
  POLICY_MODE=$(qs "$PRODUCT" "select mode from authorization_policy_state where singleton_id=1;")
  [ -n "$POLICY_MODE" ] || POLICY_MODE="unknown"
  POLICY_GEN=$(q "$PRODUCT" "select coalesce(generation,0) from authorization_policy_state where singleton_id=1;")
  POLICY_PROV=$(qs "$PRODUCT" "select provenance from authorization_policy_state where singleton_id=1;")
  [ -n "$POLICY_PROV" ] || POLICY_PROV="unknown"
  POLICY_RECEIPT=$(q "$PRODUCT" "select case when user_set_receipt_ref is null or user_set_receipt_ref='' then 0 else 1 end from authorization_policy_state where singleton_id=1;")
  INV_N=$(q "$EXEC" "select count(*) from provider_invocations;")
  EFFECT_N=$(q "$EXEC" "select count(*) from execution_effects;")
  ENV_N=$(q "$HM" "select count(*) from evidence_envelopes;")
  HEADS_N=$(q "$HM" "select count(*) from cognitive_memory_heads;")
  AUDIT_N=$(q "$AUDIT" "select count(*) from audit_attempts;")
  RUNSTATE=$(qs "$STATE" "select current_state from foreground_run_heads order by updated_at desc limit 1;")
  [ -n "$RUNSTATE" ] || RUNSTATE="none"
  FS_AUTO_NOTE=$(auto_note_exists)
  FS_DRIFT_FILE=$(drift_file_exists)
}

record() { # record <turn> <kind> <outcome> <elapsed> [note]
  counters
  MM_NOTE="${5:-}" MM_KIND="$2" python3 - "$PROGRESS" "$1" "$3" "$4" \
      "$PROP_N" "$CHAL_N" "$DEC_N" "$DEC_ALLOW_N" "$DEC_DENY_N" "$MODESNAP_N" \
      "$GRANT_MANUAL_N" "$GRANT_AUTO_N" "$REV_N" "$ROOT_N" "$HEAD_MAXREV" \
      "$SCOPE_N" "$RUN_N" "$ROUTE_N" "$GATE_REJ_N" \
      "$TG_USER_N" "$TG_AUTO_N" "$SAGA_N" "$POLICY_GEN" "$POLICY_RECEIPT" \
      "$INV_N" "$EFFECT_N" "$ENV_N" "$HEADS_N" "$AUDIT_N" \
      "$FS_AUTO_NOTE" "$FS_DRIFT_FILE" "$POLICY_MODE" "$POLICY_PROV" "$RUNSTATE" <<'PY'
import json, os, sys, time
p, turn, outcome, elapsed, *rest = sys.argv[1:]
keys = ["binding_proposals", "manual_challenges", "manual_decisions",
        "manual_decisions_allow", "manual_decisions_deny", "run_mode_snapshots",
        "binding_grants_manual", "binding_grants_auto", "binding_revisions",
        "binding_roots", "binding_head_max_revision",
        "task_scopes", "foreground_run_heads", "context_route_decisions",
        "effect_gate_rejections",
        "task_grants_user", "task_grants_policy_auto", "authorization_sagas",
        "policy_generation", "policy_receipt_present",
        "provider_invocations", "execution_effects", "evidence_envelopes",
        "cognitive_memory_heads", "audit_attempts",
        "fs_auto_note_exists", "fs_drift_file_exists"]
row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "turn": int(turn),
       "kind": os.environ.get("MM_KIND", ""), "outcome": outcome,
       "elapsed_s": float(elapsed),
       "policy_mode": rest[len(keys)], "policy_provenance": rest[len(keys) + 1],
       "last_run_state": rest[len(keys) + 2],
       "note": os.environ.get("MM_NOTE", "")}
row.update({k: int(v) for k, v in zip(keys, rest[:len(keys)])})
with open(p, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
print(json.dumps(row, ensure_ascii=False))
PY
}

terminal_reached() { # $1 baseline run count
  counters
  [ "$RUN_N" -gt "$1" ] || return 1
  case "$RUNSTATE" in COMPLETED|FAILED|STOPPED|CANCELLED) return 0 ;; esac
  return 1
}

# send_confirmed <bid> <msg> <baseline run count>: send, require a new Run head
# within 30 s, retry the send once.  Echoes ok|retried|send_failed.
send_confirmed() {
  local bid="$1" msg="$2" base="$3" i n
  "$SEND" "$bid" "$msg" >/dev/null 2>&1 || true
  for i in $(seq 1 6); do sleep 5; n=$(q "$STATE" "select count(*) from foreground_run_heads;"); [ "$n" -gt "$base" ] && { echo ok; return 0; }; done
  "$SEND" "$bid" "$msg" >/dev/null 2>&1 || true
  for i in $(seq 1 6); do sleep 5; n=$(q "$STATE" "select count(*) from foreground_run_heads;"); [ "$n" -gt "$base" ] && { echo retried; return 0; }; done
  echo send_failed; return 1
}

wait_previous_idle() { # a slow previous Run must finish, else 发送 is 停止
  local i
  for i in $(seq 1 120); do
    RS=$(qs "$STATE" "select current_state from foreground_run_heads order by updated_at desc limit 1;")
    case "$RS" in RUNNING|CLAIMED|QUEUED) sleep 10 ;; *) break ;; esac
  done
}

wait_analysis_quiet() { # the analysis lane settles 10–60 s after a turn
  local i
  for i in $(seq 1 18); do
    n=$(q "$HM" "select count(*) from analysis_batches where state not in ('applied','failed','dead_letter');")
    [ "$n" = 0 ] && break
    sleep 5
  done
}

# ---------- main loop ----------
echo "manual_driver: bundle=$BID userdata=$USERDATA evidence=$EVIDENCE start=$START"
echo "manual_driver: fixtures ws=$WS rootB=$ROOT_B rootC=$ROOT_C link=$ROOT_LINK -> $OUTSIDE"
record 0 baseline baseline 0 >/dev/null

for (( T=START; T<=LAST; T++ )); do
  MSG="${TURNS[$T]}"
  K="${KIND[$T]}"

  if [ "$K" = "ui" ]; then
    echo "=== T$T [MANUAL UI] ${HINT[$T]}"
    echo "    完成 UI 步骤后输入观察结果并按 Enter 继续（不发送消息）..."
    UI_NOTE=""; read -r UI_NOTE || true
    record "$T" ui manual_ui 0 "$UI_NOTE"
    continue
  fi

  [ "$T" = 13 ] && drift_root_c

  wait_previous_idle
  counters
  BASE_RUN=$RUN_N
  echo "=== T$T [$K] send (${#MSG} chars)"
  START_TS=$(date +%s)
  SENT=$(send_confirmed "$BID" "$MSG" "$BASE_RUN")
  if [ "$SENT" = send_failed ]; then
    echo "    !! T$T send_failed: no new Run head after two sends (recorded, continuing)"
    record "$T" "$K" send_failed $(( $(date +%s) - START_TS )) "send_failed"
    continue
  fi
  [ "$SENT" = retried ] && echo "    warn: T$T needed a second send"

  UI_NOTE=""
  if [ "$K" = "ask" ]; then
    echo "    >>> T$T 需要 UI 应答：${HINT[$T]}"
    echo "    >>> 应答后输入观察结果并按 Enter（挑战 TTL 300s，务必尽快）..."
    read -r UI_NOTE || true
  fi

  OUTCOME=timeout
  while :; do
    ELAPSED=$(( $(date +%s) - START_TS ))
    [ "$ELAPSED" -ge "$TIMEOUT" ] && break
    if terminal_reached "$BASE_RUN"; then OUTCOME=settled; break; fi
    sleep "$POLL"
  done
  ELAPSED=$(( $(date +%s) - START_TS ))
  [ "$OUTCOME" = timeout ] && echo "    !! T$T timed out after ${ELAPSED}s (recorded, continuing)"
  wait_analysis_quiet
  record "$T" "$K" "$OUTCOME" "$ELAPSED" "${SENT}${UI_NOTE:+ }${UI_NOTE}"
done

echo "manual_driver: done -> $PROGRESS"
