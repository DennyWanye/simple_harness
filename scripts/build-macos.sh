#!/bin/bash
# macOS（Apple 芯片）试用安装包：冻结后台 → 冒烟启动 → 打 .app 与 .dmg（2026-10-08 起）。
#
# 用法（仓库根目录）：scripts/build-macos.sh
# 产物：tauri-app/src-tauri/target/release/bundle/dmg/SimpleHarness_<版本>_aarch64.dmg
#
# 做的事（任何一步失败即停）：
#   1. 取钉死版本的无界面 Chrome（已有且校验通过就不再下载），放在 ~/Library/Caches/DPW
#   2. PyInstaller 冻结后台到 backend/dist/deskpet-backend（不带本地模型：DESKPET_BUNDLE_MODELS=0）
#   3. 核对冻结包里的浏览器与 Playwright；在一次性数据目录上启动冻结后台，等到 "startup complete"
#   4. tauri build 加载发布专用配置 tauri.macos-release.conf.json（把冻结后台放进 .app 的 Resources/backend）
#
# 未做：苹果签名与公证（没有开发者账号；首次打开要右键"打开"）、自动更新（updater 未启用）。
# 不读写 .env、不打印密钥；冒烟用的数据目录在临时目录，结束即删。
set -euo pipefail
REPO=$(cd "$(dirname "$0")/.." && pwd)
PY=$REPO/backend/.venv/bin/python
CACHE=${DESKPET_BROWSER_CACHE:-$HOME/Library/Caches/DPW/pw-161-1228}
[ "$(uname -s)" = Darwin ] || { echo "只在 macOS 上运行"; exit 1; }
[ "$(uname -m)" = arm64 ] || { echo "目前只支持 Apple 芯片（arm64）"; exit 1; }

echo "== 1/4 浏览器组件"
mkdir -p "$CACHE"
"$PY" "$REPO/scripts/acquire_playwright_browser.py" --cache-root "$CACHE" --json >/dev/null

echo "== 2/4 冻结后台（几分钟）"
cd "$REPO/backend"
rm -rf dist/deskpet-backend build/deskpet-backend
DESKPET_BUNDLE_MODELS=0 PLAYWRIGHT_BROWSERS_PATH="$CACHE/playwright-browsers" PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
  "$PY" -m PyInstaller deskpet-backend.spec --noconfirm --clean --distpath dist --workpath build >"$REPO/backend/build/pyinstaller-macos.log" 2>&1 \
  || { echo "PyInstaller 失败，见 backend/build/pyinstaller-macos.log"; exit 1; }
OUT=$REPO/backend/dist/deskpet-backend
[ -x "$OUT/deskpet-backend" ] || { echo "没有产出 $OUT/deskpet-backend"; exit 1; }

echo "== 3/4 冻结包核对与冒烟启动"
"$PY" "$REPO/scripts/assert_playwright_packaging.py" "$OUT" --json \
  --tauri-config "$REPO/tauri-app/src-tauri/tauri.macos-release.conf.json" >/dev/null
SMOKE=$(cd "$(mktemp -d)" && pwd -P)  # /var 是软链接，状态库拒绝含软链接的路径
PORT=${DESKPET_SMOKE_PORT:-18765}
lsof -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null && { echo "冒烟端口 $PORT 被占用"; exit 1; }
DESKPET_USER_DATA_DIR="$SMOKE/userdata" DESKPET_USER_LOG_DIR="$SMOKE/logs" DESKPET_USER_LOG="$SMOKE/logs/backend.log" \
  DESKPET_USER_CACHE_DIR="$SMOKE/cache" DESKPET_BACKEND_PORT="$PORT" \
  "$OUT/deskpet-backend" >"$SMOKE/backend.out" 2>&1 &
BACKEND_PID=$!
ok=0
for _ in $(seq 1 90); do
  if grep -q "startup complete" "$SMOKE/backend.out" 2>/dev/null; then ok=1; sleep 5; break; fi
  kill -0 "$BACKEND_PID" 2>/dev/null || break
  sleep 2
done
# "启动完成"不够：编排服务、成长功能等子系统失败只记一条 error、后台照样起来（2026-10-08 实测
# 漏过）。在停后台之前看，关后台时 MCP 收尾的报错栈不算。
if grep -qE "orchestration service failed to start|growth_authority_cutover_failed|Traceback" "$SMOKE/backend.out"; then ok=0; fi
kill -TERM "$BACKEND_PID" 2>/dev/null || true
for _ in $(seq 1 15); do kill -0 "$BACKEND_PID" 2>/dev/null || break; sleep 1; done
kill -KILL "$BACKEND_PID" 2>/dev/null || true
if [ "$ok" != 1 ]; then
  cp "$SMOKE/backend.out" "$REPO/backend/build/smoke-macos.out"
  rm -rf "$SMOKE"
  echo "冻结后台没能启动，输出见 backend/build/smoke-macos.out"; exit 1
fi
rm -rf "$SMOKE"

echo "== 4/4 打 .app 与 .dmg"
cd "$REPO/tauri-app"
npx tauri build --bundles app --config src-tauri/tauri.macos-release.conf.json </dev/null
# .dmg 用系统 hdiutil 直接做：Tauri 的 bundle_dmg.sh 要驱动 Finder 摆图标，无人值守时常失败。
BUNDLE=$REPO/tauri-app/src-tauri/target/release/bundle
APP=$BUNDLE/macos/SimpleHarness.app
VERSION=$(python3 -c "import json;print(json.load(open('$REPO/tauri-app/src-tauri/tauri.conf.json'))['version'])")
DMG=$BUNDLE/dmg/SimpleHarness_${VERSION}_aarch64.dmg
STAGE=$(mktemp -d)
cp -cR "$APP" "$STAGE/"            # APFS 克隆，不占空间
ln -s /Applications "$STAGE/Applications"
mkdir -p "$BUNDLE/dmg"
rm -f "$DMG"
hdiutil create -volname "SimpleHarness" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"
ls -la "$DMG"
