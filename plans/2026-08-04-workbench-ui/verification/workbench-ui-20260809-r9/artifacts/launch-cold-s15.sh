#!/usr/bin/env bash

# ── 硬闸 0：屏幕未锁 ────────────────────────────────────────────────
# 锁屏时 WindowServer 不合成用户窗口 → AX 查询返回 0 窗口、screencapture 只有
# 壁纸、WebView 被挂起导致前端 effect 不执行（表现为 backend 起不来）。
# 这套失败形态酷似"应用崩了/代码回归"，2026-08-08 实测据此误判过一次，
# 差点去二分一个不存在的回归。所有观测前先断言未锁。
# 两个坑（都实测踩过，别改回管道写法）：
#   ① `ioreg ... | grep -q` + `set -o pipefail`：ioreg 自身退出码非零 → 整条
#      管道判失败 → if 永远不成立。
#   ② 即使先把输出存进变量，`printf ... | grep -q` 仍然失败：grep -q 命中后
#      立即退出并关闭管道，printf 收到 SIGPIPE 返回非零，pipefail 再次把管道
#      判为失败。grep -c 因为读完全部输入反而"正常"，两者结论相反极具迷惑性。
# 结论：用纯 bash 的 case 做子串匹配，完全不经管道。
_ioreg_out="$(ioreg -n Root -d1 -a 2>/dev/null || true)"
case "$_ioreg_out" in *CGSSessionScreenIsLocked*)
  echo "SCREEN_LOCKED: 屏幕已锁定，AX/截图/窗口计数全部无效观测。请先解锁再测。" >&2
  exit 3 ;;
esac

ROOT=/Users/denny/projects/simple_harness
LOG=$ROOT/plans/2026-08-04-workbench-ui/verification/workbench-ui-20260809-r9/artifacts/s15-cold.log
cd $ROOT/tauri-app
env DESKPET_USER_DATA_DIR=$ROOT/.testenv/cold-S15-r8 \
    DESKPET_BACKEND_DIR=$ROOT/backend \
    DESKPET_DEV_MODE=1 \
    npm exec tauri dev -- --config '{"build":{"beforeDevCommand":"npm run dev:relay"}}' > "$LOG" 2>&1 &
echo "launched pid=$!"
