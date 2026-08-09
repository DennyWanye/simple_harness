# WBUI-DEF-BUILD-01 —— capability_build 真实调用在 admit 阶段崩溃并掀翻宿主 run

发现于：r9 / WBUI-S06 判定项④ 路1 取证（2026-08-09）
严重度：中 —— LLM 能力生成链路完全不可用；且失败不是降级为工具错误反馈，
而是 driver_runtime_failed 把整轮对话 run 掀翻（用户视角：消息挂掉无回复）。

## 现象
会话中让 pet 生成新技能 → LLM 依次调用 capability_search（成功）→ capability_build
→ backend 崩溃链：
  subagent_registry.py:378 _workflow_spawn → builder.py:1367 admit
  → builder.py:1145 _verify_search → builder.py:979 extract_search_attestation
  → raise CapabilityBuildError → harness/runtime.py driver_runtime_failed
capability_operations 表零新增（没走到 create_operation）。

## 分析
builder.admit 要求会话历史里存在符合规范的 capability_search attestation；
kimi-k3 实际调用了 capability_search，但 canonical_messages 中的记录不满足
extract_search_attestation 的提取条件（格式/位置断言失败）。
两个问题：
  1. attestation 提取对真实 LLM 调用序列过于脆弱（search 明明发生了却提取不到）
  2. CapabilityBuildError 未被 capability_build 工具边界捕获降级为工具错误结果，
     直接穿透到 driver 层掀翻整轮 run——违反"单工具失败不应终结对话"的预期
（能力中心里 capability_build 健康=「降级 · Instructional or currently unavailable
for direct execution」与此互为印证。）

## 修复方向（建议，未实施）
- extract_search_attestation 失败时返回结构化工具错误（提示模型先完成合规 search），
  而非抛异常穿透；
- driver 对 prepare_control/delegated 路径的异常加边界，降级为该 tool_call 失败。
