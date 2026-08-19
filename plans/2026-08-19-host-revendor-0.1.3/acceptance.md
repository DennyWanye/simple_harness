# 验收标准：宿主 re-vendor simple-harness-sdk 0.1.3

> 状态：待确认
> 仓库：`simple_harness`（宿主，仅依赖切换）；0.1.3 wheel 来自 `simple-harness-sdk` 已发布（SHA 81025b2c…）
> 来源：接续 sdk-consumer-0.1.3 program（消费者层两个缺陷已修并发 0.1.3）

## 范围

**包含**（单垂直切面：依赖切换）：
- vendor 0.1.3 wheel + `sdk_candidate.py` SSOT 三行常量更新（验证单一事实源：切换只改一处）
- `pyproject.toml` / `uv.lock` 切到 0.1.3
- 回归 + conformance + 真机冒烟（full-surface，改启动装配常量）

**明确不包含**：消费者层 API 的宿主侧使用（宿主走 10-Port 全量 API，不消费 `build_consumer_runtime`）；SDK 仓库改动。

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|----|--------|-------------------|--------|
| V-AC-1 | vendor 0.1.3 | `backend/vendor/simple_harness_sdk-0.1.3-py3-none-any.whl` SHA-256 = `81025b2c…a7b9`；pyproject/uv.lock 指向 0.1.3 | 必须 |
| V-AC-2 | SSOT 单点切换 | 只改 `sdk_candidate.py` 三行常量即完成全链路版本切换（grep 确认无 0.1.2 残留硬编码） | 必须 |
| V-AC-3 | 兼容 | 0.1.3 对 0.1.2 纯新增（api_compat_check 三维无删除）；宿主 10-Port 适配零改动 | 必须 |
| V-AC-4 | 无回归 | 18 分片回归零新增红；conformance 22/22 | 必须 |
| V-AC-5 | 真机可用 | 启动 `sdk_runtime_ready sdk_version=0.1.3`；主聊天真 DeepSeek 回复（真机验证） | 必须 |

## 非功能 / 边界
- 向后兼容：宿主 10-Port 用法零改动；fail-closed 校验（verify_sdk_candidate）不削弱
- 文档：vendor/README + ARCHITECTURE 回写 0.1.3 active

## 测试场景矩阵
`input_sensitive=false`（依赖切换，非 LLM 语义功能）；`stateful_init=true`（改启动装配常量，含冷启动场景）。

| scenario_id | input_class | primary_risk | gate_type | required | manual_required | terminal_expectation |
|---|---|---|---|---|---|---|
| V-COLD | 冷启动 | 启动装配在 0.1.3 下可用 | positive-value | 是 | 是（真机） | sdk_runtime_ready 0.1.3 + 聊天回复 |

## 测试义务矩阵
| obligation_id | type | ac_id | risk | min_decisive_test | required_reason |
|---|---|---|---|---|---|
| TO-V1 | delivery | V-AC-1 | — | sha256sum 核对 vendor/dist | 证明 vendor 正确 bytes |
| TO-V2 | delivery | V-AC-2 | — | grep 0.1.2 残留 | 证明 SSOT 单点切换 |
| TO-V3 | delivery | V-AC-3 | — | api_compat_check 0.1.2→0.1.3 | 证明纯新增 |
| TO-V4 | delivery | V-AC-4 | — | 18 分片回归 + conformance | 证明无回归 |
| TO-V5 | delivery | V-AC-5 | — | V-COLD 真机 | 证明真机可用 |
| TO-R1 | change-risk | — | FAIL-REGRESSION | full-surface smoke | 改启动装配常量 |

## 完成的定义
1. 5 条 V-AC 全过；2. 三义务矩阵 PASS；3. 宿主 git 干净 + ARCHITECTURE 回写；4. gate finalize exit 0
