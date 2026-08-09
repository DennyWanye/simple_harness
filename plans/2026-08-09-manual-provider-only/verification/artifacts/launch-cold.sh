#!/usr/bin/env bash
# 阶段5 冷启动：全新 user-data 目录，验证「无登录墙 + 零会话」。
#
# 硬闸 0：屏幕未锁。锁屏时 WindowServer 不合成用户窗口 → AX 查询返回 0 窗口、
# screencapture 只有壁纸、WebView 被挂起导致前端 effect 不执行（表现为
# backend 起不来）。这套失败形态酷似"应用崩了/代码回归"，2026-08-08 实测据此
# 误判过一次。所有观测前先断言未锁。
# 坑：不能用管道 + grep -q（pipefail 下 ioreg 非零退出 / SIGPIPE 都会把管道
# 判为失败），用纯 bash case 做子串匹配。
_ioreg_out="$(ioreg -n Root -d1 -a 2>/dev/null || true)"
case "$_ioreg_out" in *CGSSessionScreenIsLocked*)
  echo "SCREEN_LOCKED: 屏幕已锁定，AX/截图/窗口计数全部无效观测。请先解锁再测。" >&2
  exit 3 ;;
esac

ROOT=/Users/denny/projects/simple_harness
PROFILE=${1:-$ROOT/.testenv/phase5-cold}
LOG=$ROOT/plans/2026-08-09-manual-provider-only/verification/artifacts/phase5-cold.log

# DESKPET_BACKEND_DIR 必须设：否则 Tauri 会跑 bundle 里的 frozen backend
# （旧构建产物，不含本次改动），测了等于白测。日志里要看到
# `[backend_launch] Dev python=... backend_dir=<ROOT>/backend` 才算对。
cd "$ROOT/tauri-app" || exit 1
# DESKPET_CONFIG 优先级 1，会跳过 resolve_config_path 里的
# `_recover_orphaned_endpoints` 自愈——那个自愈会从**默认 user data 目录**
# （~/Library/Application Support/deskpet）把 endpoint 复制进来，导致
# "全新 profile" 其实继承了真机既有 provider（2026-08-09 实测踩到）。
env DESKPET_CONFIG="$PROFILE/config.toml" \
    DESKPET_USER_DATA_DIR="$PROFILE" \
    DESKPET_BACKEND_DIR="$ROOT/backend" \
    DESKPET_DEV_MODE=1 \
    npm exec tauri dev > "$LOG" 2>&1 &
echo "launched pid=$! profile=$PROFILE log=$LOG"
