#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 DennyWanye
# SPDX-License-Identifier: BUSL-1.1
#
# Simple Harness — 一次性开发环境安装（macOS / Linux）。
# 用法：git clone 后在仓库根目录执行 ./scripts/setup.sh
# 之后每次启动只需 ./scripts/dev.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

err() { printf '\033[31m[setup] %s\033[0m\n' "$*" >&2; }
info() { printf '\033[36m[setup] %s\033[0m\n' "$*"; }
ok() { printf '\033[32m[setup] %s\033[0m\n' "$*"; }

missing=0

# ── Node.js ≥ 20 ──
if command -v node >/dev/null 2>&1; then
  node_major="$(node -p 'process.versions.node.split(".")[0]')"
  if [ "$node_major" -lt 20 ]; then
    err "Node.js >= 20 required (found $(node --version))"
    missing=1
  fi
else
  err "Node.js not found. Install: https://nodejs.org (or brew install node / apt install nodejs)"
  missing=1
fi

# ── Rust toolchain ──
if ! command -v cargo >/dev/null 2>&1; then
  err "Rust toolchain not found. Install: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh"
  missing=1
fi

# ── uv (Python package manager; also provisions Python 3.11+) ──
if ! command -v uv >/dev/null 2>&1; then
  err "uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
  missing=1
fi

# ── Linux: Tauri system libraries ──
if [ "$(uname -s)" = "Linux" ]; then
  if command -v pkg-config >/dev/null 2>&1; then
    if ! pkg-config --exists webkit2gtk-4.1 2>/dev/null; then
      err "Missing webkit2gtk. Debian/Ubuntu: sudo apt install libwebkit2gtk-4.1-dev build-essential \\"
      err "  libssl-dev libayatana-appindicator3-dev librsvg2-dev"
      err "Fedora: sudo dnf install webkit2gtk4.1-devel openssl-devel libappindicator-gtk3-devel librsvg2-devel"
      missing=1
    fi
  else
    err "pkg-config not found — install your distro's Tauri prerequisites first:"
    err "  https://tauri.app/start/prerequisites/#linux"
    missing=1
  fi
fi

[ "$missing" -ne 0 ] && { err "先装好上面缺的依赖再重跑 ./scripts/setup.sh"; exit 1; }

info "installing frontend deps (tauri-app/) ..."
(cd tauri-app && npm install)

info "syncing backend venv (backend/, first run downloads torch — 会比较久) ..."
(cd backend && uv sync)

ok "done. 启动应用：./scripts/dev.sh"
