# TaskGraph 与 HTN 并行开发调研

日期：2026-09-21。性质：只读调查与建议分工，不是已派发任务、已创建 worktree 或已解除实施门禁。本轮未向其他任务发送指令，未改 SDK、原始附件或架构事实源。

## 结论

建议采用 **两条实施线、一个集成负责人、按稳定检查点交付**。现阶段 HTN/H1 继续生产链实施，TaskGraph 继续 TG-A-prep；共享运行链由 HTN/H1 owner 单独写入。TaskGraph 的真实编译/提交/收敛接线等相关前置验证后开始，不必等待全部 H1–H8 收尾。

上一轮“HTN H3–H8 目录可以直接并行”的说法不够精确：原 TaskGraph allowlist 包含 `planning/htn/validation.py`，并复用 HTN compiler、preview、accepted_outputs、occurrence_outcomes 和 Commit；目录分离并不代表语义无依赖。

## 本次发现

- 当前 Codex 任务“继续 H1-H 任务交接”处于 active；其工作使用 SDK candidate `/Users/denny/projects/simple-harness-sdk-h1h-impl`。本轮只读取状态，没有打断或派工。
- SDK candidate HEAD 仍是 `102ad3dfa2db38d575ea929d39ec5ed1561a71da`；SDK main 为 `51dbed2...`。当前 `git diff --stat` 有 29 个 tracked 文件变化，尚不含 untracked；仅 event_handler 已有大量变化。不能把 HEAD 当当前完整基线。
- 现有 SDK worktrees 包括 h1h-impl、h1h-tests 等，没有本轮建立的 TaskGraph 开发 worktree。不要占用/清理 h1h-tests 或其他旧目录，它们不是空闲目录的证明。
- ARCHITECTURE 最新记录说明 H1-H 整门尚未闭合。20 PASS / 8 PARTIAL / 8 NOT_COVERED 和 3891/5 是先前源码检查点，后继修改后不能自动续用；本轮未重跑这些测试。
- 更晚的《Operation完成合同补遗评估》已明确架构待决项为 0，D1/D2/D3 和完成合同可以进入实施。旧“架构裁定请求”不是当前停工理由。参考 SQL 的跨 Spec/Scope 重审唯一键缺陷是实施必修项。
- V1.2.1 的本地工具原包 88/2，独立副本两行测试兼容修复后 90/90；需要正式整理可复测工具交付，不能再以原包 90/90 宣称通过。

本地证据来自文末文件与当前源码/任务/worktree 清单；不断变化的工作区须在实际开始时重新读取。

## A/B/I 三种职责

| 线 | 当前具体工作 | 写入位置与所有权 | 必须交付 |
|---|---|---|---|
| A：HTN/H1 | Operation intent/materialization、payload/resolver、Completion Spec/Scope/Reader、D3 同决定延期恢复；H1-H 合同与实际覆盖 | 原 HTN/H1 owner 继续使用现有 candidate；其内部若再并行，也需按文件单写者拆分 | 准确接口/错误/事务/状态合同、稳定源码检查点、相关实际测试结果、剩余缺口 |
| B：TaskGraph 准备 | 两处工具测试修复、source-map 核对、真实 registry collection/bind、runner 补丁副本与隔离迁移验证；42/12 测试映射和与新完成合同的差异表 | 新 ignored evidence 目录；基线 snapshot 不改，runner 使用 derived copy；只改自己新增的报告/工具副本 | 原包与修复版分别记录，base/derived 身份分开，迁移报告、待接线清单 |
| I：集成 | 接收 runner 提案、确认上游接口检查点、分配 migration 版本、整合共享接缝、运行组合回归 | 指定一个集成负责人；可由 A owner 兼任，不需要再启动一个写同仓的 Agent | 集成源码身份、差异审查、组合测试、架构状态更新 |

当前 A 与 B 可同时推进。B 不修改 A 的生产源码、不替 A 造授权或 Operation producer；A 不直接改 B 的冻结证据或回填 B 的测试结果。C01–C10 归 A 的合同验证，B 消费收据并审核，不重复实现同一入口。

## 共享文件与迁移规则

以下均是共享接缝，当前由 A/I 单写：

- `orchestrator/event_handler.py`、`hierarchical_dispatch.py`
- `orchestrator/commit_service.py`、`plan_commits.py`、`planning_admission_commits.py`、`action_commits.py`、`resolution_commits.py`
- `planning/plan_preview.py`、`planning/decision_admission.py`、`planning/htn/compiler.py`、`planning/htn/validation.py`
- `storage/htn_store.py`、`storage/store.py`、`storage/schema.py`
- Host 的 SDK 安装配置、vendor/lock、真实 route 与共用 ARCHITECTURE 状态页

即使不同 worktree 中 Git 能自动合并，也必须审查事务边界、错误 union、完成含义与 read-set，不能把无文本冲突等同于无语义冲突。

runner 的例外：B 可在派生快照改 `_apply_migration` 迭代并提出补丁；A/I 审阅后在自身基线实施。B 不覆盖 candidate 的整个 store.py。正式注册版本由 I 在当前完整迁移链上分配；不能两边都预定 v21。旧已登记 DDL/checksum 不变；新 HTN 表先满足依赖，TaskGraph 注册在后续 TG-A-registration 范围处理。

## 必须先对齐的五组接口

| 上游 owner A 提供 | 下游 B 消费处 | 不可变的语义要求 |
|---|---|---|
| request、authority、Operation/action identity、running-work reader | taskgraph_sources/read_plan_context | 精确身份、完整集合、错误不可当空结果；同一 Store 一致读 |
| preview/compiled admission/commit receipt | TG-B preview、TG-C commit | 同一冻结 delta/read-set；唯一 Commit 和原事务；C01–C10 等价证明 |
| CompletionReader + Scope/Contribution | accepted_outputs、occurrence_outcomes、settlement_facts | 准备内容可按 DATA 读取；MIXED 效果未完成不能释放完整 ORDER；不由 Task 终态猜完成 |
| D3 continuation/stop gate/reconcile/wakeup | TG-D convergence/followups | 复用原 request/raw/decision，UNKNOWN 不提前消除，无第二套 Operation/余额/完成账本 |
| 最新 schema chain、migration runner | TG-A 全 schema 迁移 | 顺序/父表/FK/事务/旧数据保持，未注册 descriptor 与正式迁移证据区分 |

接口交付表至少包含：实际 qualified symbol/签名、文件 hash、输入输出及具名错误、事务 owner、read-set/版本轴、对应 nodeid/收据范围。来源不可读时记录缺口；测试 adapter 不得替代生产 producer。

需要在 TaskGraph 对齐附录中明确引用 V14-OP-SEAMS-1.0 和 V14-OP-COMPLETION-1.0 对相关接口的最新约定；不改原附件字节，不重新设计另一套完成合同。若发现实质冲突，局部裁定该冲突，其他独立准备继续。

## 工作区和基线：分两步

### 现在：冻结副本即可，无需打断 HTN

B 通过现有 capture 工具获取含 tracked dirty 和 untracked 受控源码的 snapshot，验证前后 fingerprint 一致。采集过程中 A 改动导致不一致，就丢弃该次验收资格并使用新目录重采集；绝不 reset A。若持续无法稳定，约定一个很短的“暂停源码写入、完成 capture”交接点，而不是暂停全部开发。

基线 B0 不再变化；runner 实验写 B0 的 derived copy。新 HTN 检查点 B1 来后另建目录，按变更文件和依赖核对影响，不覆盖旧记录、不热同步正在测试的源码。

### 真正 SDK 接线阶段：独立、短期 worktree

A/I 先提供包含所需 dirty/untracked 实现的、经审阅的本地检查点 commit，再从这个确切 commit 建 B 的短期 worktree（分支使用 `codex/` 前缀）。现有 candidate 不变，不让 B 从 SDK main 或老 `102ad3d` 空白起步。

Git worktree 隔离工作目录、HEAD 和 index，但仓库对象及多数 refs/config 共享；从某 commit 新建 worktree 不会自动带走另一个工作区的未提交实现。见 [Git 官方 worktree 文档](https://git-scm.com/docs/git-worktree)。因此“建一个 worktree”不能替代检查点交付。当前不要求 HTN 为 B 立即强制提交全部 dirty 内容，prep 用 snapshot 即可。

基线 commit 只包含审阅过的源码/测试/允许的说明，不包含原始证据、凭据、运行数据库或机械 git add -A。隔离 worktree 是有明确验收边界的临时实现区，回归后及时整合，不引入长期 feature 分支。

## 从现在到合流的具体顺序

1. **现在即可并行**：A 按最新 Operation/完成/D3 补遗实施；B 整理两行工具修复、重新 capture、参数 collection/bind、隔离 runner/migration probe。
2. **首个小交付**：B 将 runner 的最小 diff 和 base/derived 结果交 I；I 基于自己的最新 source review、应用并重测。B 的 DERIVED_PASS 不改成 candidate PASS。
3. **接口检查点**：A 输出上述五组接口清单与完成范围；B 更新 source-map 和 TaskGraph 对齐表。特别验证 DATA 准备可用与 ORDER 真完成的差异，以及同一决定恢复的身份规则。
4. **注册与合同门**：I 完成 H1 必要 schema/runner，A 取得 C01–C10 与 H1-H 实际覆盖/变异/独立核验；在既定范围完成 TaskGraph 正式迁移、新建/升级/回滚/FK/checksum/旧数据验收。
5. **TG-B 开始条件**：SOURCE_MAP_COMPLETE + H1_H_READY + TG_A_VALIDATED 满足后，B 在新基线短期 worktree 实施真实图编译/resolver。此时 A 可继续不影响这些合同的 H2–H8 项，不要求整个 HTN 验收结束。
6. **共享接线合流**：TG-C/D 对共享文件的改动交 I 串行整合；H1 和 TaskGraph 各自测试通过后，仍需在组合源码上测试事务、Operation/Completion、延期/重启和旧模式，最后才做 Host/模型/UI/制品验收。

当前不建议为“看起来并行”提前实现 TaskGraph Store/图运行代码或放宽 V1.2.1 的门禁。若确需把纯新增模块提前独立实现，应明确修订该切片范围及输入合同；本次调研没有替用户扩大这一范围。

## 运行隔离与复测

- 各线使用独立 DB、artifact 目录、evidence/run ID；不连接对方测试库或生产库。pytest 不仅隔离目录，还需控制 sqlite/临时数据与安装依赖。
- prep 可用现有 .venv 只读运行锁定依赖；任何依赖安装/sync/升级不得并发作用于共享 .venv。独立运行区确认实际 import 路径和 source hash，防止加载旧 wheel。
- B 当前无模型调用、无需 backend/Vite 端口；HTN 模型链继续自身已定配置与预算。不要因 TaskGraph 旧文档 GPT-only 字样擅自改 HTN 的 DeepSeek 配置，TaskGraph 自己的后续模型验收另按其授权处理。
- 后续两线都要 Host UI 时，隔离 user data、DB 和端口，Tauri 自行管理 backend/Vite；vendor/lock 由集成负责人统一更新。
- 基础 reader/contract/schema 或 fixture/conftest 改动：重验受影响合同和相关集成测试；组合发布点再做规定完整门禁。纯文档不触发无意义的全套重跑。历史测试保持历史身份，不因为同 HEAD 自动继承。
- 完成功能片段时，按 AGENTS 同次更新对应 ARCHITECTURE、PROJECT_STATUS 与必要里程碑；集成负责人防止两个流程覆盖状态正文。报告只有工具/准备 PASS 时，不提升产品完成度。

## 可直接交接的任务边界（尚未发送）

**给 HTN owner：**继续现有 candidate 的 H1–H8 已定范围，优先交付 Operation/Completion/D3 与唯一 Commit 的稳定接口和测试证据。共享提交/派发/存储文件保持单写者；接收 TaskGraph runner 最小提案后在最新源码整合，提供新的基线身份。不要为 TaskGraph 停掉独立 HTN 工作。

**给 TaskGraph owner：**仅执行 V1.2.1 prep 与上述接口对齐。修正工具测试并保留原包；使用冻结 snapshot/derived source、独立 SQLite；不改 HTN candidate、不开 TG-B/C/D/E，不制造替代 producer。交付 runner diff、完整性/迁移/collection 证据和明确未满足的门。

**给集成负责人：**管理共享文件、migration 序号和检查点；先审 base/derived diff 再在自己的最新代码中整合；每次合流重新确认源码身份与测试覆盖。不要把 reference、collection 或单线 PASS 宣称为组合通过。

## 依据

- [当前架构入口](/Users/denny/projects/simple_harness/ARCHITECTURE/index.md) 最新段落。
- `plans/taskSys2/升级planV1/v1.4/WAIT复验-2026-09-21.md`：最新覆盖与后继修改。
- `plans/taskSys2/升级planV1/v1.4/V1.4-D1-D2-D3-Operation补遗-2026-09-21.md`。
- `plans/taskSys2/升级planV1/v1.4/V1.4-Operation完成合同补遗-2026-09-21.md` §5、§9–10：CompletionReader/消费者/实施顺序。
- `plans/taskSys2/升级planV1/v1.4/Operation完成合同补遗评估-2026-09-21.md`：架构待决 0、参考唯一键必修。
- TaskGraph 原 TG-EXEC-2.0 §3、§13–14；V1.2.1 §4–6、§9；同目录 REVIEW-2026-09-21.md。
- 现场读取的任务列表、SDK git worktree list、diff --stat、相关源码符号；未重跑 HTN 产品验收。
