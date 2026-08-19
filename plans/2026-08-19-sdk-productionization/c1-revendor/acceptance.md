# 验收标准：消费侧卫生 + 重新 vendor（C1）

> 状态：DRAFT
> 仓库：`simple_harness`（宿主；两个 SDK 的代码改动已在 H1/M1-M4 完成）
> 来源：SDK 生产化 program Slice C1

## 范围

- 重新 vendor harness SDK 0.1.4 + memory SDK 0.2.0 成品 wheel
- 版本单一来源（`sdk_candidate.py` 单点，含 memory SDK 身份）
- backend 依赖 + `uv.sources` 指向新 wheel

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|----|--------|-------------------|--------|
| C1-AC-1 | 重新 vendor | `backend/vendor/` 含 `simple_harness_sdk-0.1.4` 与 `simple_harness_memory_sdk-0.2.0` wheel，SHA256 与 `sdk_candidate.py` 一致 | 必须 |
| C1-AC-2 | 版本单一来源 | `sdk_candidate.py` 的 `SDK_VERSION`/`SDK_MEMORY_VERSION` 与 installed 的 `simple_harness.__version__`/`simple_harness_memory.__version__` 一致 | 必须 |
| C1-AC-3 | backend 集成 | `uv.sources` 指向新 wheel；import 冒烟通过；`verify_sdk_candidate` 返回 identity | 必须 |

## 适用性声明（APPLICABILITY_DECLARATION）

- `input_sensitive=false`：版本 bump + vendor，无 LLM 语义功能。
- `llm_payload_driven=false`：无 LLM 输出驱动端侧状态机。
- `stateful_init=false`：无异步注册服务/登录态依赖。

## 完成的定义（DoD 摘要）

1. 3 条 C1-AC 全部通过（verify 脚本 + import 冒烟）
2. `simple_harness` git status 干净
3. gate finalize exit 0，receipt 入账
