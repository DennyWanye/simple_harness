#!/bin/bash
# 发布内嵌 SDK 的一个 opt 版本并让 Host 钉住它（开发期流程，2026-10-01 从会话临时脚本固化）。
#
# 用法：scripts/release_sdk_opt.sh <旧 opt 号> <新 opt 号> "<SDK 提交说明>" <独立核验回执.json>
# 例：  scripts/release_sdk_opt.sh 112 113 "SDK opt.113：删旧规划协议路径" .local-test-evidence/2026-10-06/taskgraph-gate/review-181200.json
# 独立核验回执由 sdk/simple-harness-sdk/scripts/acceptance/write_review_receipt.py 在评估员写完评估后生成；
# 原计划 §16 不豁免 INDEPENDENT_REVIEW，没有回执验收门不过、不发版。
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
OLD=${1:?旧 opt 号}; NEW=${2:?新 opt 号}; MSG=${3:?SDK 提交说明}; REVIEW=${4:?独立核验回执 json}
GIT=${GIT:-git}; command -v "$GIT" >/dev/null || GIT=/Library/Developer/CommandLineTools/usr/bin/git
[ -f "$REVIEW" ] || { echo "独立核验回执不存在：$REVIEW"; exit 1; }
REPO=$(cd "$(dirname "$0")/.." && pwd)
SDK=$REPO/sdk/simple-harness-sdk
PREFIX="0.13.0.dev20260925+opt"
UPSTREAM=$REPO/.local-test-evidence/2026-10-05/f2-upstream/evidence.json

cd "$SDK"
sed -i '' "s/+opt\\.$OLD\"/+opt.$NEW\"/" src/agent_orchestrator/version.py src/simple_harness/version.py
grep -q "+opt.$NEW\"" src/agent_orchestrator/version.py || { echo "版本号没有改成 opt.$NEW（当前不是 opt.$OLD？）"; exit 1; }
uv run --frozen python scripts/build/taskgraph_manifest.py generate --upstream "$UPSTREAM" >/dev/null
# 执行图部署验收门（原计划 §0.3 / §16，补齐第 1 批 V12）：改坏 M01～M12 + 数据表守护 + 执行图用例 +
# 随机序列全过才把清单写成 VALIDATED；Host 启动只认 VALIDATED。Host 接缝用例在下面钉版后跑。
GATE_OUT=$(uv run --frozen python scripts/acceptance/taskgraph_gate.py --upstream "$UPSTREAM" --review "$(cd "$REPO" && realpath "$REVIEW")" | tail -1)
GATE=$(printf '%s' "$GATE_OUT" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["gate"] if d["status"]=="PASS" else "")')
[ -n "$GATE" ] || { echo "执行图验收门没通过，不发版："; echo "$GATE_OUT"; exit 1; }
uv run --frozen python scripts/build/taskgraph_manifest.py validate --gate "$GATE" >/dev/null
# 保证通道三份接缝（收尾写方、四个消费者、重启恢复）：改过收尾与消费者后证据要重跑（2026-10-07 夜间 N3-16）
for seam in final-writer four-consumer recovery; do
  SEAM_OUT=$(uv run --frozen scripts/assurance_seams/$seam-seam.py 2>&1 | tail -1)
  printf '%s' "$SEAM_OUT" | python3 -c 'import json,sys; sys.exit(0 if json.load(sys.stdin).get("status")=="PASS" else 1)' \
    || { echo "接缝 $seam 没通过，不发版："; echo "$SEAM_OUT"; exit 1; }
done
find src tests -name __pycache__ -prune -exec rm -rf {} +

cd "$REPO"
$GIT add -A sdk/simple-harness-sdk ARCHITECTURE plans/2026-09-27-desktop-next
$GIT reset -q .local-test-evidence/2026-09-28/batch7/watch_steps.py 2>/dev/null || true
$GIT commit -qm "$MSG"
COMMIT=$($GIT rev-parse HEAD)

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
$GIT rm -q "vendor/simple_harness_sdk-$PREFIX.$OLD-py3-none-any.whl" "vendor/simple_harness_sdk-$PREFIX.$OLD.candidate-manifest.json"
$GIT add -f "$WHEEL" "$MANIFEST"
uv lock --offline >/dev/null 2>&1
uv sync --inexact --offline >/dev/null 2>&1
uv run --frozen python -m pytest -q -p no:cacheprovider tests/sdk_adapters/test_sdk_source_identity.py \
  tests/sdk_adapters/test_sdk_candidate.py tests/orchestration/test_deployment_manifest.py \
  tests/sdk_adapters/test_composition.py \
  tests/orchestration/test_taskgraph_bound_at_creation.py tests/orchestration/test_taskgraph_execution_reads.py \
  tests/orchestration/test_taskgraph_operator_verbs.py tests/orchestration/test_ws_taskgraph_routing.py \
  tests/orchestration/test_support_export_taskgraph_history.py \
  tests/orchestration/test_recovery_degraded_host.py tests/orchestration/test_recovery_isolated_host.py \
  tests/orchestration/test_global_budget_setting.py tests/orchestration/test_chat_mission_amend.py \
  tests/orchestration/test_assurance_quarantine.py \
  tests/orchestration/test_contract_projection.py tests/orchestration/test_projection.py \
  tests/orchestration/test_assurance_host_api.py \
  tests/orchestration/test_diagnostics_contract.py tests/orchestration/test_mission_diagnostics.py 2>&1 | tail -1

cd "$REPO"
$GIT add -A backend/pyproject.toml backend/uv.lock backend/deskpet/sdk_adapters/sdk_candidate.py backend/vendor
$GIT commit -qm "Host 钉 SDK opt.$NEW"
# 密钥扫描：只看这次发布改动且仍存在的文件；"task-<编号>" 里的 sk- 不算（前面不能是字母）。
HITS=$($GIT diff HEAD~2 --name-only --diff-filter=d -z \
  | xargs -0 grep -lE "(^|[^A-Za-z])sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|BEGIN (RSA|EC|OPENSSH) PRIVATE" 2>/dev/null || true)
if [ -n "$HITS" ]; then
  echo "发现疑似密钥，未推送："; echo "$HITS"; exit 1
fi
$GIT push -q origin main
$GIT log --oneline -2
