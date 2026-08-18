# 验收标准：SDK 易用性优化（harness 0.1.2 切换 + 双 SDK 文档/验证脚本）

> 状态：DRAFT（待用户确认）
> 来源：2026-08-19 双 SDK 易用性评估结论（见会话记录；事实源 `ARCHITECTURE/SDK_EXTRACTION.md`、`ARCHITECTURE/MEMORY_SDK_BOUNDARY.md`、`plans/2026-08-17-sdk-ease-of-integration/`）
> 涉及仓库：`simple_harness`（宿主）、`simple-harness-sdk`、`simple-harness-memory-sdk`，各自仓库各自提交。

## 范围

**包含**（三个垂直 slice，每个独立验收）：

- **Slice 1 — harness SDK v0.1.2 宿主切换**（高风险：改启动装配链路）
  - `backend/vendor/` 纳入官方 `simple_harness_sdk-0.1.2-py3-none-any.whl`（来源 SDK 仓库 `dist/`，SHA-256 逐字节核对）
  - `backend/pyproject.toml` 依赖声明切到 0.1.2 wheel；`desktop_runtime.py` 的 `_SDK_VERSION` / `_SDK_WHEEL_SHA256` 同步更新
  - 验证 0.1.2 对 0.1.1 的 10-Port API 向后兼容（宿主现有 `sdk_adapters` 不改或少改）
  - 宿主回归：pytest 基线不新增失败、conformance 仍过、真机聊天链路可用
- **Slice 2 — harness SDK 消费者体验**（SDK 仓库）
  - quickstart.md 示例与 v0.1.2 真实 API 对齐（当前示例对着 0.1.1/0.1.2 都跑不起来）
  - `examples/minimal-consumer` 真正跑通（修掉 `Run completed: None` / 状态断言未收尾问题）
  - 文档推广 `build_consumer_runtime` 为推荐入口，10-Port 全量 API 降级为高级用法
- **Slice 3 — memory SDK 文档修正 + 双 SDK release-gate 验证脚本**
  - memory SDK README 标注 `[embeddings]` / `[world]` extras 与功能对应关系
  - README 说明默认 HashEmbedder 的检索质量前提（不装 embeddings extra 时语义召回退化）
  - 两个 SDK 各产出一个"5 分钟跑通"一键验证脚本（干净 venv → 安装 → 最小示例跑通 → 结构化 PASS/FAIL），作为 release gate

**明确不包含**：
- SDK 新功能开发（不新增 runtime/memory 能力）
- PyPI 发布（仍 path/wheel 分发）
- Windows x64 / Linux ARM64 平台验证（仅 macOS ARM64，跨平台待单独批准）
- AI Phone 实际接入实施（仅消费者体验改进）
- memory SDK 的 sqlite-vec 向量索引改造（评估建议项，不在本次范围）
- 宿主 `sdk_adapters` 重构迁移到 consumer layer（仅验证兼容，不做重构）

## 功能验收条款

### Slice 1 — harness SDK v0.1.2 宿主切换

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|----|--------|-------------------|--------|
| S1-AC-1 | vendor 0.1.2 wheel | `backend/vendor/simple_harness_sdk-0.1.2-py3-none-any.whl` 存在，SHA-256 与 SDK 仓库 `dist/` 同版本 wheel 逐字节一致；`pyproject.toml` path 依赖指向 0.1.2、requirement 改为 `>=0.1.2`；旧 0.1.1 wheel 处理显式记录（保留或删除） | 必须 |
| S1-AC-2 | fail-closed 校验同步 | `desktop_runtime.py` 的 `_SDK_VERSION="0.1.2"`、`_SDK_WHEEL_SHA256` 为 0.1.2 真实值；篡改 wheel 一字节后宿主启动按预期 fail-closed 拒绝（负向验证） | 必须 |
| S1-AC-3 | API 向后兼容 | 宿主现有 `sdk_adapters`（10-Port 组装）在 0.1.2 下不修改或仅最小修改即可工作；0.1.2 wheel 顶层导出是 0.1.1 的超集（导出清单 diff 无删除项） | 必须 |
| S1-AC-4 | pytest 无回归 | `cd backend && python -m pytest` 相对锁定基线无新增 failed（基线在 phase-2 锁定为快照文件） | 必须 |
| S1-AC-5 | conformance 仍过 | 宿主 adapter 的 conformance 套件结果不劣于切换前（切换前基线 20/20） | 必须 |
| S1-AC-6 | 真机聊天链路可用 | 干净状态下 `./scripts/dev.sh` 启动，SDK runtime ready slot 就绪，主聊天发一条消息得到非空 assistant 回复（真机验证，非脚本 replay） | 必须 |

### Slice 2 — harness SDK 消费者体验（simple-harness-sdk 仓库）

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|----|--------|-------------------|--------|
| S2-AC-1 | quickstart 可运行 | `docs/quickstart.md` 中的最小示例代码逐字拷贝到干净 venv + 0.1.2 wheel 环境可运行成功（脚本化验证，非人工目测） | 必须 |
| S2-AC-2 | minimal-consumer 跑通 | `python examples/minimal-consumer/main.py`（或等价入口）退出码 0，run 达到预期终态（COMPLETED 或示例显式声明的终态），不再出现 `Run completed: None` 类未收尾断言 | 必须 |
| S2-AC-3 | 文档推广消费者入口 | quickstart / integration-guide / api/ports.md 以 `build_consumer_runtime` 为推荐接入路径；10-Port 全量 `RuntimePorts` API 标注为高级用法；三处文档无互相矛盾 | 必须 |
| S2-AC-4 | 从零跑通验证 | 模拟外部用户：干净目录 + 干净 venv，仅依 quickstart 文档（不读 SDK 源码）完成安装到首次 run 成功，全程脚本记录 | 必须 |

### Slice 3 — memory SDK 文档 + 双 SDK release-gate 脚本

| ID | 功能点 | 验收条件（可验证） | 优先级 |
|----|--------|-------------------|--------|
| S3-AC-1 | README extras 对应关系 | memory SDK README 明确：`enable_world_model=True` 需要 `[world]` extra；BGE-M3 语义嵌入需要 `[embeddings]` extra（torch/sentence-transformers + 首次联网下载权重）；逐条与 `pyproject.toml` extras 定义一致 | 必须 |
| S3-AC-2 | hash embedder 前提说明 | README 明确默认 HashEmbedder 下语义召回为哈希伪向量、质量有限，生产使用应装 `[embeddings]` | 必须 |
| S3-AC-3 | harness SDK release-gate 脚本 | SDK 仓库提供一键脚本：干净 venv → 装 wheel → 跑通最小示例 → conformance CLI → 输出结构化 PASS/FAIL（exit code 区分），在本机实际执行 PASS | 必须 |
| S3-AC-4 | memory SDK release-gate 脚本 | SDK 仓库提供一键脚本：干净 venv → 安装 → quickstart 级示例（append + recall + facts）跑通 → 结构化 PASS/FAIL，在本机实际执行 PASS | 必须 |

## 非功能 / 边界

- **向后兼容**：Slice 1 不破坏宿主现有 `sdk_adapters` 公开行为；SDK 仓库侧改动不破坏 v0.1.2 已发布 API（只修文档与示例，如必须改代码则需 CHANGELOG 条目）
- **错误态**：fail-closed 校验（S1-AC-2）在 wheel 被篡改/缺失时必须拒绝启动且报错信息指明期望/实际 SHA
- **性能**：release-gate 脚本单机 macOS ARM64 全量运行 ≤ 10 分钟（干净 venv 建环时间计入）
- **兼容**：Python ≥ 3.11；宿主 backend venv 用 uv 管理，wheel 切换后 `uv sync` 可重现
- **审计**：三仓库各自提交，提交信息注明关联 program；SDK 仓库更新 CHANGELOG

## Assurance contract 摘要

- **Profile**：standard（信任开发者账户、OS/kernel、系统路径程序）
- **受保护资产**：
  - ASSET-1：宿主生产聊天链路可用性（SDK runtime 是唯一生产 ingress，切 wheel 直接触碰）
  - ASSET-2：SDK 公开 API 稳定性（消费者依赖）
  - ASSET-3：release 工件完整性（wheel SHA-256 链）
  - ASSET-4：文档正确性（消费者唯一接入指南）
- **可信假设**：
  - TRUST-1：消费者按文档实现 Ports（SDK 不防御恶意 Port 实现）
  - TRUST-2：开发环境可建干净 venv、可联网安装依赖
  - TRUST-3：Python 3.11+ 可用；macOS ARM64
  - TRUST-4：SDK 仓库 `dist/` 的 0.1.2 wheel 是官方构建（commit 91df02d release）
- **范围内失败**：
  - FAIL-1：vendor 了错误的 wheel bytes（版本/SHA 不符）
  - FAIL-2：0.1.2 与宿主 10-Port 用法不兼容导致生产链路回归
  - FAIL-3：文档/示例修正后仍跑不通（文档说谎）
  - FAIL-4：release-gate 脚本误报 PASS（实际坏了但报过）
  - FAIL-5：宿主 fail-closed 校验被削弱（版本/SHA 校验变成摆设）
- **明确范围外条件**：
  - OOS-1：恶意消费者/恶意 SDK 使用者
  - OOS-2：Python < 3.11、非 macOS 平台
  - OOS-3：完全离线环境（干净 venv 需联网装依赖）
  - OOS-4：SDK 新功能缺陷（只保本次触碰面的兼容与文档正确）
- **最大可接受影响**：宿主聊天链路暂时不可用需回滚 vendor；不得导致用户数据丢失/损坏

## 测试场景矩阵

`input_sensitive=false`（本次为依赖切换 + 文档/脚本，无 LLM 语义新功能），不设输入语义矩阵。
`stateful_init=true`（Slice 1 改启动装配链路，SDK runtime ready slot 是异步注册的持久服务），冷路径场景如下：

| scenario_id | input_class | exact_input | primary_risk | gate_type | required | manual_required | terminal_expectation |
|-------------|-------------|-------------|--------------|-----------|----------|-----------------|----------------------|
| COLD-1 | 冷启动 | 干净/重置用户数据目录 → `./scripts/dev.sh` 首次启动 → 主聊天发"你好，介绍下你自己" | 启动装配链路在全新状态可用 | positive-value | 是 | 是（真机） | SDK runtime ready + 非空 assistant 回复 |

## 测试义务矩阵（Test Obligation Matrix）

| obligation_id | type | ac_id | risk | min_decisive_test | required_reason |
|---------------|------|-------|------|-------------------|-----------------|
| TO-S1-1 | delivery | S1-AC-1 | — | sha256sum 对比 vendor 与 dist/ wheel | 直接证明 vendor 了正确 bytes |
| TO-S1-2 | delivery | S1-AC-2 | — | 篡改 wheel 一字节 → 启动 fail-closed 拒绝 | 证明校验真实生效而非摆设 |
| TO-S1-3 | delivery | S1-AC-3 | — | 0.1.1/0.1.2 顶层导出 diff 无删除 + 宿主 sdk_adapters import 测试 | 证明向后兼容 |
| TO-S1-4 | delivery | S1-AC-4 | — | pytest 全量对比基线快照 | 证明无回归 |
| TO-S1-5 | delivery | S1-AC-5 | — | conformance 套件全量运行 | 证明宿主 adapter 合规不退化 |
| TO-S1-6 | delivery | S1-AC-6 | — | COLD-1 场景真机执行 | 证明生产聊天链路真实可用 |
| TO-S2-1 | delivery | S2-AC-1 | — | 脚本逐字提取 quickstart 代码块在干净 venv 执行 | 证明文档示例为真 |
| TO-S2-2 | delivery | S2-AC-2 | — | minimal-consumer 脚本执行 + 终态断言 | 证明示例可跑通 |
| TO-S2-3 | delivery | S2-AC-3 | — | 文档 grep 检查 + 交叉引用一致性核对 | 证明推荐入口一致 |
| TO-S2-4 | delivery | S2-AC-4 | — | 干净目录仅依 quickstart 的脚本化复现 | 证明外部用户真实可接入 |
| TO-S3-1 | delivery | S3-AC-1/2 | — | README 与 pyproject extras 逐条比对 | 证明文档与事实一致 |
| TO-S3-2 | delivery | S3-AC-3 | — | 本机执行 harness release-gate 脚本得 PASS | 证明脚本可用 |
| TO-S3-3 | delivery | S3-AC-4 | — | 本机执行 memory release-gate 脚本得 PASS | 证明脚本可用 |
| TO-R1 | change-risk | — | FAIL-2 | 宿主全表面冒烟（所有入口各打最小一枪） | 启动装配改动 → full-surface 触发 |
| TO-R2 | change-risk | — | FAIL-1 | pyproject lock/hash 校验链路验证（uv sync 重现） | 依赖声明变更 |
| TO-E1 | exploratory | — | 潜在性能问题 | release-gate 脚本计时 | 非阻断 |

## 完成的定义（DoD 摘要）

1. 三个 slice 全部"必须"条款通过各自测试
2. 所有 delivery / change-risk 类型 obligation 有对应 PASS testcase
3. 三仓库各自 `git status` 干净、各自提交、CHANGELOG 更新
4. 宿主 ARCHITECTURE/（SDK_EXTRACTION.md / PROJECT_STATUS.md）与 memory SDK 边界文档按事实回写
5. 无回归：宿主全表面冒烟 + pytest 基线对比通过
6. gate finalize exit 0，receipt 入账
