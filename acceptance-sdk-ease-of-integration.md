# 验收标准：SDK 易接入性改进（v0.1.1 → v0.1.2）

> 状态：DRAFT（待用户确认）
> 父级 Program：Simple Harness SDK v0.1 提取（见 `plans/2026-08-13-simple-harness-sdk/acceptance.md`）
> 本次聚焦：补齐 SDK-AC-6/AC-7/AC-8 的剩余差距，达成"其他项目（如 AI Phone）可方便接入"的目标

## 范围

**包含**：
- 删除旧 Workflow 路由器（`ModelPersonalWorkflowMatcher`），实现 SDK-AC-6 Agent 统一选择
- 补齐三个官方 Workflow 的 Host Ports（durable-task/personal/capability），实现 SDK-AC-7 完整可用性
- 实现 Conformance 测试套件（从占位符变为可执行），实现 SDK-AC-8 消费者验证能力
- 编写完整消费者文档（Quickstart、Integration Guide、API Reference、Migration Guide）
- 提供参考实现示例（minimal consumer example）
- Simple Harness 产品完成真实切换验证（作为第一消费者）

**明确不包含**：
- SDK 核心功能开发（AC-1/AC-2/AC-3/AC-4/AC-5 已完成）
- AI Phone 移动端部署（仅提供 Handoff 文档）
- 跨平台远程验证（Windows x64、Linux ARM64 等待单独批准）
- 产品级冷启动路径（SR-9 已移至 follow-up）

## 功能验收条款

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|----|--------|-------------------|--------|
| **EI-AC-1** | 删除独立 Workflow Matcher | `ModelPersonalWorkflowMatcher` 相关代码已删除；`main.py` 不再构造该 matcher；Personal Workflow 选择由 `agent.general` 通过 catalog 完成；3 个 Personal Workflow 场景测试通过（Agent 自主选择正确候选）| 必须 |
| **EI-AC-2** | 补齐 Workflow Host Ports | `WorkflowRuntimeDriver` 注入所需 Host ports（personal_catalog/capability_catalog/workspace/artifact）；`personal_v1` 有公开 profile factory；`capability_build` 支持条件注册（ports 齐全时自动可用）；Simple Harness 通过 ports 完成真实 Personal/Capability Workflow 端到端 | 必须 |
| **EI-AC-3** | Conformance 测试套件实现 | `simple_harness.testing.run_conformance_suite()` 从占位符变为可执行；至少覆盖 5 个核心场景（Provider/Tool/Runtime/Workflow/Persistence）；CLI `python -m simple_harness.testing.cli run-conformance` 返回结构化报告（PASS/FAIL + 详情）；Simple Harness 作为消费者通过 conformance | 必须 |
| **EI-AC-4** | Quickstart 文档 | 编写 `docs/quickstart.md`（≤500行）：包含安装、最小示例（10行代码跑通 Agent）、常见问题；至少 1 个外部审阅者按文档从零跑通 | 必须 |
| **EI-AC-5** | Integration Guide | 编写 `docs/integration-guide.md`（≤1000行）：详细说明如何实现必要 Ports（Provider/Tool/Workspace/Authorization/等），提供每个 Port 的接口定义 + 伪代码示例；覆盖三个官方 Workflow 的接入路径 | 必须 |
| **EI-AC-6** | API Reference 补全 | 补全 `docs/api/` 目录：除已有 `contracts.md` 外，新增 `runtime.md`（Runtime/Kernel API）、`workflow.md`（三个官方 Workflow API）、`ports.md`（必须实现的 Port 接口清单）；每个公开类/函数有 docstring | 必须 |
| **EI-AC-7** | Minimal Consumer Example | 在 SDK 仓库提供 `examples/minimal-consumer/`：<100 行代码演示如何接入 SDK（fake Provider + 1个 Tool + 简单对话）；可独立运行（`python examples/minimal-consumer/main.py`）；README 说明每部分作用 | 必须 |
| **EI-AC-8** | Memory Port 接口定义 | 定义 `MemoryPort` 接口（短期记忆/长期记忆/实体记忆的读写契约）；在 `docs/api/ports.md` 中文档化；Runtime 通过 Port 调用 Memory（可选依赖）；保留扩展点以便后续接入独立 Memory SDK | 必须 |
| **EI-AC-9** | AI Phone Handoff 文档 | 编写 `docs/aiphone-handoff.md`：说明 AI Phone 团队需要实现的 Adapter 清单、数据迁移注意事项、移动端特殊约束（内存/存储/权限）；包含架构图 + 检查清单 | 必须 |

## 非功能与边界

- **文档质量**：所有文档必须经过拼写检查、格式一致、代码示例可运行
- **向后兼容**：v0.1.1 → v0.1.2 不破坏已有公开 API（删除 `ModelPersonalWorkflowMatcher` 不算破坏，因为它属于产品内部实现）
- **性能**：Conformance 测试套件全量运行 ≤5 分钟（单机 macOS ARM64）
- **错误提示**：Conformance 失败时提供清晰错误信息（指出哪个 Port 未实现/哪个契约违反）
- **可审计性**：所有代码变更有对应 CHANGELOG 条目

## Assurance Contract 摘要

- **Profile**: standard（信任开发者账户、OS/kernel、系统路径程序）
- **受保护资产**:
  - ASSET-1: SDK 公开 API 的稳定性（消费者依赖）
  - ASSET-2: Conformance 测试的准确性（消费者验证工具）
  - ASSET-3: 文档的正确性（消费者集成指南）
- **可信假设**:
  - TRUST-1: 消费者按文档实现 Ports（SDK 不防御恶意 Port 实现）
  - TRUST-2: 开发环境网络可达 PyPI（安装依赖）
  - TRUST-3: Python 3.11+ 环境可用
- **范围内失败/对手**:
  - FAIL-1: 文档示例代码无法运行
  - FAIL-2: Conformance 测试误报（实际正确但报 FAIL）
  - FAIL-3: Conformance 测试漏报（实际错误但报 PASS）
  - FAIL-4: 缺少必要 Port 接口文档导致消费者无法实现
  - FAIL-5: Agent 无法自主选择正确 Workflow（仍依赖产品路由）
- **明确范围外条件**:
  - OOS-1: 恶意消费者故意破坏 SDK（不防御）
  - OOS-2: Python 版本 < 3.11（不支持）
  - OOS-3: 网络完全隔离环境（无法安装依赖）
  - OOS-4: 移动端性能优化（AI Phone 自行处理）
- **最大可接受影响**: 消费者集成失败，需人工介入排查（不应导致数据丢失或安全问题）

## 测试义务矩阵（Test Obligation Matrix）

| obligation_id | type | ac_id | risk | min_decisive_test | required_reason |
|---------------|------|-------|------|-------------------|-----------------|
| **TO-E1** | delivery | EI-AC-1 | — | Personal Workflow 场景：Agent 从 2 个候选中选择正确的 | 直接证明 AC-1 的主要功能 |
| **TO-E2** | delivery | EI-AC-1 | — | 验证 `ModelPersonalWorkflowMatcher` 相关代码已删除 | 证明旧实现已清理 |
| **TO-E3** | delivery | EI-AC-2 | — | Personal Workflow 端到端（通过 Port 读取候选列表）| 证明 personal_catalog Port 可用 |
| **TO-E4** | delivery | EI-AC-2 | — | Capability Workflow 端到端（通过 Port 查询能力目录）| 证明 capability_catalog Port 可用 |
| **TO-E5** | delivery | EI-AC-3 | — | 运行 conformance CLI，验证返回结构化报告 | 证明 conformance 可执行 |
| **TO-E6** | delivery | EI-AC-3 | — | Simple Harness 通过 conformance suite | 证明第一消费者合规 |
| **TO-E7** | delivery | EI-AC-4 | — | 外部审阅者按 Quickstart 从零跑通 | 证明文档可用性 |
| **TO-E8** | delivery | EI-AC-5 | — | Integration Guide 覆盖所有必要 Ports | 证明文档完整性 |
| **TO-E9** | delivery | EI-AC-6 | — | 所有公开类/函数有 docstring | 证明 API 文档完整 |
| **TO-E10** | delivery | EI-AC-7 | — | Minimal consumer example 可独立运行 | 证明示例代码正确 |
| **TO-E11** | delivery | EI-AC-8 | — | AI Phone Handoff 包含架构图 + 检查清单 | 证明交付物完整 |
| **TO-R1** | change-risk | EI-AC-1 | FAIL-ROUTE | Simple Harness 全表面冒烟（所有入口） | 删除 matcher 可能影响路由 |
| **TO-R2** | change-risk | EI-AC-2 | FAIL-REGRESSION | 已有 Workflow 回归测试 | 新增 Ports 可能破坏既有功能 |
| **TO-R3** | change-risk | — | FAIL-INTEGRATION | SDK wheel 可安装（clean venv） | 确保依赖声明正确 |

**类型说明**：
- `delivery`：直接证明 MUST AC，required
- `change-risk`：防范本次改动的受影响范围内风险，有明确风险时 required
- `exploratory`：探索性测试，不 required（本次无）

## 完成的定义（DoD 摘要）

1. ✅ 全部 8 条 EI-AC 通过测试
2. ✅ 所有 delivery 类型的 test obligation 都有对应的 PASS testcase
3. ✅ 所有 change-risk 类型的 test obligation 都有对应的 PASS testcase
4. ✅ Simple Harness 产品通过 conformance suite
5. ✅ 文档审阅：至少 1 个外部审阅者确认 Quickstart 可用
6. ✅ 无回归：Simple Harness 全表面冒烟通过
7. ✅ CHANGELOG 更新（v0.1.1 → v0.1.2 变更记录）
8. ✅ ARCHITECTURE/ 目录更新（SDK_EXTRACTION.md 反映当前状态）
