#!/usr/bin/env bash
ROOT=/Users/denny/projects/simple_harness
LOG=$ROOT/plans/2026-08-04-workbench-ui/verification/workbench-ui-20260808-r8/artifacts/s01-cold.log
cd $ROOT/tauri-app
env DESKPET_USER_DATA_DIR=$ROOT/.testenv/cold-S01-r7 \
    DESKPET_BACKEND_DIR=$ROOT/backend \
    DESKPET_DEV_MODE=1 \
    npm exec tauri dev -- --config '{"build":{"beforeDevCommand":"npm run dev:relay"}}' > "$LOG" 2>&1 &
echo "launched pid=$!"
