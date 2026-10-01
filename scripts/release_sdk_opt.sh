#!/bin/bash
# 发布内嵌 SDK 的一个 opt 版本并让 Host 钉住它（开发期流程，2026-10-01 从会话临时脚本固化）。
#
# 用法：scripts/release_sdk_opt.sh <旧 opt 号> <新 opt 号> "<SDK 提交说明>"
# 例：  scripts/release_sdk_opt.sh 112 113 "SDK opt.113：删旧规划协议路径"
#
# 做的事（任何一步失败即停）：
#   1. 两个 version.py 改成新 opt 号；重新生成部署清单；清 __pycache__
#   2. 提交 SDK 源码 + ARCHITECTURE + 当前主计划目录的文档
#   3. 构建候选 wheel 到 backend/vendor
#   4. Host 钉版（sdk_candidate.py、pyproject.toml、uv.lock、vendor 换文件），跑 64 条钉版测试
#   5. 提交"Host 钉 SDK opt.N"，密钥扫描，推送 origin main
#
# 约定：只在主检出（main）上运行；真机局跑到一半时不要运行（会换掉后端正在用的 wheel）。
set -euo pipefail
OLD=${1:?旧 opt 号}; NEW=${2:?新 opt 号}; MSG=${3:?SDK 提交说明}
REPO=$(cd "$(dirname "$0")/.." && pwd)
SDK=$REPO/sdk/simple-harness-sdk
PREFIX="0.13.0.dev20260925+opt"
UPSTREAM=$REPO/.local-test-evidence/2026-09-27/batch2a-upstream/evidence.json

cd "$SDK"
sed -i '' "s/+opt\\.$OLD\"/+opt.$NEW\"/" src/agent_orchestrator/version.py src/simple_harness/version.py
grep -q "+opt.$NEW\"" src/agent_orchestrator/version.py || { echo "版本号没有改成 opt.$NEW（当前不是 opt.$OLD？）"; exit 1; }
uv run --frozen python scripts/build/taskgraph_manifest.py generate --upstream "$UPSTREAM" >/dev/null
find src tests -name __pycache__ -prune -exec rm -rf {} +

cd "$REPO"
git add -A sdk/simple-harness-sdk ARCHITECTURE plans/2026-09-27-desktop-next
git reset -q .local-test-evidence/2026-09-28/batch7/watch_steps.py 2>/dev/null || true
git commit -qm "$MSG"
COMMIT=$(git rev-parse HEAD)

cd "$SDK"
uv run --frozen python scripts/build/development_candidate.py --output ../../backend/vendor 2>&1 | tail -1

cd "$REPO/backend"
WHEEL="vendor/simple_harness_sdk-$PREFIX.$NEW-py3-none-any.whl"
MANIFEST="vendor/simple_harness_sdk-$PREFIX.$NEW.candidate-manifest.json"
WSHA=$(shasum -a 256 "$WHEEL" | cut -d' ' -f1)
MSHA=$(shasum -a 256 "$MANIFEST" | cut -d' ' -f1)
python3 - "$OLD" "$NEW" "$WSHA" "$MSHA" "$COMMIT" <<'PY'
import re, sys
old, new, wsha, msha, commit = sys.argv[1:]
p = "deskpet/sdk_adapters/sdk_candidate.py"; s = open(p).read()
s = s.replace(f"+opt.{old}", f"+opt.{new}")
s = re.sub(r'SDK_WHEEL_SHA256 = "[0-9a-f]{64}"', f'SDK_WHEEL_SHA256 = "{wsha}"', s, 1)
s = re.sub(r'SDK_CANDIDATE_MANIFEST_SHA256 = "[0-9a-f]{64}"', f'SDK_CANDIDATE_MANIFEST_SHA256 = "{msha}"', s, 1)
s = re.sub(r'SDK_SOURCE_COMMIT = "[0-9a-f]{40}"', f'SDK_SOURCE_COMMIT = "{commit}"', s, 1)
open(p, "w").write(s)
p = "pyproject.toml"; s = open(p).read(); open(p, "w").write(s.replace(f"+opt.{old}", f"+opt.{new}"))
PY
git rm -q "vendor/simple_harness_sdk-$PREFIX.$OLD-py3-none-any.whl" "vendor/simple_harness_sdk-$PREFIX.$OLD.candidate-manifest.json"
git add -f "$WHEEL" "$MANIFEST"
uv lock --offline >/dev/null 2>&1
uv sync --inexact --offline >/dev/null 2>&1
uv run --frozen python -m pytest -q -p no:cacheprovider tests/sdk_adapters/test_sdk_source_identity.py \
  tests/sdk_adapters/test_sdk_candidate.py tests/orchestration/test_deployment_manifest.py \
  tests/sdk_adapters/test_composition.py 2>&1 | tail -1

cd "$REPO"
git add -A backend/pyproject.toml backend/uv.lock backend/deskpet/sdk_adapters/sdk_candidate.py backend/vendor
git commit -qm "Host 钉 SDK opt.$NEW"
if git diff HEAD~2 --name-only | xargs grep -lE "sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|BEGIN (RSA|EC|OPENSSH) PRIVATE" 2>/dev/null; then
  echo "发现疑似密钥，未推送"; exit 1
fi
git push -q origin main
git log --oneline -2
