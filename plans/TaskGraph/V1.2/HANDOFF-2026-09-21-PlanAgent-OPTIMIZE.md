# TaskGraph V1.2 PlanAgent 优化交接

**编号：TG-READY-1.2-HANDOFF · 日期：2026-09-21（Asia/Shanghai）**  
**目标：修订 V1.2 的执行前置，使后续 WorkAgent 能在证据闭合后安全进入 TG-A/TG-B。**

## 1. 当前裁定

V1.2 仍需要 PlanAgent 优化。当前只完成了 TG-A-prep 的局部准备，不能开始 TaskGraph 生产实现，也不能打开生产 opt-in。

| Gate | 当前状态 | 说明 |
|---|---|---|
| Reference kit integrity | PASS | 原始 V1.2 ZIP 文件完整性通过 |
| Source capture | PASS_WITH_SOURCE_MAP_INCOMPLETE | candidate 身份已冻结，但 source map 仍不完整 |
| SDK migration | BLOCKED | 真实 Store runner 的 trigger smoke 失败 |
| H1-H registry | STATIC_ONLY | 36/36 case 静态读取成功，未执行 |
| H1-H collection | COLLECTED_NOT_RUN | collection 成功，未执行 SDK case |
| H1-H coverage bind | BLOCKED | 参数化 nodeid 无法与 registry 基础 nodeid 精确绑定 |
| Commit seam equivalence | BLOCKED | C01–C10 没有行为收据，合同等价未证明 |
| TG-A validated | BLOCKED | 迁移、H1-H 和合同门禁未闭合 |
| TG-B/C/D/E | NOT_ALLOWED | 依赖尚未满足 |
| Production enablement | BLOCKED | 必须保持关闭 |

## 2. 本轮真实身份

- Candidate：`/Users/denny/projects/simple-harness-sdk-h1h-impl`
- Branch：`codex/h1h-impl`
- HEAD：`102ad3dfa2db38d575ea929d39ec5ed1561a71da`
- Candidate fingerprint：`d9c331ff016b76cb4868c7bee4dc98ba8ecc13343018db70ebadb4c2a383a6dd`
- Evidence run：`/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-21/taskgraph/20260921T114218-39887`
- 原始 V1.2 ZIP 保持不变：`plans/TaskGraph/V1.2/taskgraph-v1.1-prep-complete-2026-09-21.zip`

主仓库和 candidate 原本都有 dirty 修改。PlanAgent 不得 reset、clean、rebase、覆盖或替换这些修改。

## 3. 必须由 PlanAgent 解决的事项

### 3.1 Commit seam 合同

计划合同期望 `CommitService.commit_admitted_plan`；candidate 当前实际是：

```text
PlanningAdmissionCommitsMixin.commit_planning_revision
```

允许复用现有入口，但只有 C01–C10 全部具备源码、实际行为 nodeid、文件 hash 和独立审阅收据后，才能登记 `EQUIVALENT_USE_EXISTING`。本轮没有这些收据，当前应按 `NON_EQUIVALENT_H1H_FIX_REQUIRED`/BLOCKED 处理。

重点修复或证明：

- 输入必须是两阶段准入后的冻结类型，不能用原始模型 JSON 或 `PreAdmitted` 对象。
- commit 事务内重读真实 authorization、operation、running-work 和 read-set。
- preview、delta、source snapshot 必须是同一份冻结身份。
- Plan/decision/receipt/outbox 必须同事务写入并整体 rollback。
- 同命令重送返回原 receipt，异内容冲突，不能重复派发。
- 所有新协议调用边都经过 admission guard；明确区分 legacy/compatibility 直调。

不得为了通过 AST 检查制造空壳函数、宽 `*args/**kwargs` 包装、默认授权或旁路提交。

### 3.2 Migration runner

真实 probe 已报告：

```text
MIGRATION_RUNNER_TRIGGER_UNSUPPORTED
OperationalError: unrecognized token: "'negative value"
```

PlanAgent 应在 H1-H 工作流中修复真实 runner 的多语句/trigger 拆分边界，并用隔离源码副本验证：

- 新建完整 schema
- 升级已有完整 schema
- 25 个真实 trigger
- 多语句 trigger、字符串/注释内分号
- 失败整体 rollback
- FK、checksum 拒绝、旧数据保持、幂等 reopen

不得删除 trigger、修改原始 TaskGraph DDL bytes/checksum、用 reference fixture 冒充真实父 schema，或在本次 prep 中直接注册生产 migration。

### 3.3 H1-H nodeid 映射

静态 registry 已读取 36 case、35 个唯一 registry nodeid。collection 后出现参数化 nodeid：

```text
tests/orchestrator/full_target/test_h1h_plan_preview.py::test_state_free_decisions_stop_before_shape_or_operation_preview
```

实际 collection 展开为 `[wait]`、`[no-change]`、`[declare-blocked]`。需要修复 registry/adapter，使 case → 参数化 nodeid → assertion coverage 的映射明确且可审阅。修复后先重新 collection，再绑定；仍然不得把 collection-only 当作 PASS。

### 3.4 Source map

当前 31 项 requirement 的观察状态：

- `PRESENT_UNVERIFIED_BEHAVIOR`：17
- `PLANNED_ABSENT`：11
- `SYMBOL_MISSING_OR_AMBIGUOUS`：3

缺失/歧义包括 P02、P09、P19；TaskGraph store/source/convergence/API/replay/followup 仍是 planned absent。必须重新生成 source map，并由独立审阅确认等价关系；不能把计划目标自动标成已有实现。

### 3.5 交付包自测

原始包在当前 macOS 重跑为 55 pass / 1 error，错误来自 `collect_h1h.py` 的 `/var` 与 `/private/var` 路径规范化。只读 evidence 副本中窄修复后为 56/56，但原始 ZIP 未修改。请正式修订交付包并重新生成 manifest/hash；不能把 evidence 副本的 56/56 直接宣称为原始包结果。

## 4. 禁止事项

- 不实现 TG-B compiler/resolver、TaskGraph Store、Host route、真实模型或生产 opt-in。
- 不执行 H1-H focused suite、42 SDK cases、12 mutations 来制造通过数量；在合同和 migration 阻塞未解除前保持未执行。
- 不修改当前 dirty candidate 的无关文件，不覆盖主仓库已有计划/架构文档。
- 不写入 `.env`、secrets、keychain 或任何凭据。
- 原始日志、源码快照、SQLite、collection 产物继续留在 `.local-test-evidence/`；Git 只提交人工审阅后的结论、命令、nodeid、相对索引和 hash。

## 5. 复核证据

- [执行总收据](/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-21/taskgraph/20260921T114218-39887/prep-execution-receipt.json)
- [source map](/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-21/taskgraph/20260921T114218-39887/source-capture/source-map.local.json)
- [migration report](/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-21/taskgraph/20260921T114218-39887/source-capture/migration-probe/migration-report.json)
- [H1-H coverage](/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-21/taskgraph/20260921T114218-39887/source-capture/h1h/coverage.local.json)
- [commit seam review](/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-21/taskgraph/20260921T114218-39887/source-capture/h1h/commit-seam-review.local.json)
- [prep tests before narrow fix](/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-21/taskgraph/20260921T114218-39887/prep-tests-before-fix.log)
- [prep tests after narrow fix in ignored copy](/Users/denny/projects/simple_harness/.local-test-evidence/2026-09-21/taskgraph/20260921T114218-39887/prep-tests-after-fix.log)

## 6. PlanAgent 完成条件

PlanAgent 只能在以下证据齐全后把 V1.2 标为可执行：

1. Commit seam 的 C01–C10 有实际 source refs、hash、focused nodeids 和行为收据；
2. H1-H 参数化 nodeid 映射完整，36 case 可绑定且仍标记 `NOT_RUN`；
3. migration runner 在隔离真实 candidate source 上通过 trigger、rollback、FK、checksum、旧数据保持和幂等检查；
4. source map 明确区分 existing、H1-H prerequisite、TaskGraph new target 和 approved equivalence；
5. 完整交付包自测在原始包上通过，manifest/hash 与工具字节一致；
6. `SOURCE_MAP_COMPLETE`、`H1_H_READY`、`TASKGRAPH_TG_A_VALIDATED` 均由独立核验确认后，才允许进入 TG-B。

在上述条件之前，最终状态必须保持：

```text
TG-A-prep: PARTIAL / BLOCKED
TG-B/C/D/E: NOT_ALLOWED
production_enablement_allowed: false
```
