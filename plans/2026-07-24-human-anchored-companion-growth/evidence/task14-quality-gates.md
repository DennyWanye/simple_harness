# Task 14 — 故障、性能、隐私与权限门结果

> 日期：2026-07-26
> 范围：计划 Task 14；本文件不替代 Task 15 的真实 provider / Tauri 真人 E2E。

## 结论

- Companion 确定性门：`588 passed`。
- Skill / Memory 计划聚焦门：`127 passed`。
- Harness / Capability / Workflow 全量组合：`937 passed, 4 xfailed`。
- 本轮改动的跨模块聚焦回归：`105 passed`。
- Frontend：Vitest `851 passed`，TypeScript `--noEmit` 通过。
- Rust：`cargo check` 通过；`cargo test` 为 `78 passed`。
- 验收脚本最终输出：`DECISION: SHIP`。
- 故障矩阵、性能与隐私聚焦组合：`54 passed`。
- Kernel / Admission / child ReadyGate 在
  `PytestUnhandledThreadExceptionWarning` 升级为 error 后：`79 passed`。
- R4.5/Companion construction gate：全绿；`RunKernel` 仍保留唯一六个公开操作。

因此 Task 14 的产品质量门通过。Task 15 已进入真实 provider 与主消息页真人测试，
但真实 Relay 返回 HTTP 402，结果按外部阻塞记录，不把入口成功冒充成长闭环成功。

仓库级 `cargo fmt --check` 仍失败：当前 Rust 树存在大范围既有格式漂移，本计划没有
Rust 生产代码改动，因此没有为了制造绿色结果而批量改写无关文件。该项作为 Task 16
交付审计中的既有基线问题保留。

## 故障矩阵

`backend/tests/companion/fault_matrix.json` 保存 32 个可执行索引场景，
`test_fault_matrix.py` 直接复用 Task 1～13 的权威测试，不复制一套假的业务实现。
覆盖 ACK 前/后崩溃、cutover 每个前后 marker 步骤、回执缺失重放、forget fence、
profile A→B→A、旧 owner envelope、单 writer、禁止伪成功、late emit=0、
runtime/evaluation survivor=0、root/child lease 与 terminal release。

矩阵之外的 secret redaction、只读评测、双层高风险 effect policy、production
`memory_recall` 零写、detail owner/as-of/redaction/detail-changed、单 DDL/旧 writer
静态门由 `test_privacy.py` 覆盖。迁移、权限、通知、提醒、候选与激活的完整组合继续由
Companion 全量门覆盖。

## 性能与写放大

性能门使用真实
`KernelRunClient(ProductVenue) → RunKernel → ReActDriver →
SqliteExecutionUnitOfWork → ExecutionWriteLane`，保持
`WAL + synchronous=FULL`，没有旁路 provider ledger。每侧 20 个样本的确定性校准结果：

| 路径 | 基线事务 | 当前事务 | p95 基线 | p95 当前 | 回退 |
|---|---:|---:|---:|---:|---:|
| Final | 2 | 4 | 53.2 ms | 56.4 ms | +6.015% |
| ToolBatch(1)，new/existing goal | 9 | 13 | 114.4 ms | 120.8 ms | +5.594% |
| ToolBatch(3) | 11 | 15 | 117.6 ms | 124.0 ms | +5.442% |
| retry/fallback | 2 | 6 | 103.2 ms | 109.6 ms | +6.202% |

四条路径均低于 10% 门。Skill scope activation 已并入现有
`persist_react_boundary` 事务；child command、precreated child Run、
RunStartSnapshot 与 cloned lease 也共用既有 child command 事务。
authority audit 维持 execution DML authority=`1`、transaction starters=`53`。

## 复杂度预算

Task 8～13 的生产事实超过 Task 7 时的整体规模估算，因此保留历史
Universal Action envelope，并新增明确的 Companion Growth construction envelope；
没有把新增产品代码伪装成历史删除，也没有压缩多条 Python statement 或删除事务。

当前审计值：

| 指标 | 当前 | Companion 上限 |
|---|---:|---:|
| raw total LOC | 135578 | 135600 |
| adjusted total LOC | 135069 | 135100 |
| core LOC | 44713 | 44750 |
| Kernel 物理 LOC | 1277 | 1300 |
| public operations | 6 | 6 |
| unknown classifications | 0 | 0 |
| deletion budget | 255 | 255 |

`RunKernel` 中 Admission start/recovery 协作逻辑已抽到
`deskpet.harness.admission_launch`，但该模块仍诚实计入 Harness core；
没有通过 non-core exception 规避计数。`AgentLoop` 的 frozen Skill remount
实现抽到独立 helper，`AgentLoop` AST 范围为 3800；UoW transaction starter
保持 53。

## SQLite 测试生命周期修复

扩大 Kernel 组合时，aiosqlite worker 会在 pytest function event loop 已关闭后回调，
原断言虽然通过但会留下 `PytestUnhandledThreadExceptionWarning`。根因是部分历史测试
fixture 没有显式关闭其短命连接，而共享 writer fixture 只知道 ExecutionWriteLane。

修复后：

- 测试 fixture 跟踪每个 test 创建的 aiosqlite connection，并在同一 function loop
  teardown 中统一关闭；
- poisoned / final-owner writer close task 由测试 teardown 等待，不再成为 fire-and-forget；
- 相关 79 项用例把该 warning 升级为 error 后仍全绿。

这不是隐藏 warning；它把 worker 生命周期收敛到测试自己的 event loop。

## 进程与资源

- 最终 deterministic smoke：root PID、create-time、命令、后代与峰值 private memory
  记录在 `evidence/task14-automation/smoke-final-run.json`；退出后命令 scope 无存活进程，
  smoke 观测峰值并释放 `1099345920` bytes。
- 最终 R4.5 门的 PID/create-time、完整命令、运行中 private memory 采样与
  survivor=0 记录在 `evidence/task14-automation/r45-final2-run.json`。
- 2026-07-26 的最终 Harness 全量重跑为 `937 passed, 4 xfailed`，耗时
  `381.33s`；两个精确命令行进程自然退出后 survivor=0。
- 一次误用 `pytest --trace-config` 的诊断进程按精确 PID/create-time 清理，没有按镜像名
  广杀；对应 survivor=0。

原始自动化日志只含本地测试输出，不含登录凭据、shared secret 或 device key。
