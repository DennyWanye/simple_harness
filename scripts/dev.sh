#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
#
# Simple Harness — 从源码启动整个应用（macOS / Linux）。
# 前置：先跑过一次 ./scripts/setup.sh
#
# 一条命令拉起全部：tauri dev 启动 Rust 壳 + vite 前端；
# Rust 侧 process_manager 通过 DESKPET_BACKEND_DIR 自动用
# backend/.venv/bin/python 拉起 Python 后端，退出时连带回收。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -x "$ROOT/backend/.venv/bin/python" ]; then
  printf '\033[31m[dev] backend/.venv 不存在 — 先跑 ./scripts/setup.sh\033[0m\n' >&2
  exit 1
fi
if [ ! -d "$ROOT/tauri-app/node_modules" ]; then
  printf '\033[31m[dev] tauri-app/node_modules 不存在 — 先跑 ./scripts/setup.sh\033[0m\n' >&2
  exit 1
fi

# 显式指向源码 backend（优先级最高，覆盖编译期 fallback 路径）。
export DESKPET_BACKEND_DIR="$ROOT/backend"

cd "$ROOT/tauri-app"
exec npm run tauri:dev
