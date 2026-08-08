#!/usr/bin/env bash
# S08 步骤7 专用：起**改版前基线构建**（644ab16 worktree），指向共享 fixture 目录。
# 用法：launch-baseline.sh <logname>
#
# 与 launch-app.sh 的差别（三处，缺一不可）：
#   ① cwd / DESKPET_BACKEND_DIR 指向 /tmp/wbui-baseline，否则跑的是主树代码，
#      "改版前造数"这件事就不成立了；
#   ② DESKPET_PYTHON 指主树 .venv —— 基线 worktree 没有自己的 .venv（项目 CLAUDE.md 坑 #8）；
#   ③ DESKPET_USER_DATA_DIR 指共享 fixture 目录，不是 cold-A。
set -uo pipefail

_ioreg_out="$(ioreg -n Root -d1 -a 2>/dev/null || true)"
case "$_ioreg_out" in *CGSSessionScreenIsLocked*)
  echo "SCREEN_LOCKED: 屏幕已锁定，AX/截图/窗口计数全部无效观测。请先解锁再测。" >&2
  exit 3 ;;
esac

BASE=/tmp/wbui-baseline
FIX=/tmp/wbui-upgrade-fixture-userdata
MAIN=/Users/denny/projects/simple_harness
LOG=$MAIN/plans/2026-08-04-workbench-ui/verification/workbench-ui-20260809-r9/artifacts/${1:-baseline}.log

cd "$BASE/tauri-app" || exit 1
env DESKPET_USER_DATA_DIR="$FIX" \
    DESKPET_BACKEND_DIR="$BASE/backend" \
    DESKPET_PYTHON="$MAIN/backend/.venv/bin/python" \
    DESKPET_DEV_MODE=1 \
    npm exec tauri dev -- --config '{"build":{"beforeDevCommand":"npm run dev:relay"}}' > "$LOG" 2>&1 &
echo "launched baseline pid=$! log=$LOG"
echo "  user_data=$FIX"
echo "  backend_dir=$BASE/backend"
