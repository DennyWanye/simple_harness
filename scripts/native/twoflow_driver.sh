#!/bin/bash
# 两轮完整流程（含重启）native driver (2026-09-09): 22 turns (20 sends + 2 UI)
# split into two phases around an app restart on the SAME userdata.
# Covers S6 Task 7「两轮长对话/重启旅程」+ HM-TO-A1/A3/A4/A5/A7/A8.
#
# Usage: twoflow_driver.sh <bundle-id> <userdata-dir> <evidence-dir> <phase> [start-turn]
#   <phase> = flow1 | flow2 | all
#   <evidence-dir> MUST be the FIRST run dir <E1> for both phases — the progress
#   file has to stay a single file so the verifier can take per-turn deltas.
#
# Env overrides:
#   A6_SEND        path to send.sh helper (default: scratchpad send.sh)
#   A6_AXDUMP      path to ax_dump.sh helper (default: scratchpad ax_dump.sh)
#   TF_FIXTURES    read fixture dir (default: $HOME/SimpleHarnessWorkSpace/twoflow-fixture)
#   TF_TIMEOUT     per-turn timeout seconds (default: 480 = plan §4「8 分钟未见终态」)
#   A6_POLL        poll interval seconds (default: 5)
#   A6_UI_SETTLE   bounded wait (seconds) for a pending authorization card / 停止 to clear
#                  before a send (default: 180)
#   TWOFLOW_DRY_RUN=1  print the turn plan and exit; touches no DB, sends nothing
#
# Contains NO credentials.  Turn kinds: send / ui (@UI@) / restart (@RESTART@).
#
# 2026-09-09 对齐（今天的三处变更）：
#   * 授权策略的唯一权威是 workflow.db.authorization_policy_state（MM-D1）——
#     sdk-product-state.db 的同名表是 DDL 残留种子行，驱动**不得**读它判 auto/manual。
#   * 读类工具（read_file/glob/grep/list_directory）现在要求目标目录已绑定；越界读走
#     S4 绑定提案通道（F-Z1b/F-Z1c）。Auto 策略自动授予，模型需要**重路由一次**：
#     native.log 里应出现 read_workspace_binding_revised → context_route → read。
#     因此 T4 读的是任务托管家目录**之外**的夹具（binding_roots +1），
#     T15 重启后再读同一夹具（binding_roots 不变）—— 这就是绑定跨重启复用的证据。
#   * 每轮记录新增 binding_roots / binding_proposals / policy_* 三组计数。
set -u

BID="${1:?bundle-id required}"
USERDATA="${2:?userdata dir required}"
EVIDENCE="${3:?evidence dir required}"
PHASE="${4:?phase required: flow1|flow2|all}"
START="${5:-}"

SCRATCH="/private/tmp/claude-501/-Users-taiwan-PROJECTS-SimplaHarness/6927d19d-804c-42ea-a91d-fd3cf836f540/scratchpad"
SEND="${A6_SEND:-$SCRATCH/send.sh}"
AXDUMP="${A6_AXDUMP:-$SCRATCH/ax_dump.sh}"
TIMEOUT="${TF_TIMEOUT:-480}"
POLL="${A6_POLL:-5}"
UI_SETTLE="${A6_UI_SETTLE:-180}"
DRY_RUN="${TWOFLOW_DRY_RUN:-0}"
FIXDIR="${TF_FIXTURES:-$HOME/SimpleHarnessWorkSpace/twoflow-fixture}"
FIXFILE="$FIXDIR/clips-source.md"

DATA="$USERDATA/data"
HM="$DATA/human_memory_v7.db"
STATE="$DATA/state.db"
PRODUCT="$DATA/sdk-product-state.db"
# MM-D1: 授权策略的唯一权威。sdk-product-state.db 的同名表是 DDL 残留，恒为
# auto/0/factory_default，读它会把真实的 manual 记成 auto。
WF="$DATA/workflow.db"
AUDIT="$DATA/operation-audit.db"
EXEC="$DATA/simple-harness-sdk/execution-v6.sqlite3"
PROGRESS="$EVIDENCE/twoflow-progress.jsonl"

if [ "$DRY_RUN" != 1 ]; then
  [ -x "$SEND" ] || [ -f "$SEND" ] || { echo "send helper not found: $SEND" >&2; exit 2; }
  mkdir -p "$EVIDENCE"
fi

# ---------- read fixture (deterministic; only created when missing) ----------
# 必须落在既定 workspace 根（~/SimpleHarnessWorkSpace）的**严格后代**里, 且不能是
# 任务托管家目录 —— 这样 T4 的 read_file 才会触发 F-Z1b 的绑定提案(Auto 自动授予)。
# 刻意做小(< 16 KiB): 本旅程验重启, 不验分页。
gen_fixtures() {
  [ -s "$FIXFILE" ] && return 0
  mkdir -p "$FIXDIR"
  cat >"$FIXFILE" <<'EOF'
# 霜降素材 · 原始清单（只读夹具）

A-01 霜降清晨的窗台，长镜头，可用
A-02 霜降市集的摊位，手持，待剪
A-03 霜降夜色的路灯，固定机位，可用

归档口径：成品一律放到「霜降素材 / 成片」。
EOF
}
[ "$DRY_RUN" = 1 ] || gen_fixtures

FLOW1_FIRST=1;  FLOW1_LAST=11
FLOW2_FIRST=12; FLOW2_LAST=22

case "$PHASE" in
  flow1) FIRST=${START:-$FLOW1_FIRST}; LAST=$FLOW1_LAST ;;
  flow2) FIRST=${START:-$FLOW2_FIRST}; LAST=$FLOW2_LAST ;;
  all)   FIRST=${START:-$FLOW1_FIRST}; LAST=$FLOW2_LAST ;;
  *) echo "phase must be flow1|flow2|all" >&2; exit 2 ;;
esac

# ---------- turns ----------
TURNS=(); KIND=(); HINT=()

# —— 流程一：建任务 → 用工具 → 记决定 → 收口 ——
TURNS[1]="记住：我整理素材的时候，成品一律放到「霜降素材 / 成片」这个目录。"
KIND[1]=send; HINT[1]=""
TURNS[2]="新建一个项目任务：霜降素材整理。"
KIND[2]=send; HINT[2]=""
TURNS[3]="在这个任务的工作目录里建一个 clips.md，写三行素材条目：A-01、A-02、A-03。"
KIND[3]=send; HINT[3]=""
# T4 读的是任务托管家目录**之外**的夹具 -> 触发 F-Z1b 读闸门绑定提案(Auto 自动授予),
# 期望 native.log: read_workspace_binding_revised -> context_route(重路由) -> read_file。
TURNS[4]="请读一下 $FIXFILE 这个文件，把里面列的三条素材编号念给我听，并确认和 clips.md 里写的一致。"
KIND[4]=send; HINT[4]=""
TURNS[5]="记一个决定：素材编号统一用 A-序号 两段式，不再用日期前缀。"
KIND[5]=send; HINT[5]=""
TURNS[6]="把上一句话改得更简洁一点。"
KIND[6]=send; HINT[6]=""
TURNS[7]="另外新建一个项目任务：霜降字幕校对。"
KIND[7]=send; HINT[7]=""
TURNS[8]="在字幕校对这个任务的目录里建一个 subs.md，写一行「待校对」。"
KIND[8]=send; HINT[8]=""
TURNS[9]="请记住我以后常用的一个流程，名字叫「霜降清点」：第一步在当前工作目录写 inventory.md，列出目录里的文件；第二步把同样内容再抄一份到 inventory-backup.md。以后我说按霜降清点做，就按这两步。"
KIND[9]=send; HINT[9]=""
TURNS[10]="这次霜降素材整理收尾之后，提醒我写一份成片说明。"
KIND[10]=send; HINT[10]=""
TURNS[11]="霜降素材整理这个任务先到这里，标记完成。"
KIND[11]=send; HINT[11]=""

# —— 流程二：搜索恢复 → 跨重启召回 → UI 遗忘 → 审计面核验 ——
TURNS[12]="我们接着聊。你还记得我最早说过，成品要放到哪个目录吗？只依据我以前说过的回答。"
KIND[12]=send; HINT[12]=""
TURNS[13]="我之前做过一个跟「霜降」有关的整理任务，帮我先找出来，别急着打开。"
KIND[13]=send; HINT[13]=""
TURNS[14]="就精确打开「霜降素材整理」，把它的恢复要点和已经记下的决定说给我听。"
KIND[14]=send; HINT[14]=""
# T15 重启后再读**同一个** T4 夹具: 绑定根已在库里, 不应再出提案、binding_roots 不增。
TURNS[15]="这个任务当时留下的 clips.md 现在还在吗？读出来核对一下三行素材条目；另外把 $FIXFILE 也再读一遍，看是不是还能读到。"
KIND[15]=send; HINT[15]=""
TURNS[16]="在这个任务的目录里，按我以前存的「霜降清点」流程做一遍。"
KIND[16]=send; HINT[16]=""
TURNS[17]="以后有机会我想学做饭。"
KIND[17]=send; HINT[17]=""
TURNS[18]="@UI@"
KIND[18]=ui
HINT[18]="点『记忆』→『记忆列表』→（必要时用『下一页』翻页）找到「成品一律放到『霜降素材 / 成片』」那条，点 AXButton『忘记这条记忆』——一击即生效，无二次确认。输入 before=<点前条数> after=<点后条数> forgot=1"
TURNS[19]="再问一次：我说过成品要放到哪个目录？"
KIND[19]=send; HINT[19]=""
TURNS[20]="我最早说那句话的原文，现在还能在这个对话里翻到吗？把那一条原话找出来给我看。"
KIND[20]=send; HINT[20]=""
TURNS[21]="@UI@"
KIND[21]=ui
HINT[21]="点『操作记录』→『查看我的记忆操作记录（仅元数据）』→『读取记录』至少一页 → 在『本机执行审计』区点『读取终态 Run 审计』→ 对一个 Run 点『查看该 Run 的操作』→ 点『读取记忆调用记录』→ 点『结束本次查看』→ 切回『记忆列表』。输入 grant=1 pages=<n> sections=runs,run_operations,memory_calls"
TURNS[22]="霜降字幕校对这个任务也标记完成，然后用一句话总结这两轮我们一共做了什么。"
KIND[22]=send; HINT[22]=""

phase_of() { [ "$1" -le "$FLOW1_LAST" ] && echo flow1 || echo flow2; }

# ---------- sqlite helpers (read-only, WAL-safe) ----------
q() { local db="$1" sql="$2" out; [ -f "$db" ] || { echo 0; return; }
      out="$(sqlite3 "file:${db}?mode=ro" "$sql" 2>/dev/null)" || out=""; [ -n "$out" ] || out=0; echo "$out"; }
qs() { local db="$1" sql="$2" out; [ -f "$db" ] || { echo ""; return; }
       out="$(sqlite3 "file:${db}?mode=ro" "$sql" 2>/dev/null)" || out=""; echo "$out"; }

counters() {
  PRIMARY_N=$(q "$STATE" "select count(*) from human_memory_primary_conversations;")
  PRIMARY_W=$(q "$STATE" "select count(*) from human_memory_primary_conversations where writable=1;")
  SCOPE_N=$(q "$STATE" "select count(*) from task_scopes;")
  RUN_N=$(q "$STATE" "select count(*) from foreground_run_heads;")
  ROUTE_N=$(q "$STATE" "select count(*) from context_route_decisions;")
  ROUTE_CREATE_N=$(q "$STATE" "select count(*) from context_route_decisions where route='create_new';")
  ROUTE_RESUME_N=$(q "$STATE" "select count(*) from context_route_decisions where route='resume_existing';")
  ROUTE_NORECALL_N=$(q "$STATE" "select count(*) from context_route_decisions where origin='no_recall';")
  SEARCH_N=$(q "$STATE" "select count(*) from task_scope_search_access_receipts where operation='search';")
  OPEN_N=$(q "$STATE" "select count(*) from task_scope_search_access_receipts where operation='open';")
  EVENT_N=$(q "$STATE" "select count(*) from task_scope_events;")
  VIEW_N=$(q "$STATE" "select count(*) from task_scope_read_view_revisions;")
  CLOSURE_N=$(q "$STATE" "select count(*) from task_scope_closure_receipts;")
  PROCUSE_N=$(q "$STATE" "select count(*) from procedure_uses;")
  BINDREV_N=$(q "$STATE" "select count(*) from task_workspace_binding_revisions;")
  # F-Z1b 读闸门: 越界读 -> 提案 -> (Auto)自动授予 -> 新根。跨重启复用时两者都不增。
  BINDROOT_N=$(q "$STATE" "select count(*) from task_workspace_binding_roots;")
  BINDPROP_N=$(q "$STATE" "select count(*) from task_workspace_binding_proposals;")
  ENV_N=$(q "$HM" "select count(*) from evidence_envelopes;")
  HEADS_N=$(q "$HM" "select count(*) from cognitive_memory_heads;")
  SEM_N=$(q "$HM" "select count(*) from cognitive_memory_heads where memory_type='semantic';")
  EPI_N=$(q "$HM" "select count(*) from cognitive_memory_heads where memory_type='episode';")
  PROC_N=$(q "$HM" "select count(*) from cognitive_memory_heads where memory_type='procedure';")
  PROS_N=$(q "$HM" "select count(*) from cognitive_memory_heads where memory_type='prospective';")
  PROCREC_N=$(q "$HM" "select count(*) from procedure_records;")
  PROSREC_N=$(q "$HM" "select count(*) from prospective_records;")
  PROSREG_N=$(q "$HM" "select count(*) from prospective_scheduler_registrations;")
  SUPP_N=$(q "$HM" "select count(*) from suppression_directives;")
  SUPPT_N=$(q "$HM" "select count(*) from suppression_targets;")
  TRREQ_N=$(q "$HM" "select count(*) from typed_recall_requests;")
  TRRES_N=$(q "$HM" "select count(*) from typed_recall_results;")
  TRITEM_N=$(q "$HM" "select count(*) from typed_recall_result_items;")
  AUD_GRANT_N=$(q "$AUDIT" "select count(*) from human_audit_grants;")
  AUD_DEL_N=$(q "$AUDIT" "select count(*) from human_audit_deliveries;")
  AUD_HSTREAM_N=$(q "$AUDIT" "select count(*) from human_audit_host_streams;")
  AUD_HDEL_N=$(q "$AUDIT" "select count(*) from human_audit_host_deliveries;")
  INV_N=$(q "$EXEC" "select count(*) from provider_invocations;")
  EFFECT_N=$(q "$EXEC" "select count(*) from execution_effects;")
  TG_USER_N=$(q "$PRODUCT" "select count(*) from task_grants where source='user';")
  RUNSTATE=$(qs "$STATE" "select current_state from foreground_run_heads order by updated_at desc limit 1;")
  [ -n "$RUNSTATE" ] || RUNSTATE="none"
  PRIMARY_ID=$(qs "$STATE" "select primary_conversation_id from human_memory_primary_conversations limit 1;")
  [ -n "$PRIMARY_ID" ] || PRIMARY_ID="none"
  # MM-D1: 只读 workflow.db。本旅程要求 auto —— Auto 才会自动授予读闸门的绑定提案。
  POLICY_MODE=$(qs "$WF" "select mode from authorization_policy_state limit 1;")
  [ -n "$POLICY_MODE" ] || POLICY_MODE="unknown"
  POLICY_GEN=$(q "$WF" "select generation from authorization_policy_state limit 1;")
  POLICY_PROV=$(qs "$WF" "select provenance from authorization_policy_state limit 1;")
  [ -n "$POLICY_PROV" ] || POLICY_PROV="unknown"
}

record() { # record <turn> <kind> <phase> <outcome> <elapsed> [note]
  counters
  TF_NOTE="${6:-}" TF_KIND="$2" TF_PHASE="$3" python3 - "$PROGRESS" "$1" "$4" "$5" \
      "$PRIMARY_N" "$PRIMARY_W" "$SCOPE_N" "$RUN_N" "$ROUTE_N" "$ROUTE_CREATE_N" \
      "$ROUTE_RESUME_N" "$ROUTE_NORECALL_N" "$SEARCH_N" "$OPEN_N" "$EVENT_N" "$VIEW_N" \
      "$CLOSURE_N" "$PROCUSE_N" "$BINDREV_N" "$BINDROOT_N" "$BINDPROP_N" \
      "$ENV_N" "$HEADS_N" "$SEM_N" "$EPI_N" "$PROC_N" "$PROS_N" \
      "$PROCREC_N" "$PROSREC_N" "$PROSREG_N" "$SUPP_N" "$SUPPT_N" \
      "$TRREQ_N" "$TRRES_N" "$TRITEM_N" \
      "$AUD_GRANT_N" "$AUD_DEL_N" "$AUD_HSTREAM_N" "$AUD_HDEL_N" \
      "$INV_N" "$EFFECT_N" "$TG_USER_N" "$POLICY_GEN" \
      "$PRIMARY_ID" "$RUNSTATE" "$POLICY_MODE" "$POLICY_PROV" <<'PY'
import json, os, sys, time
p, turn, outcome, elapsed, *rest = sys.argv[1:]
keys = ["primary_conversations", "primary_conversations_writable", "task_scopes",
        "foreground_run_heads", "context_route_decisions", "route_create_new",
        "route_resume_existing", "route_no_recall", "task_scope_search_ops",
        "task_scope_open_ops", "task_scope_events", "task_scope_read_view_revisions",
        "task_scope_closure_receipts", "procedure_uses", "binding_revisions",
        "binding_roots", "binding_proposals",
        "evidence_envelopes", "cognitive_memory_heads", "heads_semantic", "heads_episode",
        "heads_procedure", "heads_prospective",
        "procedure_records", "prospective_records", "prospective_scheduler_registrations",
        "suppression_directives", "suppression_targets",
        "typed_recall_requests", "typed_recall_results", "typed_recall_result_items",
        "human_audit_grants", "human_audit_deliveries", "human_audit_host_streams",
        "human_audit_host_deliveries",
        "provider_invocations", "execution_effects", "task_grants_user",
        "policy_generation"]
row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "turn": int(turn),
       "kind": os.environ.get("TF_KIND", ""), "phase": os.environ.get("TF_PHASE", ""),
       "outcome": outcome, "elapsed_s": float(elapsed),
       "primary_conversation_id": rest[len(keys)],
       "last_run_state": rest[len(keys) + 1],
       "policy_mode": rest[len(keys) + 2],
       "policy_provenance": rest[len(keys) + 3],
       "note": os.environ.get("TF_NOTE", "")}
row.update({k: int(v) for k, v in zip(keys, rest[:len(keys)])})
with open(p, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
print(json.dumps(row, ensure_ascii=False))
PY
}

terminal_reached() {
  counters
  [ "$RUN_N" -gt "$1" ] || return 1
  case "$RUNSTATE" in COMPLETED|FAILED|STOPPED|CANCELLED) return 0 ;; esac
  return 1
}

# Manual run3 / A6 都见过的失败形态：上一轮留下一张没被应答的授权卡（底部
# 『允许本次绑定/拒绝』或 SDK 弹窗『允许一次』），或发送键还是「■ 停止」，
# 于是 send.sh 把文本填进去、点了一颗当时不存在/不生效的『发送』，
# 结果就是 "no new Run head after two sends"。发送前先有界地等这些按钮消失。
# 永远返回 0：等不到也照旧发送（不比今天更糟），但把原因带进 progress 的 note。
UI_BLOCKERS='允许一次|允许本次绑定|重试完成已允许的绑定|拒绝|停止'

wait_ui_send_ready() {
  local bid="$1" i names tries
  [ -x "$AXDUMP" ] || [ -f "$AXDUMP" ] || { echo axdump_missing; return 0; }
  tries=$(( UI_SETTLE / 5 )); [ "$tries" -ge 1 ] || tries=1
  for i in $(seq 1 "$tries"); do
    names=$(bash "$AXDUMP" "$bid" AXButton 2>/dev/null | awk -F'|' '{print $2}')
    printf '%s\n' "$names" | grep -qE "$UI_BLOCKERS" || { echo ready; return 0; }
    sleep 5
  done
  echo ui_blocked; return 0
}

send_confirmed() {
  local bid="$1" msg="$2" base="$3" i n guard suffix=""
  guard=$(wait_ui_send_ready "$bid"); [ "$guard" = ready ] || suffix=":$guard"
  "$SEND" "$bid" "$msg" >/dev/null 2>&1 || true
  for i in $(seq 1 6); do sleep 5; n=$(q "$STATE" "select count(*) from foreground_run_heads;"); [ "$n" -gt "$base" ] && { echo "ok$suffix"; return 0; }; done
  guard=$(wait_ui_send_ready "$bid"); [ "$guard" = ready ] || suffix=":$guard"
  "$SEND" "$bid" "$msg" >/dev/null 2>&1 || true
  for i in $(seq 1 6); do sleep 5; n=$(q "$STATE" "select count(*) from foreground_run_heads;"); [ "$n" -gt "$base" ] && { echo "retried$suffix"; return 0; }; done
  echo "send_failed$suffix"; return 1
}

wait_previous_idle() {
  local i RS
  for i in $(seq 1 120); do
    RS=$(qs "$STATE" "select current_state from foreground_run_heads order by updated_at desc limit 1;")
    case "$RS" in RUNNING|CLAIMED|QUEUED) sleep 10 ;; *) break ;; esac
  done
}

wait_analysis_quiet() {
  local i n
  for i in $(seq 1 18); do
    n=$(q "$HM" "select count(*) from analysis_batches where state not in ('applied','failed','dead_letter');")
    [ "$n" = 0 ] && break
    sleep 5
  done
}

restart_pause() {
  cat <<EOF
=== @RESTART@ 现在执行同 userdata 重启（流程一已结束）
    1) 杀干净（三件都要，launcher 探到任何 /Contents/MacOS/simple-harness 进程会抛
       native_test_already_running；端口 18120 也会被残留 main.py 占住）：
         pkill -f 'launch_native_candidate.py' ; sleep 1
         pkill -f '/Contents/MacOS/simple-harness' ; sleep 3
         lsof -nP -iTCP:18120 -sTCP:LISTEN | awk 'NR>1{print \$2}' | xargs -r kill
         lsof -nP -iTCP:18120 -sTCP:LISTEN      # 应无输出
    2) 同 userdata 重启（--userdata 走 resolve(strict=True)，必须是第一次跑出来的目录；
       带 --userdata 时启动器不重写 llm_runtime.json，model_overrides.toml 也原样保留）：
         "\$PY" scripts/native/launch_native_candidate.py --launch \\
           --source <worktree> --bundle "<…/SimpleHarness Memory Verify <sha8>p18120.app>" \\
           --installed-target <installed-h0710-m0638-s0313> \\
           --env-file <env> --model deepseek-v4-flash --memory-probe \\
           --userdata $USERDATA \\
           --evidence-root <evidence-root> --port 18120
       新 run 目录 <E2> 只多出第二段 native.log / launch.json；所有 DB 仍在 $USERDATA。
    3) 等 UI 不再显示「等待主对话就绪 / 正在重新读取…」
    输入观察结果（如 E2=<路径> pid=<新 pid> primary_id_unchanged=1）并按 Enter 继续...
EOF
  RS_NOTE=""; read -r RS_NOTE || true
  # turn = -1 so the restart marker never collides with a real turn or the baseline.
  record -1 restart restart restart 0 "$RS_NOTE"
}

dry_run_plan() {
  echo "twoflow_driver DRY RUN: phase=$PHASE turns=$FIRST..$LAST"
  echo "  send helper : $SEND"
  echo "  ax dump     : $AXDUMP"
  echo "  fixture     : $FIXFILE (读闸门绑定提案的目标, 必须在 workspace 根之下)"
  echo "  userdata    : $USERDATA"
  echo "  evidence    : $EVIDENCE  (progress -> $PROGRESS)"
  echo "  policy db   : $WF (MM-D1 唯一权威; 本旅程要求 mode=auto)"
  echo "  timeout=${TIMEOUT}s poll=${POLL}s ui_settle=${UI_SETTLE}s"
  echo
  printf '%-4s %-6s %-6s %-6s %s\n' T PHASE KIND CHARS TEXT
  for (( t=FIRST; t<=LAST; t++ )); do
    printf '%-4s %-6s %-6s %-6s %s\n' \
      "T$t" "$(phase_of "$t")" "${KIND[$t]}" "${#TURNS[$t]}" "${TURNS[$t]}"
    [ -n "${HINT[$t]}" ] && printf '     %-6s %-6s %-6s %s\n' "" "" "" "HINT: ${HINT[$t]}"
  done
  echo
  echo "（dry run：未发送任何消息、未读任何 DB、未创建夹具、未写 progress）"
}

# ---------- main loop ----------
if [ "$DRY_RUN" = 1 ]; then dry_run_plan; exit 0; fi

echo "twoflow_driver: bundle=$BID userdata=$USERDATA evidence=$EVIDENCE phase=$PHASE turns=$FIRST..$LAST"
echo "twoflow_driver: fixture=$FIXFILE ($(wc -c <"$FIXFILE" | tr -d ' ') bytes)"
counters
echo "twoflow_driver: policy(workflow.db)=$POLICY_MODE gen=$POLICY_GEN provenance=$POLICY_PROV"
[ "$POLICY_MODE" = auto ] || echo "    !! 警告：本旅程假定 Auto 策略（读闸门提案自动授予）。当前不是 auto。"
# Only the very first turn writes the T0 baseline; a flow2/resume run must not
# overwrite it, otherwise the verifier's per-turn deltas lose their origin.
if [ "$FIRST" -le "$FLOW1_FIRST" ] && [ ! -s "$PROGRESS" ]; then
  record 0 baseline flow1 baseline 0 >/dev/null
fi
# flow2 launched separately: record the restart marker before the first turn.
if [ "$PHASE" = flow2 ] && [ "$FIRST" = "$FLOW2_FIRST" ]; then restart_pause; fi

for (( T=FIRST; T<=LAST; T++ )); do
  PH=$(phase_of "$T")

  if [ "$PHASE" = all ] && [ "$T" = "$FLOW2_FIRST" ]; then restart_pause; fi

  MSG="${TURNS[$T]}"
  K="${KIND[$T]}"

  if [ "$K" = ui ]; then
    echo "=== T$T [$PH][MANUAL UI] ${HINT[$T]}"
    echo "    完成 UI 步骤后输入观察结果并按 Enter 继续（不发送消息）..."
    UI_NOTE=""; read -r UI_NOTE || true
    record "$T" ui "$PH" manual_ui 0 "$UI_NOTE"
    continue
  fi

  wait_previous_idle
  counters
  BASE_RUN=$RUN_N
  echo "=== T$T [$PH] send (${#MSG} chars)"
  START_TS=$(date +%s)
  SENT=$(send_confirmed "$BID" "$MSG" "$BASE_RUN")
  case "$SENT" in
    send_failed*)
      echo "    !! T$T $SENT: no new Run head after two sends (recorded, continuing)"
      record "$T" send "$PH" send_failed $(( $(date +%s) - START_TS )) "$SENT"
      continue ;;
    retried*) echo "    warn: T$T needed a second send ($SENT)" ;;
  esac
  case "$SENT" in *:ui_blocked) echo "    warn: T$T 发送前授权卡/停止键未消失（有界等待耗尽）" ;; esac

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
  record "$T" send "$PH" "$OUTCOME" "$ELAPSED" "$SENT"
done

if [ "$PHASE" = flow1 ]; then
  echo
  echo "twoflow_driver: 流程一结束。现在按 plan §2 的三步做同 userdata 重启，"
  echo "               然后用 phase=flow2 再跑一次（evidence-dir 仍传同一个 <E1>）:"
  echo "               bash scripts/native/twoflow_driver.sh $BID $USERDATA $EVIDENCE flow2"
  echo "               重启完成后可先手工记一行重启回执:"
  echo "               (可选) 在 flow2 首轮前把 <E2> 路径记入结果文档"
fi
echo "twoflow_driver: done -> $PROGRESS"
