# TaskGraph 实施就绪优化交接

**编号：TG-READY-1.0｜日期：2026-09-21（UTC+08:00）**  
**类型：对 TG-EXEC-2.0 的施工门禁、证据与离线预检补充；不修改其目标语义，不授权立即实现完整 TaskGraph。**

## 0. 裁定与 NEXT ACTION

| 问题 | 当前裁定 |
|---|---|
| 可以开始 TG-A 吗？ | **可以，仅只读 source-map、Store/SQL 接口核对、隔离完整 schema 库的预检、fixture/nodeid 规划。不是完整 TG-A 业务实现或生产 opt-in。** |
| 可以开始 TG-B 吗？ | **不可以。** H1-H 三来源及唯一 Commit 接缝未通过，source-map 未完整，本地 SDK migration 未验收。 |
| 可以开启生产路径吗？ | **不可以。** TG-C/D/E、Host、真实模型、H1 完整门禁均未完成。 |

**NEXT ACTION：**使用候选项目 `.venv` 运行 `tools/capture_sources.py`，输出到 Host 已忽略的 `.local-test-evidence/2026-09-21/taskgraph/<run-id>/`；检查真实候选指纹、H1 缺失符号和 migration runner。不要从远端更新仓库，不要注册或打开 TaskGraph 运行开关。

## 1. 本轮依据与不能声称完成的事项

1. 用户 handoff 报告：候选 `102ad3dfa2db38d575ea929d39ec5ed1561a71da`，分支 `codex/h1h-impl`；SDK 主线 `51dbed2eaf81d3225bcb15911c33838a79c1fcd3`；三个 worktree 都有 dirty 修改。以上是**用户报告**，不是本轮读取本地文件的结果。
2. 附件 TG-EXEC-2.0、H1H-ADM-1.0 及各自资料包已核对。原始 42 场景、12 mutation、H1H 36 focused 组不重编号、不删断言。
3. 此环境不能读取 `/Users/denny/...` 或未提交 diff。尝试取远端候选 ref 也未能取得。`source-map.seed.json` 因此明确是 `PENDING_LOCAL_CAPTURE`，没有伪造 SHA-256、等价符号、真实 nodeid 或 SDK PASS。
4. 远端 `5ac3f058…` 仅核查了历史 migration runner 实现：它的 `Store.open → _initialize_or_validate → _apply_migration` 读取 `schema.MIGRATIONS`。**不将这个 SHA 或实现当成本地目标**。
5. 原资料包 87 reference tests、8 Schema、9表在测试父表上通过，是 handoff 中的 reference 证据；42/12 仍 PENDING。此次工具自测另列 `VALIDATION.md`。

## 2. 对旧计划的执行边界修正（不改业务协议）

| 旧表述/易误解处 | 本补充的明确含义 |
|---|---|
| §14「TG-A 可提前离线开发」 | 现在只准 **TG-A-prep**：采集、隔离副本、迁移/SQL预检、接口与测试规划。不能趁此实现 opt-in、grant 签发、派发或收敛。 |
| §15.1 使用 `python` | 所有本地命令改用候选 `.venv/bin/python`；没有环境即停止。可用项目 `uv run --frozen --no-sync --group dev python`，不自动 sync/pip install。 |
| `source_complete` 一个布尔值 | 分成身份已采集、符号存在、等价合同已审、真实行为通过。AST 找到函数不能解除 H1-H blocker。 |
| 预检探测不到 `commit_admitted_plan` | 按 H1H-ADM 补真实唯一入口和同事务接线；不能在 TaskGraph 内另建一个同名空壳。 |
| 42场景映射 | 先固定 target nodeid；只有本地 pytest collection 能填写 actual nodeid；只有真实运行才可 PASS。 |
| TG-A schema成功 | 必须真实 SDK 完整迁移链，不是 `tests/parent_fixture.sql`；process-local候选DDL试验、真实注册迁移、带旧数据回归分开记。 |
| H1-H focused通过 | 还需独立核验才可解除 TG-B 前置；H1-I/完整H1仍是后续运行/生产门，不能被Shadow替代。 |

## 3. 一次性来源对齐：产物和判定

`source-map.seed.json`：保存用户报告身份、24项原来源、7项runner/H1扩展核对点和缺失符号。不把计划新增文件当成已有实现。

`tools/capture_sources.py` 在用户机器生成 `source-map.local.json`：
- 三仓真实 HEAD、branch、index digest、dirty 文件清单及工作树 SHA-256；删除/rename/symlink 分开记录。
- 候选 `src/tests/scripts/schemas` 和受控配置的完整文件 hash；identity = HEAD + index digest + 这些字节 + dirty 清单，不只钉 HEAD。
- 精确 AST 定位到类/方法、起止行、签名；重复同名/缺失不会被自动选中。
- 源码冻结复制到同一个 ignored 证据目录，包含当前工作树的真实 dirty 内容。不是 checkout/cherry-pick；不复制 `.git/.venv`，不导入SDK、不触碰库。
- 采集前后对照指纹，变化则整次快照不合格。

`equivalences.json`：默认空。真实等价实现须填写 path、qualified symbols、文件 hash、reviewed_by、合同证据、focused nodeids。禁止搜索到“名字相似”自动降格通过。

source-map 所有行有四种实施分类：`EXISTING_REUSE`、`H1H_PREREQUISITE`、`TASKGRAPH_NEW_TARGET`、明确审阅的等价路径。未来新增目标允许尚不存在；必须复用的现有/前置来源缺失不允许伪装成NEW绕过。

**SOURCE_MAP_COMPLETE 需要人工/独立核验合同映射；capture脚本永不自行把它置 true。**未读到的Host route与架构入口仍为空并阻塞对应阶段。

## 4. H1-H 依赖闭合在自己的工作流中完成

详情见 `H1-H-DEPENDENCIES-ALLOWLIST-TESTS.md`。本次不重新设计授权/Operation/PlanShape；逐条继承 H1H-ADM-1.0 §2–6。

正式入口链必须是：

```text
真实 request / decision
→ pre_admit_planning_decision
→ adapt_for_preview
→ preview_candidate（纯计算，无取消/写库/模型）
→ admit_compiled_plan
→ CommitService.commit_admitted_plan
   → 同 Store 写事务复核 grant/operations/runtime/read-set
   → 原 commit_plan_revision
   → 同事务 decision/receipt/followup
```

`WAIT/NO_CHANGE/DECLARE_BLOCKED` 走 NoPlanMutation，不调用图编译和operation mapper；仍检查request/身份。decode-only两类继续拒绝执行。PR-7 Shadow和NanoJev结果不为此链提供权限。

## 5. TG-A 预检新增的具体检查：SQL触发器与runner

原TaskGraph DDL有25个 `CREATE TRIGGER`。历史参考 `_apply_migration()` 使用 `migration.ddl.split(';')`，会把触发器内部语句切碎。

这说明原reference parent fixture上的执行成功**没有证明真实runner兼容**。当前候选是否已修复必须本地检查，不预判失败。

本包probe通过真实候选Store在临时完整schema复制库上先运行一个trigger smoke：成功后再评估完整扩展。若失败，报告 `MIGRATION_RUNNER_TRIGGER_UNSUPPORTED`；不能偷偷换成 `executescript()` 使测试绿，也不能修改原DDL删除trigger。

`reference/sql_statement_iterator.py` 是拟议的边界拆分参考，不自动patch。若本地确有该问题，提交窄范围 `TG-A-RUNNER-01` 变更申请：仅修改 `_apply_migration` 的statement迭代，旧DDL bytes/checksums不变，保留原事务。先在隔离源码副本测试，独立核验后才可移植到候选。`complete_statement` 只检测语句是否完整，真正语法仍由 `connection.execute` 判断；不能把它作为validator。

## 6. 精确施工顺序

1. 采集三worktree + 附件/本地裁定hash，保持所有dirty内容。
2. 审阅H1三链和`commit_admitted_plan`的真实路径；由既有H1-H工作流补代码，TaskGraph不替它造默认值。
3. TG-A-prep同时做Store/SQL键/触发器runner预检和测试映射。缺父表时**记录BLOCKED**；不拼测试父表冒充实际候选库。
4. H1H schema和数据来源到位后，在新的隔离完整库重跑真实runner新建、升级、rollback、FK、checksum、旧数据保持；注册编号先比较完整迁移清单。
5. `SOURCE_MAP_COMPLETE + H1_H_READY + TASKGRAPH_TG_A_VALIDATED` 才进入TG-B真实compiler/resolver接线。
6. TG-C、TG-D、TG-E按原计划实施；安全不变量回归随每片执行，不推迟到最后统一碰运气。
7. 最终统一跑42场景、12mutation、stateful、legacy/fullH1、Host、获准GPT-5.6专项和独立核验。生产启用还需明确操作人批准。

## 7. 测试与证据

详见 `TEST-NODEID-MAP-42-12.md` / `sdk-nodeid-map.json` / `mutation-nodeid-map.json`。

- 42组原输入、步骤、断言完整保留在JSON；原有等价测试可映射但不得少断言。
- reference PASS 不会填入SDK结果。`--collect-only` 只证明可收集，不证明测试通过。
- 真实Store/Compiler/Commit/Validity/manifest resolver与H1producer不可mock；固定provider响应只用来驱动真实内核。V06真实模型部分不可stub。
- stateful单独排程：200 examples×50步骤，对照完整重算。模型测试最后、固定候选与预算，避免前置结构错误浪费token。
- 所有原始日志/截图/JUnit/SQLite/trace/本地源码副本留 `.local-test-evidence/`；Git只留人工审阅过的结论、命令、nodeid、相对索引、hash。**不得直接Git提交原包中的 `reference-tests.log` 或本轮原始日志。**

## 8. 当前状态

`REFERENCE_KIT_PASS=PASS` 仅按用户报告和精确原输入包；`SOURCE_MAP_COMPLETE=PENDING`；`H1_H_READY=BLOCKED`；`TASKGRAPH_TG_A_READY=PENDING_LOCAL_PREFLIGHT`（离线准备已获准）；`TASKGRAPH_TG_A_VALIDATED=BLOCKED`；Core/Host/模型/生产均BLOCKED。

所有SDK gate绑定的candidate_sha是用户报告值，`candidate_fingerprint_observed=null`。本轮不伪造真实dirty身份。详细依赖与通过条件见 `STATE-GATES.md`。

## 9. 交接给WorkAgent的指令

> 不改变TG-EXEC-2.0目标语义，不打开生产。先用本包脚本采集当前候选dirty字节，不追远端。将所有缺失来源如实保留BLOCKED。H1三链及唯一Commit由H1H-ADM的allowlist处理。TG-A当前只做离线核对与隔离库验证；真实runner、真实父表、真实注册checksum均未证明前，不得用reference fixture充数。42场景先登记target nodeid，本地collect后登记actual nodeid，真实执行后才能PASS。所有原始证据保留ignored目录，Git只提交摘要和hash。现阶段TG-B和生产路径均不允许。
