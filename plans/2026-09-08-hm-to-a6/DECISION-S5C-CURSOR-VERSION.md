# 决策：user_version 封闭白名单统一为迁移链派生 + Procedure 提示与 memory_types 解耦

日期：2026-09-09
工作树：`.claude/worktrees/s5c-cursor-version`（分支 `worktree-s5c-cursor-version`，基线 `243369c0`）
来源复核：`plans/2026-09-07-corpus-c01-local/RUN-RERUN-FLASH-01-REVIEW.md`（提交 `786ba9de`）§1、§2
相关前置：[DECISION-HISTORY-TOOL-CALL-ARGS.md](DECISION-HISTORY-TOOL-CALL-ARGS.md)（引入 v55 的那一步）

---

## 1. 事项一（生产回归）：v55 上每一次预约登记都失败

### 1.1 现象与根因

`865bfe7a` 落 `migrations/primary/047_primary_assistant_tool_calls_v55.sql`，把
`PRAGMA user_version` 由 54 抬到 55。`backend/deskpet/memory/s5c_store.py:93` 用一个**字面量元组**
挑选游标表：

```python
"prospective_outbox_cursor_v52" if db.execute("PRAGMA user_version").fetchone() in ((52,), (53,), (54,))
else "prospective_outbox_cursor"
```

55 不在其中 → 静默退回 v50 旧表；而 `s5c/044_prospective_terminals_v52.sql:35-36` 早已用
`CREATE TRIGGER s5c_cursor_v50_sealed ... RAISE(ABORT,'s5c_cursor_successor_required')` 把旧表封死，
于是 `s5c_store.py:384` 的 INSERT 必抛 `sqlite3.IntegrityError`。

**影响面不是语料跑道，而是生产**：`runtime_composition.py:91` 的 `ProspectiveRuntimeLane`
用的就是同一个 `S5cStore`——凡是 v55 库，任何提醒的登记都无法落定，SDK typed-recall 的
类型授权闸门因此永远扣留该提醒。语料侧表现为 20 例 SETUP_BLOCKED。

### 1.2 裁决：**接受版本集合一律从迁移链派生，禁止字面量集合**

用户授权本代理裁决技术取舍。三个候选：

| 候选 | 结论 | 理由 |
|---|---|---|
| (a) 把 `55` 补进那个元组 | **否决** | 复核明确点名的反模式：下一次迁移照样会忘。同一个错误在本仓已经复制了 8 处 |
| (b) 改成 `>= 52` 的开区间比较 | **部分采纳，但不够** | 语义对（可加性迁移），但它把"未来任意整数"也一并接受了，与 `prospective_signal_store` 原注释"不接受任何未来整数"的口径冲突；也无法在**新增迁移却忘了登记**时报错 |
| (c) 新增 `deskpet/memory/schema_chain.py`：**链从迁移文件本身发现**，所有准入判断由链派生，并要求每一步在链登记表里登记自己的 module/validator/initializer | **采纳** | 既保住"只接受已发布版本"的封闭性，又让"忘记更新"从静默降级变成**测试期红灯**（守卫测试） |

`schema_chain` 只依赖标准库，不在导入期打开数据库、不导入任何 schema 模块，因此可以被
每个 store / schema 模块无环引用。

- `DOMAIN_CHAIN` — 扫描 `migrations/{s5c,procedure,primary}/*.sql`，按 `NNN_name_vNN.sql` 解析，
  断言序号与版本自 `BASE_SCHEMA_VERSION+1 = 50` 起连续，否则导入即 `SchemaChainError`。
- `accepted_versions(introduced_at)` — "在 V 首次发布的能力，存在于 V 及其后的每个链版本"。
  取代全部字面量元组。语义前提是**迁移可加**（不删前驱表）；若将来出现非加性迁移，
  必须改这条派生规则本身，而不是留一个过期字面量。
- `DOMAIN_SCHEMA_STEPS` — 新迁移唯一需要手写的一行（module / validator / initializer）。
  `domain_validator()` / `domain_initializer()` 以 importlib 惰性解析，未登记的整数返回 `None`
  （调用方保持各自的 fail-closed 兜底），绝不回退成前驱的校验器。

### 1.3 已修的全部封闭白名单

| 文件 | 原字面量 | 现 | 是否为线上缺陷 |
|---|---|---|---|
| `memory/s5c_store.py:93` | `((52,),(53,),(54,))` | `accepted_versions(52)` | **是**（本次主因） |
| `memory/procedure_use_store.py:235,237` | `== 53` / `!= 54` | `accepted_versions(54)` + `== 53` 旧字节分支 | **是**（v55 上 Procedure 重试谱系整体被拒，同类潜伏缺陷） |
| `memory/procedure_use_store.py:264` | `!= 54` | `accepted_versions(54)` | **是**（`append_attempt` 同上） |
| `memory/procedure_use_store.py:299` | `!= 54` | `accepted_versions(54)` | **是**（`resolve_procedure_observation_authority` 同上） |
| `memory/prospective_signal_store.py:42` | `not in (51,52,53,54,55)` | `accepted_versions(51)` | 否（当时已含 55） |
| `memory/prospective_signal_store.py:44` | `in (53,54,55)` | `accepted_versions(53)` | 否 |
| `memory/s5c_schema.py:35` | `(50,51,52,53,54,55)` | `accepted_versions(50)` | 否 |
| `memory/s5c_timer_schema.py:33` | `(51,...,55)` | `accepted_versions(51)` | 否 |
| `memory/s5c_timer_schema.py:67-82` | 5 段 `elif == N` 阶梯 | 链登记表 | 否 |
| `memory/s5c_timer_schema.py:87` | `(51,...,55)` | `accepted_versions(51)` | 否 |
| `memory/s5c_terminal_schema.py:61` | `(52,53,54,55)` | `accepted_versions(52)` | 否 |
| `memory/s5c_terminal_schema.py:112-128` | 4 段阶梯 | 链登记表 | 否 |
| `memory/procedure_schema.py:26` | `(53,54,55)` | `accepted_versions(53)` | 否 |
| `memory/procedure_schema.py:68-80` | 3 段阶梯 | 链登记表 | 否 |
| `memory/procedure_recovery_schema.py:24` | `(54,55)` | `accepted_versions(54)` | 否 |
| `memory/procedure_recovery_schema.py:66-74` | 2 段阶梯 | 链登记表 | 否 |
| `memory/primary_tool_call_schema.py` | 无 `_expected_user_version` 形参（下一步必踩） | 补齐同形状 + 链阶梯 | 否（预防） |
| `memory/schema.py:528-536` | `_validate_composed_extension` 里第二份手维护的 `{50:…,55:…}` 校验器字典 | `schema_chain.domain_validator(version)` | 否（fail-closed，但同属「必须记得改」的清单） |
| `memory/schema.py:897,949` | `maximum_human_schema_version=55` | `schema_chain.HEAD_SCHEMA_VERSION` | 否（fail-closed，但同样会过期） |

**审计后确认为"开区间、无需随迁移更新"因而保留**：`main.py:8504/8517/9079`（`>= 35`）、
`sdk_adapters/context_authority.py:407`（`< 45`）。**范围外保留**：
`scripts/restore_state_db_backup.py:73`（`!= 31`）是人工恢复脚本，改动它等于改恢复语义，
不在本次审计范围（复核限定 `memory/*schema*.py` 与 `*_store.py`）。

各步骤"自己的版本号"（如 `PRAGMA user_version=52`、`if current != 51`）保留字面量：那是
不可变的历史身份，不是会过期的白名单；它们现在统一由各模块的 `SCHEMA_VERSION` 常量表达。

### 1.4 守卫测试（`tests/memory/test_s5c_schema_chain.py`，7 条）

按"当初能最早拦住它"的顺序：

1. `test_no_closed_user_version_allow_list_survives_in_memory_modules` — **源码 AST 扫描**：
   遍历 `deskpet/memory/*.py`（`schema_chain.py` 除外），两种形状即失败——① `In`/`NotIn`
   比较右侧是含 49–69 区间整数的字面量元组/列表/集合（`S5cStore` 踩的那个）；② 以该区间整数
   为键的字典字面量（`schema.py` 那第二份校验器映射）。这是本类缺陷的直接护栏；
   补这条测试时它当场又抓出了 `schema.py:528` 一处。
2. `test_every_discovered_migration_registers_its_step` — 新迁移文件若未登记进
   `DOMAIN_SCHEMA_STEPS`，或模块 `SCHEMA_VERSION` 与链不一致，则红。**已实测**：临时放一个
   `primary/048_fake_step_v56.sql` 后该测试与 `test_head_module_owns_the_head_version` 立即失败。
3. `test_full_chain_to_head_registers_a_prospective_record` — 端到端事实：跑完整迁移链到当前
   链首（`domain_initializer(HEAD)`，不写死 55），再用真 `S5cStore.commit_registration` 落一条
   登记，断言 v52 游标表 1 行、被封死的 v50 旧表 0 行。这正是本次回归打断的那条链路。
4. 另有链连续性/链首一致性、v52 起各版本都选新游标表、50/51 仍选旧表共 4 条。

### 1.5 两处可观测性缺口

- `memory/prospective_runtime.py` 新增 `failure_identity(exc)`：在 `type=` 之外补
  `sqlite=SQLITE_CONSTRAINT_TRIGGER`、`sqlite_code=1811`，并沿 `__cause__`/`__context__`
  最多 8 层找到首个 `sqlite3.Error`（本次真实形态就是被包一层）。
  **payload-free 的判据**：SQLite 报文只有整条消息**完全**是 `^[a-z][a-z0-9_]{0,63}$`
  蛇形 token 时才记录——那正是我方 DDL `RAISE(ABORT,'...')` 的形状；`UNIQUE constraint failed: u.a`
  这类含 schema 文本的消息一律只留结果码。已用含中文内容的 UNIQUE 冲突做反向断言。
  三处 `log.warning` 全部改用它。
- `quality/corpus_prospective.py`：`ProspectiveSetupNotReady` 现在携带 `receipt`（与成功路径同一份），
  失败前把 `setup_error` 写进回执，`tick_errors` 由类型名升级为同一个 `failure_identity`；
  `quality/corpus_scoring_session.py:536` 在 `SETUP_NOT_READY` 分支把回执写进
  `outcome["prospective_registration"]`。本次回归里 20 例只留下一句原因字符串，
  连"哪些提醒被播种、跑了几个 tick、哪条约束拒绝了写入"都要手工重建。

---

## 2. 事项二（F-ETR-5）：`procedure_hint` 与 `memory_types` 解耦

### 2.1 根因

`MEMORY_TYPE_SELECTION_POLICY` 规则 R4 正确地劝阻模型请求 typed recall 根本不供给的
`procedure` 类型；而 `sdk_adapters/context_route.py:510` 的提示恰恰以
`"procedure" in memory_types` 为触发条件。R4 一生效，提示就对**它本该服务的那一类请求**静默了：
C06 `procedure_discover` 调用率 18/19 → 14/19（同批 A/B：带提示 4/4 调用，不带 10/15）。

### 2.2 裁决：触发条件改为"请求本身"，不再是"类型选择"

新触发规则（三者取或，且**召回结果中没有任何 procedure 片段**仍是硬前提）：

1. `"procedure" in memory_types`（保留旧行为，向后兼容）；
2. `indicates_workflow_request(query)` — 查询呈"这事怎么做"形态；
3. 该 Run 已经绑定过 TaskScope（`ledger.latest_route_decision_for_run(run_id, task_only=True)`）
   ——在任务里干活时，"存了但未绑定的工作流"正是 typed recall 会扣留的东西。

**提示 payload 一字未改**（`_PROCEDURE_HINT`），仍只出现在结果顶层、不入 `ContextRouteReceipt`；
PERSONA 对它的描述依旧准确，未改。

`indicates_workflow_request` 放在 `memory/recall_selection.py`——与 R4 政策原文同址，
且与 `selection_policy_departures` 同性质：**纯函数、无 gold、纯建议**，任何调用方都不得据此
门禁/改写召回。词表为闭集（中文简繁：流程/步骤/怎么做/怎样做/如何做/如何操作/操作方法/做法/工序；
英文小写匹配：workflow/procedure/runbook/playbook/sop/checklist/steps/step by step/how do i/how to）。

两处成本取舍：

- **多一次账本读**：仅当前两个纯判据都不成立时才发生（`or` 短路），且是单行索引查询。
- **失败不得毁掉已成功的召回**：`_run_is_task_scoped` 内部吞掉异常并记
  `context_route_procedure_hint_scope_unavailable`，返回 False。已有测试覆盖。

误报边界：`test_plain_question_outside_any_task_scope_still_gets_no_hint`（"我常用的日期格式"）与
既有的 `test_unrequested_procedure_type_gets_no_unsolicited_hint`（"上周做了什么"）都仍不出提示。

---

## 3. 测试

命令（一次一个 pytest 进程，具名文件）：

```
tests/memory/test_s5c_{consumer,consumer_sdk,same_timestamp_cursor,store,terminal_schema,timer_schema,schema_chain}.py
tests/memory/test_primary_tool_call_schema.py
tests/memory/test_prospective_*.py
tests/quality/test_corpus_prospective_settlement.py
tests/sdk_adapters/test_context_route_tool.py
tests/memory/test_recall_selection_policy.py
tests/sdk_adapters/test_context_route_prospective_runtime.py
```

| | 基线 `243369c0` | 本次 |
|---|---|---|
| 通过 | 154 | 193（+36 新增，+3 为链首模块 `test_primary_tool_call_schema.py` 纳入同一条命令） |
| 失败 | 20 | 20（**同一份清单，逐条一致**） |

另单独跑 `tests/memory/test_procedure_{schema,recovery_schema,recovery_runtime,duplicate_recovery}.py`
（因改了 `procedure_use_store.py` 的三处 `!= 54`）：12 通过 0 失败。

20 条既有红全部是基线红，与本次改动无关，两类：

- 11 条 `tests/memory/test_prospective_consumer_m617.py:105` 的 `RecursionError`（`World.__getattr__`
  在 manager 构造失败后自递归；即复核所述 `SQLiteHumanMemoryBackend.__init__() got an
  unexpected keyword argument 'clock'` 一类环境不同步）；
- 9 条其余（`test_s5c_store.py` 两条 `DID NOT RAISE ... future_database`、
  `cognitive_vector_generations` 缺表、`MandatoryContextActionExhausted` 等）。

验证方式：worktree 检出即基线 `243369c0` 干净树，改动前先跑了同一条命令取基线（**未用
`git stash`**）。`test_s5c_store.py` 的两条 `future_database` 红值得后续单独跟——它们与本次
链改动无关（改动前后完全一致），但指向 `inspect_startup_epoch` 在 v50 库上的 epoch 判定。

新增/改动测试共 36 条：链守卫 7、`failure_identity` 4、语料回执 3、`procedure_hint` 解耦 5、
`indicates_workflow_request` 17（含参数化）。

## 4. 未做 / 后续

- `scripts/restore_state_db_backup.py:73` 的 `!= 31` 未动（范围外，改动即改恢复语义）。
- `initialize_s5c_state_db` / `initialize_s5c_timer_state_db` 中 `version > 50` / `> 51` 的
  "future" 硬拒保留：链上层初始化器只在版本低于自身时下探，这两处不可达于 v52+。
- 上述 `test_s5c_store.py` 两条基线红。
