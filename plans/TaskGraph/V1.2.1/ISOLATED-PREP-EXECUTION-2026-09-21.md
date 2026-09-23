# TaskGraph 隔离准备执行记录

日期：2026-09-21。用户最新要求：不打扰 HTN 测试和修复，**等 HTN 完成后再接入其代码**。本约束优先于此前“接口检查点通过即可提前接线”的建议。此前 WAITING_FOR_HTN_COMPLETION 已被用户澄清替代：仅代码接入等待 HTN；独立准备继续并行。最新进展见 PARALLEL-PROGRESS-2026-09-21.md。

## 本轮完成

- 从保留的 V1.2.1 原包解压副本生成独立本地修正版；原 ZIP、Downloads 和 HTN candidate 未改。
- 两处测试修复：路径断言的期待值 resolve；临时 venv 在 POSIX 使用 symlinks，Windows 保留复制方式（Windows 未验证）。
- 更新本地修正版 DELIVERY-MANIFEST，增加本地变更说明；50 项文件完整性 PASS。
- 使用现有项目解释器只读运行工具自测，禁写 pyc、nice=15、无并行：90/90 PASS，0.406 秒，退出码 0。不是 SDK/H1-H 行为验证。
- 修复 patch 单独保存在同目录 LOCAL-MACOS-TEST-FIX.patch。可从补丁重建本地工具；原附件 VALIDATION 仍是上游环境记录。
- 本地修正版 ZIP 已生成于 ignored evidence，不加入 Git。SHA-256：1fe90d7aa9128143f0b0e1e8f72f7c5134add5885a8892dc7cb5daf435e8ed78。

没有修改 SDK 源码、依赖、DB、Host vendor/lock、共享 ARCHITECTURE；没有启动服务或模型，没有给 HTN 任务发消息、派工或中断。只读状态快照显示 HTN 任务仍 active。

## 上轮暂停范围（已被用户澄清替代）

新 candidate 扫描/冻结、SDK collection、迁移与批量测试、runner 回迁、migration 注册、TG-B/C/D/E 接线均等待 HTN 完成。避免追逐活动源码和与 HTN 测试争用资源。已归档的旧 fingerprint/测试结果只说明旧检查点，不声明为当前状态。

这比原建议的并行范围更保守：现在并行完成独立工具与接口资料，运行链工作留在 HTN 最终交接之后。

## HTN 接口核对表（规格映射，不是已实现认定）

| 接口组 | HTN 权威内容 | TaskGraph 后续核验与消费 | 必须防止的错误 |
|---|---|---|---|
| 固定请求/授权/操作来源 | H1-H 实际 request reader、grant lineage、Operation/action link、完整 running-work | source-map P02/P09/P19 与 taskgraph_sources 同事务一致读 | 名字相近就签等价、缺来源当空集合 |
| 准备贡献与效果完成 | V14-OP-COMPLETION §5 OperationCompletionReader：read_requirements/read_scope/read_current_effect，实际名称以交接源码为准 | DATA 经 accepted_outputs 精确取可读贡献；ORDER 经 occurrence_outcomes/settlement_facts 判断本范围真实完成 | 有准备 Acceptance 就释放 ORDER 或把 MIXED Task 判完 |
| 冻结预览与唯一提交 | 实际 preview、同一 compilation/read-set、C01–C10 守卫及原 receipt | TG-B/C 的原接口复用 | 预览 A 提交 B、第二套 Commit/授权 |
| 延期/冷恢复 | V14-OP-SEAMS D3 continuation、stop gate、CAS lease、原 raw/request/decision | TG-D 复用停止/核对/恢复，核对与 TaskGraph convergence 的职责边界 | 再问模型、旧 proposal-text 恢复新协议、UNKNOWN 假收敛 |
| schema/迁移 | HTN 最终真实 MIGRATIONS、Completion 四表/相关合同、Store runner | 从最终完整 schema 验证 TG DDL 及升级/回滚/旧数据 | 预定 v21、覆盖已登记 checksum、派生 PASS 算 candidate PASS |

HTN Completion 参考 SQL 的跨 Spec/Scope 重审唯一键问题属于 HTN owner 实施范围，TaskGraph 不替其改；接入时检查其回归收据。需要核对的具名模块若尚未落地，继续明确缺口，不创建默认实现。

## 何时算“HTN 完成”

1. HTN owner 提供最终完成结论与可定位的交接、源码身份、实际测试证据；任务 idle/completed 只说明一次运行结束，不能自动代表 HTN 完成。
2. 对照 HTN 既定范围检查剩余 BLOCKED/PARTIAL/NOT_COVERED；历史 36/36 和旧 full_target 不自动关闭当前门。
3. 确认测试和修复已结束、实际工作树稳定，并取得含必要 dirty/untracked 实现的可复现基线；不 reset/clean，不直接合并所有 dirty 文件。
4. 若 owner 仅报告部分完成或仍在修复，则继续等待；无需打扰其任务。只在出现实质阻塞或需要用户决定时说明。

完成后先重新 capture 并审接口，建立 TaskGraph 独立基线。若 runner 已被 HTN 修复，直接核对并复用，不重复套旧 patch。若仍需要修复，先在隔离源码验证。TG-B 接线继续要求 SOURCE_MAP_COMPLETE、H1_H_READY、TG_A_VALIDATED 与相关核验满足。用户的等待要求不是提前放行门禁；接入不等于发布/替换 Host 或合并主线。

## 证据与命令

证据目录：.local-test-evidence/2026-09-21/taskgraph/isolated-prep-215421。

本地修正版：.local-test-evidence/2026-09-21/taskgraph/isolated-prep-215421/taskgraph-v1.2.1-local-macos-fix.zip。

完整逐文件 SHA-256：.local-test-evidence/2026-09-21/taskgraph/isolated-prep-215421/evidence-index.json，索引 SHA-256：6be8cb333f0f4194ba8fb0cb4a44e332e8d3febad6572f432f4689594b33bbc0。

命令（PY 为已有 candidate/.venv/bin/python；KIT 为独立工具副本）：

```bash
PYTHONDONTWRITEBYTECODE=1 nice -n 15 "$PY" "$KIT/tools/verify_delivery.py" --root "$KIT"
PYTHONDONTWRITEBYTECODE=1 nice -n 15 "$PY" -m unittest discover -s "$KIT/tests" -v
```

历史本轮结论：TOOL_PREP=PASS；HTN_COMPLETION=WAITING。用户后继澄清后，SDK_CAPTURE/隔离 MIGRATION 已恢复并行推进，只有 INTEGRATION 等 HTN 完成。TG-B/C/D/E 按门禁推进，见最新进展。

## 后续检查已登记

已创建当前任务的每小时 heartbeat：`htn-taskgraph`（ACTIVE）。仅低频读取 HTN 状态；无可操作变化保持安静。完成判定按上文收据与基线检查，不能以一次 turn 结束自动接入；准备工作和接入仍在本任务处理，不向 HTN 派工。

## 用户澄清后的优先级

TaskGraph 继续并行准备与隔离验证；HTN 完成后优先切换到最终代码接入，再继续 TaskGraph。不得将等待集成扩为全部停工。每小时 heartbeat 已改为此规则。
