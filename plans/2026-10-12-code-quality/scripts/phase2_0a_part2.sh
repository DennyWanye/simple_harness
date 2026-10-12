#!/bin/bash
# 阶段 2.0a（二）一键重做：连接登记表 + 启动装配五段搬出主入口。从提交 57670cbb2 的 main.py 出发。
set -euo pipefail
cd "$(dirname "$0")/../../.."
P=backend/.venv/bin/python
M=backend/main.py
RUFF=${RUFF:-../simple_harness/sdk/simple-harness-sdk/.venv/bin/ruff}
ln() { grep -n "$1" $M | head -1 | cut -d: -f1; }
mv_() { $P scripts/code_quality/move_block.py $M "$1" "$2" --doc "$3"; }

S=$(ln "^_control_connections: dict"); E=$(ln '^    "message-panel-main": "default",'); E=$((E+1))
mv_ backend/host/connections.py $S-$E "宿主进程的连接登记表：控制通道连接、按连接的检视查询任务、聊天对端分组。只改内容、不重新绑定。"
S=$(ln "^async def _restart_orchestration"); E=$(ln "^# --- Register providers ---")
mv_ backend/host/settings_persistence.py $S-$((E-1)) "设置页触发的持久化与重启：重启编排服务、把监工开关写回 config.toml。"
S=$(ln "^# --- Register providers ---"); E=$(ln "^# P4-S17: SessionDB is the single conversation source")
mv_ backend/host/llm_providers.py $((S+1))-$((E-1)) "模型提供方的启动装配：设置页的运行时覆盖、密钥解析、本地与云端提供方、计费账本、混合路由。"
S=$(ln "^memory_store = None"); E=$(ln "^# S8 (R9): log the current hardware tier")
mv_ backend/host/tools_bootstrap.py $S-$((E-1)) "工具层的启动装配：基础工具注册表、v2 工具注册表、权限与自动模式、插件、能力范围存储与解析器。"
S=$(ln "^_tier = classify_tier()"); E=$(ln "^# 记忆系统升级 WI-M1.2/M1.7")
mv_ backend/host/voice_bootstrap.py $S-$((E-1)) "启动时记录硬件档位，并按配置装好旧版语音运行时。"
S=$(ln "^def _make_str_llm_call"); E=$(ln "^_companion_ingress_tasks")
mv_ backend/host/llm_calls.py $S-$((E-1)) "模型调用的构造与刷新：给事实抽取、深度研究、图像接口用的调用函数，以及设置变化后的重新接线。"

NEW="backend/host/connections.py backend/host/settings_persistence.py backend/host/llm_providers.py backend/host/tools_bootstrap.py backend/host/voice_bootstrap.py backend/host/llm_calls.py"
# 新模块里随导入块搬来、本模块不用的导入副本（用到的那份仍在主入口）
$RUFF check --config scripts/code_quality/ruff.toml --select F401 --fix -q $NEW || true
$RUFF check --config scripts/code_quality/ruff.toml --select I001 --fix -q $M backend/host
$RUFF format --config scripts/code_quality/ruff.toml -q $M backend/host
