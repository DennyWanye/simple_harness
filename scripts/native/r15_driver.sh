#!/bin/bash
# Procedure real-use journey driver (r15+): 5 scripted turns, confirmed sends,
# Run-head terminal settle, wait for async memory analysis after turns 1–2.
# Usage: r15_driver.sh <bundle-id> <userdata-dir> <evidence-dir> [start-turn]
set -u
BID="${1:?bundle-id}"; USERDATA="${2:?userdata}"; EVIDENCE="${3:?evidence}"; START="${4:-1}"
SEND="${A6_SEND:-/private/tmp/claude-501/-Users-taiwan-PROJECTS-SimplaHarness/6927d19d-804c-42ea-a91d-fd3cf836f540/scratchpad/send.sh}"
STATE="$USERDATA/data/state.db"; HM="$USERDATA/data/human_memory_v7.db"
PROGRESS="$EVIDENCE/r15-progress.jsonl"
q() { local out; [ -f "$1" ] || { echo 0; return; }; out="$(sqlite3 "file:$1?mode=ro" "$2" 2>/dev/null)" || out=""; [ -n "$out" ] || out=0; echo "$out"; }
TURNS=()
TURNS[1]="请记住我以后常用的一个流程，名字叫“松柏记录”：第一步在当前工作目录写 record.txt，内容是当天的记录；第二步把同样内容再写一份 backup.txt。以后我说按松柏记录做，就按这两步做。"
TURNS[2]="我还有一个没定下来的想法，叫“云杉归档”：先把目录里的文件列个清单，再把清单写进 archive.txt。先记着，别执行，以后再说。"
TURNS[3]="帮我把“收到材料以后，我们再开始进行校对工作”改得简洁些。"
TURNS[4]="查一下我以前存的“云杉归档”流程草稿，现在是什么状态？只查，不要执行。"
TURNS[5]="新建一个本地任务叫“松柏九月”，在你管理的工作目录里，按我以前存的“松柏记录”流程做一遍，做完把两个文件读出来核对内容一致。"
TIMEOUTS=(0 300 300 180 300 900)
rec() { python3 - "$PROGRESS" "$@" <<'PY'
import json,sys,time
p,turn,outcome,elapsed,heads,runs,state,uses=sys.argv[1:9]
row={"ts":time.strftime("%Y-%m-%dT%H:%M:%S%z"),"turn":int(turn),"outcome":outcome,"elapsed_s":float(elapsed),"cognitive_memory_heads":int(heads),"foreground_run_heads":int(runs),"last_run_state":state,"procedure_uses":int(uses)}
open(p,"a",encoding="utf-8").write(json.dumps(row,ensure_ascii=False)+"\n"); print(json.dumps(row,ensure_ascii=False))
PY
}
snap() { HEADS=$(q "$HM" "select count(*) from cognitive_memory_heads;"); RUNS=$(q "$STATE" "select count(*) from foreground_run_heads;"); RS=$(q "$STATE" "select current_state from foreground_run_heads order by updated_at desc limit 1;"); USES=$(q "$STATE" "select count(*) from procedure_uses;"); }
for (( T=START; T<=5; T++ )); do
  snap; BASE_RUN=$RUNS; BASE_HEADS=$HEADS; T0=$(date +%s)
  echo "=== T$T send"
  "$SEND" "$BID" "${TURNS[$T]}" >/dev/null 2>&1 || true
  SENT=no; for i in $(seq 1 6); do sleep 5; snap; [ "$RUNS" -gt "$BASE_RUN" ] && { SENT=ok; break; }; done
  if [ "$SENT" = no ]; then "$SEND" "$BID" "${TURNS[$T]}" >/dev/null 2>&1 || true; for i in $(seq 1 6); do sleep 5; snap; [ "$RUNS" -gt "$BASE_RUN" ] && { SENT=retried; break; }; done; fi
  [ "$SENT" = no ] && { echo "    !! T$T send_failed"; rec "$T" send_failed $(( $(date +%s)-T0 )) "$HEADS" "$RUNS" "$RS" "$USES"; continue; }
  OUT=timeout
  while [ $(( $(date +%s)-T0 )) -lt "${TIMEOUTS[$T]}" ]; do snap; case "$RS" in COMPLETED|FAILED|STOPPED|CANCELLED) OUT=$RS; break;; esac; sleep 5; done
  if [ "$T" -le 2 ]; then # wait for the async analysis to land the procedure memory
    for i in $(seq 1 48); do snap; [ "$HEADS" -gt "$BASE_HEADS" ] && break; sleep 5; done
    [ "$HEADS" -gt "$BASE_HEADS" ] || OUT="${OUT}_no_new_head"
  fi
  snap; rec "$T" "$OUT" $(( $(date +%s)-T0 )) "$HEADS" "$RUNS" "$RS" "$USES"
done
echo "r15_driver: done -> $PROGRESS"
