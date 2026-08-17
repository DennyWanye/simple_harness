# Plan：SDK 易接入性改进（v0.1.1 → v0.1.2）

<!-- plan-status: finalized -->

> 代码基线：`c5952c2e`（2026-08-17）
> 验收标准：`acceptance-sdk-ease-of-integration.md`
> Assurance Contract：`assurance-contract-sdk-ease-of-integration.json`
> 用户确认时间：2026-08-17

## 主要矛盾

**核心问题：当前 SDK 功能已完整，但消费者没有足够信息知道"如何正确接入"。**

具体表现：
1. Conformance 套件的 case 实现（在 Simple Harness 里）全部是单行压缩代码，无法作为其他消费者的参考
2. SDK 仓库没有 Quickstart 文档、Integration Guide、示例代码
3. Memory Port 接口未定义（阻断未来 Memory SDK 接入）
4. `ModelPersonalWorkflowMatcher` 类仍保留在产品代码中（虽然已不在生产路径上）

**架构基线调查发现（实际比文档更靠前）：**
- ✅ `ModelPersonalWorkflowMatcher` 已从 `main.py` 生产路径移除（仅剩 `.bak` 文件和类定义）
- ✅ Conformance 框架已实现（runner/verifiers/contracts）
- ✅ Simple Harness 已实现所有 20 个 conformance cases（但内联压缩，无参考价值）
- ✅ Host Ports（personal_catalog/capability_host）已有 adapter 实现
- ✅ AI Phone Handoff 文档已存在（`docs/consumers/aiphone-handoff.md`）
- ❌ SDK 仓库无 Quickstart / Integration Guide / 示例代码
- ❌ SDK 无 MemoryPort 接口
- ❌ API 文档缺 runtime.md / workflow.md / ports.md

## 关联验收标准

覆盖：EI-AC-1, EI-AC-2, EI-AC-3, EI-AC-4, EI-AC-5, EI-AC-6, EI-AC-7, EI-AC-8, EI-AC-9

## 文件影响清单

| 仓库 | 文件 | 职责 | 本次改动 |
|------|------|------|----------|
| simple-harness-sdk | `src/simple_harness/runtime/ports.py`（新建） | Memory Port 接口 | 新增 MemoryQueryPort / MemoryWritePort |
| simple-harness-sdk | `src/simple_harness/runtime/__init__.py` | 导出 | 导出新 Memory Ports |
| simple-harness-sdk | `docs/api/ports.md`（新建） | Port 接口文档 | 完整记录所有必须实现的 Ports |
| simple-harness-sdk | `docs/api/runtime.md`（新建） | Runtime API 文档 | RunKernel / build_runtime / RunStart API |
| simple-harness-sdk | `docs/api/workflow.md`（新建） | Workflow API 文档 | 三个官方 Workflow 接入方法 |
| simple-harness-sdk | `docs/quickstart.md`（新建） | 快速开始 | 安装 + 10 行示例 + 运行验证 |
| simple-harness-sdk | `docs/integration-guide.md`（新建） | 完整集成指南 | Port 实现指南 + 三个 Workflow 接入 |
| simple-harness-sdk | `examples/minimal-consumer/`（新建） | 参考示例 | <100 行可独立运行的最小消费者 |
| simple-harness-sdk | `docs/consumers/aiphone-handoff.md` | AI Phone 交付 | 更新为 v0.1.1，补充 Memory Port |
| simple-harness-sdk | `CHANGELOG.md` | 变更记录 | 补充 v0.1.2 条目 |
| simple_harness | `backend/deskpet/companion/turn_authority.py` | Companion 逻辑 | 删除 ModelPersonalWorkflowMatcher 类定义 |

## Assurance / 信任与失败边界

- **Profile**：standard
- **入口链**：SDK wheel → `import simple_harness` → `build_runtime()` → `RuntimePorts`（Port 由消费者注入）
- **Trust boundary**：Port 实现由消费者提供，SDK 不防御恶意 Port
- **数据流**：Memory Port 是只读/写接口，不拥有数据存储
- **范围内失败**：FAIL-1（文档不可运行）、FAIL-4（缺 Port 文档）
- **停止追踪点**：不实现 Memory 功能本身（只定义接口）；不实现 AI Phone 移动端适配

## 任务清单（按依赖排序）

---

### Task 1 — 删除 ModelPersonalWorkflowMatcher 类定义  [覆盖 EI-AC-1]

- **改动文件**：`backend/deskpet/companion/turn_authority.py`
- **现状**：类定义在第 289 行，`__all__` 第 1003 行仍导出；但 `main.py` 不再引用
- **修改方式**：
  1. 删除 `class ModelPersonalWorkflowMatcher` 定义（约 289-350 行）
  2. 从 `__all__` 中移除 `"ModelPersonalWorkflowMatcher"`
  3. 检查是否有其他文件 import 它（`.bak` 文件不算）
- **API 兼容性考虑**：该类在 `__all__` 中导出但 Simple Harness 产品已不使用；SDK 未导出该类，因此删除不影响 SDK 消费者
- **验证**：`grep -rn ModelPersonalWorkflowMatcher backend/ --include="*.py"` 返回空
- **依赖**：无

---

### Task 2 — 定义 SDK MemoryPort 接口  [覆盖 EI-AC-8]

- **改动文件**：`simple-harness-sdk/src/simple_harness/runtime/ports.py`（新建）
- **现状**：SDK 无 Memory Port，但 conformance.py 中有 `recall_readonly` / `replace_session_todos` 使用痕迹
- **修改方式**：在 SDK 仓库新建 `ports.py`，定义两个 Protocol：

  ```python
  class MemoryQueryPort(Protocol):
      """Read-only memory recall interface. Consumers implement this to give
      the Agent access to long-term memory without owning the storage."""

      async def recall_readonly(
          self,
          query: str,
          limit: int,
          scope: str,
      ) -> list[dict[str, JsonValue]]:
          """Return at most `limit` memory entries relevant to `query`
          within `scope`. Must never write or mutate any state."""
          ...

  class MemoryWritePort(Protocol):
      """Write interface for session-scoped working memory.
      Consumers implement this to persist short-term notes across turns."""

      async def replace_session_todos(
          self,
          session_id: str,
          items: list[dict[str, JsonValue]],
      ) -> None:
          """Replace the full working-memory list for `session_id`."""
          ...
  ```

- **更新导出**：在 `runtime/__init__.py` 中导出 `MemoryQueryPort`, `MemoryWritePort`
- **更新 public-api.json**：`tests/unit/contracts/public-api.json` 加入新名称
- **验证**：`import simple_harness; simple_harness.MemoryQueryPort` 不报错
- **依赖**：无

---

### Task 3 — SDK API 文档：ports.md  [覆盖 EI-AC-6]

- **改动文件**：`simple-harness-sdk/docs/api/ports.md`（新建）
- **内容结构**：
  1. **必须实现的 Ports 总览表**（接口名 / 用途 / 是否必须）
  2. **ProviderPort**（已有，补充示例）
  3. **ToolExecutorPort**（已有，补充示例）
  4. **AuthorizationPort**（来自 authorization.py 提炼）
  5. **WorkspacePort / ArtifactPort**（来自工作流需求）
  6. **PersonalWorkflowCatalogPort**（来自 personal_catalog.py）
  7. **CapabilityCatalogPort**（来自 capability_host.py）
  8. **MemoryQueryPort / MemoryWritePort**（Task 2 新增）
  9. 每个 Port 附：接口签名 + 实现约束 + 简单实现示例
- **验证**：文档中所有代码块语法正确（`python3 -m py_compile` 可通过）
- **依赖**：Task 2

---

### Task 4 — SDK API 文档：runtime.md 和 workflow.md  [覆盖 EI-AC-6]

- **改动文件**：
  - `simple-harness-sdk/docs/api/runtime.md`（新建）
  - `simple-harness-sdk/docs/api/workflow.md`（新建）
- **runtime.md 内容**：
  - `build_runtime(ports) -> RuntimeStack` 的参数说明
  - `RunStart` 数据类字段说明
  - `RunKernel` 生命周期（start/wait_idle/close）
  - 错误码说明
- **workflow.md 内容**：
  - 三个官方 Workflow 的 Profile key 与用途
  - 如何通过 `build_official_workflow_registrations()` 获取注册
  - `WorkflowHostServices` 各子接口说明
  - initial_state 工厂函数说明
- **验证**：与 SDK 源码中实际函数签名一致（人工核对）
- **依赖**：无

---

### Task 5 — SDK Quickstart 文档  [覆盖 EI-AC-4]

- **改动文件**：`simple-harness-sdk/docs/quickstart.md`（新建）
- **内容结构**（≤500 行）：
  1. **安装**：`pip install simple_harness_sdk-0.1.1-py3-none-any.whl`
  2. **最小示例**（20 行，fake Provider + 1 个 Tool + 单轮对话）：
     ```python
     import asyncio
     from simple_harness import build_runtime, RunStart, ...
     # 实现一个最简 fake Provider
     # 注册一个 echo Tool
     # 启动 runtime，跑一个 Run，获取结果
     ```
  3. **验证安装**：`python -m simple_harness.testing --help`
  4. **下一步**：链接 Integration Guide
- **验证**：代码块能在干净 venv 中运行（用 `subprocess` 测试）
- **依赖**：Task 3（需要先知道 Port 接口）

---

### Task 6 — SDK Integration Guide  [覆盖 EI-AC-5]

- **改动文件**：`simple-harness-sdk/docs/integration-guide.md`（新建）
- **内容结构**（≤1000 行）：
  1. **概念图**：SDK 与消费者的层次关系（ASCII 图）
  2. **必须实现的 Ports**（每个有接口 + 伪代码 + 注意事项）
  3. **接入 durable_task Workflow**（步骤 + 代码）
  4. **接入 personal_v1 Workflow**（步骤 + 代码）
  5. **接入 capability_build Workflow**（步骤 + 代码，optional）
  6. **接入 Memory**（实现 MemoryQueryPort + MemoryWritePort）
  7. **运行 Conformance 验证**（命令 + 报告解读）
  8. **常见问题**（Port 未实现报什么错、Workflow 选择失败怎么排查）
- **验证**：文档中所有代码片段能通过语法检查；覆盖所有必须 Ports
- **依赖**：Task 3, Task 4

---

### Task 7 — Minimal Consumer Example  [覆盖 EI-AC-7]

- **改动文件**：`simple-harness-sdk/examples/minimal-consumer/`（新建目录）
  - `main.py`（<100 行）
  - `README.md`（说明每个部分的作用）
- **main.py 内容**：
  ```python
  """
  Minimal Simple Harness SDK consumer.
  Demonstrates: fake Provider + 1 Tool + single conversation turn.
  Run: python main.py
  """
  import asyncio
  from simple_harness import ...

  class FakeProvider:        # 实现 ProviderPort
      ...

  class EchoTool:            # 实现 Tool handler
      ...

  async def main():
      ports = RuntimePorts(provider=FakeProvider(), ...)
      async with build_runtime(ports) as runtime:
          run_id = RunId("demo-run")
          await runtime.start(RunStart(...))
          result = await runtime.wait_idle(run_id)
          print("Result:", result.state)

  asyncio.run(main())
  ```
- **README.md**：标注每个类的用途 + 对应 Port 接口 + 链接到文档
- **验证**：`python examples/minimal-consumer/main.py` 输出 `Result: completed`
- **依赖**：Task 5, Task 6

---

### Task 8 — 更新 AI Phone Handoff 文档  [覆盖 EI-AC-9]

- **改动文件**：`simple-harness-sdk/docs/consumers/aiphone-handoff.md`
- **现状**：文档已存在，基于 v0.1.0，无 Memory Port 说明
- **修改方式**：
  1. 版本号更新为 v0.1.1
  2. 补充 **Memory Port 接入章节**（AI Phone 需要实现 `MemoryQueryPort` 接入手机本地存储）
  3. 补充 **Mobile 约束说明**（SQLite 路径限制、后台任务限制）
  4. 添加 **集成检查清单**（✅ 清单形式）
  5. 添加架构关系图（ASCII 图：AI Phone → SDK Ports → SDK Runtime）
- **验证**：文档包含架构图 + 检查清单；版本号正确
- **依赖**：Task 2

---

### Task 9 — 验证 Workflow Host Ports 注入与 E2E  [覆盖 EI-AC-2]

- **改动文件**：检查 SDK 与 Simple Harness 适配层
- **现状**：架构调查显示 `personal_catalog.py` / `capability_host.py` 已存在，但未验证注入完整性
- **检查项**：
  1. SDK `WorkflowRuntimeDriver` 是否接受 `personal_catalog_port` / `capability_catalog_port` 参数
  2. Simple Harness `composition.py` 是否将这些 ports 注入到 runtime
  3. 三个 Personal Workflow 场景是否能通过完整流程
- **修改方式**（如发现缺失）：
  - 在 SDK `build_runtime()` 或 `WorkflowRuntimeDriver` 添加 port 参数
  - 在 Simple Harness 适配层注入实现
- **验证**：运行 Simple Harness E2E 脚本，验证三个 Personal Workflow（或基于已有 conformance cases）通过
- **依赖**：Task 2（Memory Port 可能被 Personal Workflow 使用）

---

### Task 10 — 运行并修复 Conformance Suite  [覆盖 EI-AC-3]

- **改动文件**：SDK `simple_harness/testing/` 或 Simple Harness 适配层
- **现状**：
  - SDK conformance 框架已完整（runner/verifiers/contracts）
  - Simple Harness `conformance.py` 实现了所有 20 个 cases（但内联压缩）
  - 未知实际运行结果
- **执行步骤**：
  1. 运行 `python -m simple_harness.testing --host deskpet.sdk_adapters.conformance:build_host --suite provider,tool,runtime,workflow --artifact-sha256 <wheel-hash>`
  2. 记录通过/失败 cases
  3. 修复失败 cases（至少确保 5 个核心场景通过）
  4. 验证 Simple Harness 整体通过率
- **验证**：conformance 报告显示至少 provider.physical_request, tool.schema, runtime.no_tool, runtime.one_tool, workflow.official_durable_task 通过
- **依赖**：Task 9（可能需要先修复 Host Ports 注入）

---

### Task 11 — 更新版本号与 API 兼容性验证  [覆盖 EI-AC-8 DoD]

- **改动文件**：
  - `simple-harness-sdk/pyproject.toml`
  - `simple-harness-sdk/src/simple_harness/__init__.py`
  - `simple-harness-sdk/CHANGELOG.md`
- **修改方式**：
  1. **版本号更新**：
     - `pyproject.toml`: `version = "0.1.2"`
     - `__init__.py`: `__version__ = "0.1.2"`
  2. **API 兼容性检查**：
     - 对比 v0.1.1 → v0.1.2 公开 API 变更
     - 确认新增：`MemoryQueryPort`, `MemoryWritePort`
     - 确认无删除或破坏性变更（Task 1 删除的 `ModelPersonalWorkflowMatcher` 不在 SDK 中）
  3. **CHANGELOG 更新**：
     ```markdown
     ## 0.1.2 — 2026-08-17
     ### Added
     - MemoryQueryPort and MemoryWritePort interfaces for Memory SDK integration
     - docs/quickstart.md, integration-guide.md
     - docs/api/ports.md, runtime.md, workflow.md
     - examples/minimal-consumer/

     ### Changed
     - docs/consumers/aiphone-handoff.md: updated to v0.1.1, added Memory Port section

     ### Removed (product-side)
     - ModelPersonalWorkflowMatcher class removed from Simple Harness companion module
     ```
- **验证**：
  - `python3 -c "import simple_harness; print(simple_harness.__version__)"` 输出 `0.1.2`
  - CHANGELOG 格式符合 Keep a Changelog 规范
  - 无 Breaking Changes（v0.1.x 允许新增 API）
- **依赖**：Task 1–8, 10

---

### Task 12 — 外部审阅者验证  [覆盖 EI-AC-4]

- **执行方式**：
  1. **招募审阅者**：找 1 名未接触过 SDK 的开发者（可以是团队其他成员或 AI Phone 团队）
  2. **提供材料**：只给 `docs/quickstart.md` + wheel 文件路径
  3. **观察过程**：记录审阅者遇到的卡点、需要补充的说明
  4. **迭代文档**：根据反馈更新 quickstart.md
  5. **通过标准**：审阅者能在 30 分钟内完成首次运行并看到 `Result: completed`
- **验证**：有审阅者通过记录（姓名 + 时间戳 + 成功截图）
- **依赖**：Task 5（quickstart.md 必须先完成）, Task 7（example 可作为验证目标）
- **注意**：此任务可在 Task 11 之后、正式发布前执行

---

## 执行顺序（依赖图）

```
Task 1 (删除 Matcher)    ──── 独立
Task 2 (MemoryPort)      ──── 独立
Task 3 (ports.md)        ──── 依赖 Task 2
Task 4 (runtime+workflow docs)  独立
Task 5 (Quickstart)      ──── 依赖 Task 3
Task 6 (Integration Guide)──── 依赖 Task 2, 3, 4
Task 7 (Example)         ──── 依赖 Task 2（API 依赖，不依赖文档）
Task 8 (AIPhone Handoff) ──── 依赖 Task 2
Task 9 (验证 Host Ports) ──── 依赖 Task 2
Task 10 (Conformance)    ──── 依赖 Task 9
Task 11 (版本号+CHANGELOG)─── 依赖 Task 1-10
Task 12 (外部审阅)       ──── 依赖 Task 5, 7, 11
```

**并行机会**：
- **第一批**：Task 1, 2, 4 可同时执行
- **第二批**：Task 3, 8 可在 Task 2 完成后并行
- **第三批**：Task 5, 6, 7 可在 Task 3 完成后并行
- **第四批**：Task 9, 10 串行执行（验证注入 → 运行 conformance）
- **收尾**：Task 11（汇总）→ Task 12（人工验证）

## 实施注意事项

1. **SDK 仓库变更**：在 `/Users/denny/projects/simple-harness-sdk` 进行，需要单独构建新 wheel（v0.1.2）
2. **public-api.json 更新**：每次新增 SDK 公开名称必须同步更新快照文件
3. **docs 代码块测试**：Quickstart 和 Integration Guide 的代码块要在干净 venv 验证
4. **Memory Port 设计约束**：只定义接口，不实现功能；接口尽量简单（2个方法足够）
5. **Example 独立运行**：example 不依赖 Simple Harness 产品代码，只依赖 SDK wheel
6. **Conformance 优先**：Task 10（运行 conformance）可能发现 Host Ports 注入问题，优先修复后再继续文档
7. **版本策略**：v0.1.x 允许新增 API（additive changes），不允许删除或修改已有 API（breaking changes）
8. **外部审阅**：Task 12 可安排在其他任务完成后，不阻塞主线开发

## Plan 修订记录

- **v1（2026-08-17 初稿）**：9 个任务
- **v2（2026-08-17 challenger round 1）**：补充 Task 9-12，覆盖缺失的 EI-AC-2/AC-3 验证、版本号更新、外部审阅
