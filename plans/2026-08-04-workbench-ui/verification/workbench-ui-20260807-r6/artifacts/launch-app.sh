#!/usr/bin/env bash
# r6 真机道启动包装：只起 Tauri（自带唯一 vite + 自 spawn backend），env 注入齐全。
# 用法：launch-app.sh <logname> [extra env KEY=VAL ...]
set -uo pipefail
ROOT=/Users/denny/projects/simple_harness
LOG=$ROOT/plans/2026-08-04-workbench-ui/verification/workbench-ui-20260807-r6/artifacts/${1:-session}.log
shift || true
cd $ROOT/tauri-app
env DESKPET_USER_DATA_DIR=$ROOT/.testenv/cold-A \
    DESKPET_BACKEND_DIR=$ROOT/backend \
    DESKPET_DEV_MODE=1 \
    "$@" npm exec tauri dev -- --config '{"build":{"beforeDevCommand":"npm run dev:relay"}}' > "$LOG" 2>&1 &
echo "launched pid=$! log=$LOG"
