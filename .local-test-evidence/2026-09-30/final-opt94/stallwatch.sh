#!/bin/bash
# 卡死看门狗：回归目录里任何日志 20 分钟没变化且回归没结束，就报警退出
O=/Users/taiwan/PROJECTS/SimplaHarness/simple_harness/.local-test-evidence/2026-09-30/final-opt94
while true; do
  sleep 60
  grep -q ALL-DONE $O/run.out 2>/dev/null && { echo "regression finished"; exit 0; }
  newest=$(stat -f %m $O/*.log $O/run.out 2>/dev/null | sort -n | tail -1)
  [ -n "$newest" ] && [ $(( $(date +%s) - newest )) -gt 1200 ] && { echo "STALL: no log change for 20 min"; tail -3 $O/run.out; exit 1; }
done
