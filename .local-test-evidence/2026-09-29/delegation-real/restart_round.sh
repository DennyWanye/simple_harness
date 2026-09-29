#!/bin/bash
# 一轮"子运行中途重启"：等身份就绪 → 发委派 → 看到子运行就干净重启应用 → 跟随到结束。
set -u
OUT=$1
HERE=$(cd "$(dirname "$0")" && pwd)
UI=/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-25/opt/ui-full
PY=/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/backend/.venv/bin/python
cd "$HERE"
sleep 40
$PY restart_run.py send "$OUT.json" | tail -1
grep -q '"child"' "$OUT.json" || { echo NO-CHILD; exit 0; }
P=$(pgrep -f "MacOS/simple-harness$"); B=$(pgrep -f "venv/bin/python main.py")
kill $P 2>/dev/null
for b in $B; do for i in $(seq 1 30); do ps -p $b >/dev/null 2>&1 || break; sleep 1; done; ps -p $b >/dev/null 2>&1 && kill $b; done
sleep 1
$PY - "$OUT.json" <<'PYEOF'
import json, sqlite3, sys
o = json.load(open(sys.argv[1]))
con = sqlite3.connect("file:/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-25/opt/ui-full/userdata/data/simple-harness-sdk/execution-v6.sqlite3?mode=ro", uri=True)
print("at-restart", con.execute("select run_id,state from runs where run_id in (?,?)", (o["child"], o["parent"])).fetchall())
PYEOF
(cd "$UI" && DESKPET_DEV_MODE=1 nohup zsh launch-app.sh > launch.out 2>&1 &)
echo relaunched
$PY restart_run.py watch "$OUT.json" > "$OUT-watch.log" 2>&1
cat "$OUT-watch.log"
