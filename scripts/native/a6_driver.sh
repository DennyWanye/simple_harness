#!/bin/bash
# HM-TO-A6 native driver: send 24 scripted turns, wait for each turn's terminal
# state, and append per-turn evidence counters to <evidence-dir>/a6-progress.jsonl.
#
# Usage: a6_driver.sh <bundle-id> <userdata-dir> <evidence-dir> [start-turn]
#
# Env overrides:
#   A6_SEND      path to send.sh helper (default: scratchpad send.sh)
#   A6_FIXTURES  fixture dir (default: $HOME/SimpleHarnessWorkSpace/a6-fixture)
#   A6_TIMEOUT   per-turn timeout seconds (default: 360)
#   A6_POLL      poll interval seconds (default: 5)
#
# Contains NO credentials. Turns 16 and 24 are manual UI steps (memory graph
# panel); the script pauses and records them without sending a message.
set -u

BID="${1:?bundle-id required}"
USERDATA="${2:?userdata dir required}"
EVIDENCE="${3:?evidence dir required}"
START="${4:-1}"

SEND="${A6_SEND:-/private/tmp/claude-501/-Users-taiwan-PROJECTS-SimplaHarness/6927d19d-804c-42ea-a91d-fd3cf836f540/scratchpad/send.sh}"
FIXDIR="${A6_FIXTURES:-$HOME/SimpleHarnessWorkSpace/a6-fixture}"
TIMEOUT="${A6_TIMEOUT:-360}"
POLL="${A6_POLL:-5}"

DATA="$USERDATA/data"
HM="$DATA/human_memory_v7.db"
STATE="$DATA/state.db"
AUDIT="$DATA/operation-audit.db"
EXEC="$DATA/simple-harness-sdk/execution-v6.sqlite3"
PROGRESS="$EVIDENCE/a6-progress.jsonl"

[ -x "$SEND" ] || [ -f "$SEND" ] || { echo "send helper not found: $SEND" >&2; exit 2; }
mkdir -p "$EVIDENCE" "$FIXDIR"

FA="$FIXDIR/qiufen-checklist-a.md"
FB="$FIXDIR/qiufen-reference-b.md"
GOAL="$FIXDIR/qiufen-goal.txt"

# ---------- fixtures (deterministic; regenerated only when missing) ----------
gen_fixtures() {
  [ -s "$FA" ] && [ -s "$FB" ] && [ -s "$GOAL" ] && return 0
  python3 - "$FA" "$FB" "$GOAL" <<'PY'
import sys
fa, fb, goal = sys.argv[1:4]

def pad(lines, target, prefix):
    out, i = [], 0
    while sum(len(l.encode()) + 1 for l in out) < target:
        out.append(f"{prefix}-{i:05d} 条目：秋分资料整理清单第 {i} 行，校对状态待定，责任人未指派。")
        i += 1
    return out

# FIXTURE A: anchor at ~3 KiB so page-in needs ~3 pages past the 1024B excerpt.
head = ["# 秋分资料整理 · 主清单 A", ""]
body = pad([], 2900, "A")
anchor = ["ANCHOR-ALPHA: 归档编号 QF-2026-0908-ALPHA-7731"]
tail = pad([], 37000, "A2")
open(fa, "w", encoding="utf-8").write("\n".join(head + body + anchor + tail) + "\n")

# FIXTURE B: anchor at ~2.6 KiB.
headb = ["# 秋分资料整理 · 参照件 B", ""]
bodyb = pad([], 2500, "B")
anchorb = ["ANCHOR-BETA", "参照件唯一串：QF-2026-0908-BETA-4419-参照口径以主清单为准"]
tailb = pad([], 45000, "B2")
open(fb, "w", encoding="utf-8").write("\n".join(headb + bodyb + anchorb + tailb) + "\n")

# GOAL: ~18 KiB (> README 16 KiB, > STATUS 12 KiB, < operation_value 32 KiB).
g, i = [], 0
while sum(len(x.encode()) + 1 for x in g) < 18000:
    g.append(f"目标条款 {i:04d}：核对主清单 A 的第 {i} 组条目，逐条比对参照件 B 的口径差异，"
             f"记录差异原因与处理结论，并保留原始出处引用。")
    i += 1
open(goal, "w", encoding="utf-8").write("；".join(g))
PY
}
gen_fixtures

GOAL_TEXT="$(cat "$GOAL")"

# ---------- turns ----------
# UI-only turns are marked with the literal prefix "@UI@".
TURNS=()
TURNS[1]="记住：我做资料校对时，统一用 Python 3.12 跑脚本。"
TURNS[2]="另外记住：我的校对结果一律存到「外接硬盘 / 校对归档」这个目录。"
TURNS[3]="把我上一句话改得更简洁一点。"
TURNS[4]="新建一个项目任务：秋分资料整理。"
TURNS[5]="在这个任务里，先找一下你有没有能读本地文件的工具。"
TURNS[6]="读 $FA 的全文，先只告诉我它的标题和总行数。"
TURNS[7]="把这次任务的目标记下来：把主清单 A 里的清单核对一遍。"
TURNS[8]="再读 $FB 的全文，告诉我它的第一行是什么。"
TURNS[9]="这两个文件分别是干什么用的？就用你已经看到的内容回答。"
TURNS[10]="记一个决定：以主清单 A 为准，参照件 B 只作参照。"
TURNS[11]="主清单 A 里 ANCHOR-ALPHA 那一条的完整取值是什么？照原文给我，不要概括。"
TURNS[12]="顺便问一句，今天几号？"
TURNS[13]="参照件 B 里 ANCHOR-BETA 后面那一整行原文是什么？照原文给我。"
TURNS[14]="以后有机会我想学画画。"
TURNS[15]="记住：秋分资料整理这套校对流程，就按我前面说的 Python 环境执行。"
TURNS[16]="@UI@打开记忆图谱面板，记录节点数与边数"
TURNS[17]="把下面这段完整的目标说明逐字记为这个任务的目标，不要概括、不要省略：${GOAL_TEXT}"
TURNS[18]="你把刚才那段目标保存成功了吗？把它的前两句原样复述一遍。"
TURNS[19]="按我最早说过的，校对结果该存到哪里？只依据我以前说过的回答。"
TURNS[20]="更正一下：校对脚本我现在统一用 Python 3.13，不是 3.12。"
TURNS[21]="不过我印象里上周好像还是按 3.12 在跑的，你说呢？"
TURNS[22]="那你现在按哪个版本执行这套校对流程？"
TURNS[23]="把「这套流程按那个 Python 环境执行」这条关系忘掉。"
TURNS[24]="@UI@关闭图谱面板后重新打开，记录边数，然后手动重发第 22 轮问题"
LAST=24

# ---------- sqlite helpers (read-only, WAL-safe) ----------
q() { # q <db> <sql>  -> prints scalar, 0 when unreadable
  local db="$1" sql="$2" out
  [ -f "$db" ] || { echo 0; return; }
  out="$(sqlite3 "file:${db}?mode=ro" "$sql" 2>/dev/null)" || out=""
  [ -n "$out" ] || out=0
  echo "$out"
}

counters() {
  ENV_N=$(q "$HM" "select count(*) from evidence_envelopes;")
  HEADS_N=$(q "$HM" "select count(*) from cognitive_memory_heads;")
  REV_N=$(q "$HM" "select count(*) from cognitive_memory_revisions;")
  REL_N=$(q "$HM" "select count(*) from cognitive_relations;")
  CONF_N=$(q "$HM" "select count(*) from cognitive_conflict_groups;")
  LLM_N=$(q "$HM" "select count(*) from llm_invocations;")
  RUN_N=$(q "$STATE" "select count(*) from foreground_run_heads;")
  SNAP_N=$(q "$STATE" "select count(*) from run_context_snapshot_receipts;")
  ATT_N=$(q "$STATE" "select count(*) from sdk_provider_attempt_audit;")
  ROUTE_N=$(q "$STATE" "select count(*) from context_route_decisions;")
  VIEW_N=$(q "$STATE" "select count(*) from task_scope_read_view_revisions;")
  INV_N=$(q "$EXEC" "select count(*) from provider_invocations;")
  MAXTOK=$(q "$STATE" "select coalesce(max(input_tokens),0) from sdk_provider_attempt_audit;")
  RUNSTATE=$(q "$STATE" "select current_state from foreground_run_heads order by updated_at desc limit 1;")
  RUNTS=$(q "$STATE" "select coalesce(max(updated_at),0) from foreground_run_heads;")
  PAGEIN_N=$(q "$EXEC" "select count(*) from execution_effects where tool_name='context_page_in';")
  EFFECT_N=$(q "$EXEC" "select count(*) from execution_effects;")
  AUDIT_N=$(q "$AUDIT" "select count(*) from audit_attempts;")
  BOUNDED=$(q "$STATE" "select count(*) from task_scope_read_view_revisions where view_kind in ('README','STATUS') and (instr(cast(content as text),'\"bounded\":true')>0 or instr(cast(content as text),'content-addressed in EVIDENCE')>0);")
}

terminal_reached() { # $1 baseline env count, $2 baseline run count, $3 baseline max updated_at
  counters
  case "$RUNSTATE" in
    COMPLETED|FAILED|STOPPED|CANCELLED)
      if [ "$RUN_N" -gt "$2" ] && awk "BEGIN{exit !($RUNTS > $3)}"; then return 0; fi ;;
  esac
  [ "$ENV_N" -ge $(( $1 + 2 )) ] && return 0
  return 1
}

record() { # record <turn> <outcome> <elapsed>
  counters
  python3 - "$PROGRESS" "$1" "$2" "$3" "$ENV_N" "$HEADS_N" "$REV_N" "$REL_N" "$CONF_N" \
      "$LLM_N" "$RUN_N" "$SNAP_N" "$ATT_N" "$ROUTE_N" "$VIEW_N" "$INV_N" "$MAXTOK" \
      "$PAGEIN_N" "$EFFECT_N" "$AUDIT_N" "$BOUNDED" "$RUNSTATE" <<'PY'
import json, sys, time
p, turn, outcome, elapsed, *rest = sys.argv[1:]
keys = ["evidence_envelopes","cognitive_memory_heads","cognitive_memory_revisions",
        "cognitive_relations","cognitive_conflict_groups","llm_invocations",
        "foreground_run_heads","run_context_snapshot_receipts","sdk_provider_attempt_audit",
        "context_route_decisions","task_scope_read_view_revisions","provider_invocations",
        "max_input_tokens","effects_context_page_in","execution_effects",
        "audit_attempts","bounded_readme_status"]
row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "turn": int(turn),
       "outcome": outcome, "elapsed_s": float(elapsed),
       "last_run_state": rest[len(keys)]}
row.update({k: int(v) for k, v in zip(keys, rest[:len(keys)])})
with open(p, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
print(json.dumps(row, ensure_ascii=False))
PY
}

# ---------- main loop ----------
echo "a6_driver: bundle=$BID userdata=$USERDATA evidence=$EVIDENCE start=$START"
echo "a6_driver: fixtures A=$FA B=$FB GOAL=$GOAL ($(wc -c <"$GOAL") bytes)"
record 0 baseline 0 >/dev/null

for (( T=START; T<=LAST; T++ )); do
  MSG="${TURNS[$T]}"
  if [[ "$MSG" == @UI@* ]]; then
    echo "=== T$T [MANUAL UI] ${MSG#@UI@}"
    echo "    执行该 UI 步骤后按 Enter 继续（不发送消息）..."
    read -r _ || true
    record "$T" manual_ui 0
    continue
  fi

  counters
  BASE_ENV=$ENV_N; BASE_RUN=$RUN_N; BASE_TS=$RUNTS
  echo "=== T$T send (${#MSG} chars)"
  START_TS=$(date +%s)
  "$SEND" "$BID" "$MSG" >/dev/null 2>&1 || echo "    warn: send helper returned non-zero"

  OUTCOME=timeout
  while :; do
    NOW=$(date +%s); ELAPSED=$(( NOW - START_TS ))
    [ "$ELAPSED" -ge "$TIMEOUT" ] && break
    if terminal_reached "$BASE_ENV" "$BASE_RUN" "$BASE_TS"; then OUTCOME=settled; break; fi
    sleep "$POLL"
  done
  NOW=$(date +%s); ELAPSED=$(( NOW - START_TS ))
  [ "$OUTCOME" = timeout ] && echo "    !! T$T timed out after ${ELAPSED}s (recorded, continuing)"
  record "$T" "$OUTCOME" "$ELAPSED"
done

echo "a6_driver: done -> $PROGRESS"
