# TaskGraph v1 → PlanAgent Handoff

日期：2026-09-21（Asia/Shanghai）  
用途：交给 PlanAgent 做实施就绪优化，不是授权立即实施完整 TaskGraph。

## 1. 当前结论

TaskGraph v1 资料包本身可复核，但当前项目还没有达到完整实施就绪状态。

允许提前开始的范围只有 **TG-A 的离线、低风险准备工作**：真实 SDK schema 副本上的 migration 预检、Store 接口核对、source-map 更新、测试 nodeid 规划和不写生产库的 fixture 验证。

暂时不得进入 TG-C/TG-D/TG-E 的真实运行链路，也不得打开 TaskGraph 生产开关。TG-B 的真实 compiler/resolver 接线要等 H1-H 前置来源完成后再进入正式实施。

权威入口：

- 主计划：[simpleharness-taskgraph-code-execution-plan.zh-CN.md](./simpleharness-taskgraph-code-execution-plan.zh-CN.md)
- 资料包：[simpleharness-taskgraph-code-plan-kit.zip](./simpleharness-taskgraph-code-plan-kit.zip)
- 当前架构入口：[../../../ARCHITECTURE/index.md](../../../ARCHITECTURE/index.md)

## 2. 已核实事实

### 2.1 资料包检查

资料包使用项目 Python 环境运行后：

- 87 个 reference unittest 通过；
- 8 份 JSON Schema 结构检查通过；
- 9 张扩展表和命名 SQL 在测试父表契约上通过；
- 42 个 SDK 场景仍为 `PENDING_SDK_EXECUTION`；
- 12 个 mutation 仍为 `PENDING_SDK_EXECUTION`。

这些结果只证明参考算法、Schema 和测试 fixture 可运行，不能证明 SimpleHarness SDK 已接线。

资料包自己的验证声明也明确没有执行真实 SDK migration、项目集成、Host、GPT-5.6、完整 H1 门禁或独立核验。

### 2.2 当前 H1-H 候选身份

- 候选路径：`/Users/denny/projects/simple-harness-sdk-h1h-impl`
- 分支：`codex/h1h-impl`
- HEAD：`102ad3dfa2db38d575ea929d39ec5ed1561a71da`
- 候选存在未提交修改；不得 reset、clean、pull、rebase、cherry-pick 或覆盖这些修改。
- SDK 主线：`/Users/denny/projects/simple-harness-sdk`，HEAD `51dbed2eaf81d3225bcb15911c33838a79c1fcd3`
- Host：`/Users/denny/projects/simple_harness`，当前工作树也有未提交修改。

### 2.3 probe 结果

执行资料包中的只读预检后，结果为：

- `source_complete=false`
- 缺少 `src/agent_orchestrator/orchestrator/planning_admission_commits.py::commit_admitted_plan`
- 预检要求重新核对实际 schema、checksum 和等价符号，不能假定通过

原始预检输出：`/tmp/taskgraph-checkout-probe.json`（如临时文件已清理，应重新生成到本地 ignored 目录）。

### 2.4 H1-H 三项 blocker

当前 H1-H 没有可以无损映射到 `AdmissionContext` 的权威生产来源：

1. mission-level planning authorization snapshot；
2. operation occurrence 到 action reconciliation 状态的权威 join；
3. candidate proposal → `PlanShapeView` 的准入前纯预检。

现有代码保持 fail-closed，不能用 `True`、空 tuple、环境变量、模型输出、method-level applicability 或临时重算补齐这些字段。

证据：

- [h1-admission-sources-terra.md](../../../.local-test-evidence/2026-09-20/h1/h1-admission-sources-terra.md)
- [auth-producer-sol-final.md](../../../.local-test-evidence/2026-09-20/h1/auth-producer-sol-final.md)
- [h1-gate-closure-sol.md](../../../.local-test-evidence/2026-09-20/h1/h1-gate-closure-sol.md)

## 3. PlanAgent 的任务

请对 TG-EXEC-2.0 做一次 **implementation-readiness optimization**，不要重写目标语义，不要开始大规模业务实现。

### 必须交付

1. **真实 source-map 修订**
   - 以当前候选 `102ad3d` 和现有 dirty diff 为准；
   - 明确每个 TaskGraph 复用符号、等价符号、缺失符号和新增文件；
   - 记录文件 SHA-256、候选 HEAD、dirty 文件清单；
   - 远端参考 SHA 只能作为历史参考，不能作为实施目标。

2. **H1-H 前置依赖包**
   - 为 authorization、operations、plan-shape 三项分别定义 producer、存储、读取事务、scope/epoch/revocation 语义、缺失行为和 focused tests；
   - 对 `commit_admitted_plan` 明确唯一权威入口及调用关系；
   - 若需要扩大 H1-H allowlist，列出具体文件和理由，不得隐式扩大范围。

3. **TG-A 可执行切片**
   - 明确真实 SDK migration runner 的入口；
   - 在完整 SDK schema 的隔离复制库上验证新建库、升级库、rollback、foreign key、checksum、legacy 不变；
   - 明确迁移编号如何避开现有 H1H-ADM/NanoJev migration；
   - 给出每个步骤的命令、输入、输出和通过条件。

4. **测试可执行映射**
   - 将 42 个场景和 12 个 mutation 映射到实际 SDK 文件、pytest nodeid 和证据路径；
   - 标明哪些必须真实 Store/Compiler/Commit，哪些只能 stub provider；
   - 将 stateful property test 与 legacy/full H1 gate 分开排程；
   - 不把 reference unittest 结果映射为 SDK PASS。

5. **环境与证据修订**
   - 所有命令固定使用项目 `.venv` 或 `uv run --frozen`，避免依赖系统 `python`；
   - 继续遵守 `.local-test-evidence/` 原始证据规则；
   - Git 只保存结论、命令、状态、nodeid、相对索引和 SHA-256，不提交原始日志、截图或数据库。

6. **状态门禁表**
   输出一张明确的状态表，至少包含：

   - `REFERENCE_KIT_PASS`
   - `SOURCE_MAP_COMPLETE`
   - `H1_H_READY`
   - `TASKGRAPH_TG_A_READY`
   - `TASKGRAPH_CORE_READY`
   - `TASKGRAPH_HOST_READY`
   - `TASKGRAPH_REAL_MODEL_READY`
   - `PRODUCTION_ENABLEMENT_ALLOWED`

   每一项必须绑定证据文件、候选 SHA 和通过条件；当前未满足项保持 `PENDING` 或 `BLOCKED`。

## 4. 优化后的实施顺序

建议 PlanAgent 将计划重排为：

1. 只读核对当前候选、Host、SDK 主线及 dirty diff；
2. 补齐 H1-H 三项权威来源的合同和 allowlist；
3. 重新生成 source-map 并做符号/SQL key 预检；
4. 仅实施 TG-A 离线 slice；
5. 在完整 SDK schema 复制库上做 migration 验证；
6. H1-H focused tests 全绿并完成独立核验后，才进入 TG-B；
7. TG-C/D/E 依序实施，不允许跳过 H1 guard；
8. 最后执行 42 场景、12 mutation、stateful、legacy、Host 和获准模型验证。

## 5. 明确禁止事项

- 不得把 87 个 reference tests 通过写成 TaskGraph 完成。
- 不得在 H1-H 三项来源缺失时构造默认授权、空 operation 状态或空 `PlanShapeView`。
- 不得创建第二套 Task、预算、Operation 或 Goal 权威表。
- 不得用 latest semantics、空 manifest、Task.CANCELLED、lease 过期或模型自报结果替代真实来源。
- 不得在主线或 Host dirty worktree 上做破坏性同步。
- 不得修改新公开 PlanningDecision wire、NanoJev Shadow、模型路由或无关产品功能。
- 不得把 Host seam、真实模型和生产开关标为已完成。

## 6. PlanAgent 输出格式

请返回以下文件或等价内容：

1. `HANDOFF-PLANAGENT-OPTIMIZED-2026-09-21.md`
2. 更新后的 source-map，包含候选身份、dirty 文件 hash、缺失符号和真实路径
3. H1-H 三项 blocker 的依赖/allowlist/测试矩阵
4. TG-A 的逐文件实施清单和可复制命令
5. 42 场景 + 12 mutation 的 nodeid 映射表
6. 状态门禁表和明确的 `NEXT ACTION`

优化结束时，必须明确回答：

> 当前是否可以开始 TG-A？
> 当前是否可以开始 TG-B？
> 当前是否可以开启 TaskGraph 生产路径？

在 H1-H 三项权威来源、完整 source-map 和真实 SDK migration 验证完成前，预期答案应分别为：

- TG-A：可以，限离线准备/隔离库验证；
- TG-B：不可以；
- 生产路径：不可以。
