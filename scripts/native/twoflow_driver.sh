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
#   A6_SEND     path to send.sh helper (default: scratchpad send.sh)
#   TF_TIMEOUT  per-turn timeout seconds (default: 480)
#   A6_POLL     poll interval seconds (default: 5)
#
# Contains NO credentials.  Turn kinds: send / ui (@UI@) / restart (@RESTART@).
set -u

BID="${1:?bundle-id required}"
USERDATA="${2:?userdata dir required}"
EVIDENCE="${3:?evidence dir required}"
PHASE="${4:?phase required: flow1|flow2|all}"
START="${5:-}"

SEND="${A6_SEND:-/private/tmp/claude-501/-Users-taiwan-PROJECTS-SimplaHarness/6927d19d-804c-42ea-a91d-fd3cf836f540/scratchpad/send.sh}"
TIMEOUT="${TF_TIMEOUT:-480}"
POLL="${A6_POLL:-5}"

DATA="$USERDATA/data"
HM="$DATA/human_memory_v7.db"
STATE="$DATA/state.db"
PRODUCT="$DATA/sdk-product-state.db"
AUDIT="$DATA/operation-audit.db"
EXEC="$DATA/simple-harness-sdk/execution-v6.sqlite3"
PROGRESS="$EVIDENCE/twoflow-progress.jsonl"

[ -x "$SEND" ] || [ -f "$SEND" ] || { echo "send helper not found: $SEND" >&2; exit 2; }
mkdir -p "$EVIDENCE"

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
TURNS[4]="把 clips.md 读回来，确认三行都在。"
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
TURNS[15]="这个任务当时留下的 clips.md 现在还在吗？读出来核对一下三行素材条目。"
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
}

record() { # record <turn> <kind> <phase> <outcome> <elapsed> [note]
  counters
  TF_NOTE="${6:-}" TF_KIND="$2" TF_PHASE="$3" python3 - "$PROGRESS" "$1" "$4" "$5" \
      "$PRIMARY_N" "$PRIMARY_W" "$SCOPE_N" "$RUN_N" "$ROUTE_N" "$ROUTE_CREATE_N" \
      "$ROUTE_RESUME_N" "$ROUTE_NORECALL_N" "$SEARCH_N" "$OPEN_N" "$EVENT_N" "$VIEW_N" \
      "$CLOSURE_N" "$PROCUSE_N" "$BINDREV_N" \
      "$ENV_N" "$HEADS_N" "$SEM_N" "$EPI_N" "$PROC_N" "$PROS_N" \
      "$PROCREC_N" "$PROSREC_N" "$PROSREG_N" "$SUPP_N" "$SUPPT_N" \
      "$TRREQ_N" "$TRRES_N" "$TRITEM_N" \
      "$AUD_GRANT_N" "$AUD_DEL_N" "$AUD_HSTREAM_N" "$AUD_HDEL_N" \
      "$INV_N" "$EFFECT_N" "$TG_USER_N" "$PRIMARY_ID" "$RUNSTATE" <<'PY'
import json, os, sys, time
p, turn, outcome, elapsed, *rest = sys.argv[1:]
keys = ["primary_conversations", "primary_conversations_writable", "task_scopes",
        "foreground_run_heads", "context_route_decisions", "route_create_new",
        "route_resume_existing", "route_no_recall", "task_scope_search_ops",
        "task_scope_open_ops", "task_scope_events", "task_scope_read_view_revisions",
        "task_scope_closure_receipts", "procedure_uses", "binding_revisions",
        "evidence_envelopes", "cognitive_memory_heads", "heads_semantic", "heads_episode",
        "heads_procedure", "heads_prospective",
        "procedure_records", "prospective_records", "prospective_scheduler_registrations",
        "suppression_directives", "suppression_targets",
        "typed_recall_requests", "typed_recall_results", "typed_recall_result_items",
        "human_audit_grants", "human_audit_deliveries", "human_audit_host_streams",
        "human_audit_host_deliveries",
        "provider_invocations", "execution_effects", "task_grants_user"]
row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "turn": int(turn),
       "kind": os.environ.get("TF_KIND", ""), "phase": os.environ.get("TF_PHASE", ""),
       "outcome": outcome, "elapsed_s": float(elapsed),
       "primary_conversation_id": rest[len(keys)],
       "last_run_state": rest[len(keys) + 1],
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

send_confirmed() {
  local bid="$1" msg="$2" base="$3" i n
  "$SEND" "$bid" "$msg" >/dev/null 2>&1 || true
  for i in $(seq 1 6); do sleep 5; n=$(q "$STATE" "select count(*) from foreground_run_heads;"); [ "$n" -gt "$base" ] && { echo ok; return 0; }; done
  "$SEND" "$bid" "$msg" >/dev/null 2>&1 || true
  for i in $(seq 1 6); do sleep 5; n=$(q "$STATE" "select count(*) from foreground_run_heads;"); [ "$n" -gt "$base" ] && { echo retried; return 0; }; done
  echo send_failed; return 1
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
  cat <<'EOF'
=== @RESTART@ 现在执行同 userdata 重启（流程一已结束）
    1) pkill -f '<bundle 名>.app/Contents/MacOS' ; sleep 3
       lsof -nP -iTCP:18120 -sTCP:LISTEN      # 应无输出
    2) python scripts/native/launch_native_candidate.py --launch \
         --source <worktree> --bundle "<…verify.app>" --installed-target <installed> \
         --userdata <E1>/userdata \
         --evidence-root .local-test-evidence/2026-09-09/twoflow-<sha>/ --port 18120
       （--userdata 必须是第一次跑出来的 <E1>/userdata；新 run 目录 <E2> 只放第二段 native.log）
    3) 等 UI 不再显示「等待主对话就绪 / 正在重新读取…」
    输入观察结果（如 E2=<路径> pid=<新 pid> primary_id_unchanged=1）并按 Enter 继续...
EOF
  RS_NOTE=""; read -r RS_NOTE || true
  # turn = -1 so the restart marker never collides with a real turn or the baseline.
  record -1 restart restart restart 0 "$RS_NOTE"
}

# ---------- main loop ----------
echo "twoflow_driver: bundle=$BID userdata=$USERDATA evidence=$EVIDENCE phase=$PHASE turns=$FIRST..$LAST"
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
  if [ "$SENT" = send_failed ]; then
    echo "    !! T$T send_failed: no new Run head after two sends (recorded, continuing)"
    record "$T" send "$PH" send_failed $(( $(date +%s) - START_TS )) "send_failed"
    continue
  fi
  [ "$SENT" = retried ] && echo "    warn: T$T needed a second send"

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
