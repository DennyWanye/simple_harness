# HTN 补齐·阶段 C 第 0 条：删金额计价残留——代码级删除清单

- 清点日期：2026-10-03；基于主分支 `89ddd7e9`；只读清点，未改代码、未跑测试。
- 依据：`HTN补齐-阶段B收尾-偏差裁决.md` 第 7-1 节（用量照记、不记金额；一次删干净；新迁移删列、不兼容旧库；不为旧执行池保留原字节）。
- 路径简写：`SDK/` = `sdk/simple-harness-sdk/src/agent_orchestrator/`；`SH/` = `sdk/simple-harness-sdk/src/simple_harness/`；`T/` = `sdk/simple-harness-sdk/tests/orchestrator/`；`Host/` = `backend/deskpet/`；`HT/` = `backend/tests/`；`FE/` = `tauri-app/src/`。
- 行号是 `89ddd7e9` 上的行号，动手前先 `git grep` 复核一次。

---

## 〇、先说结论

1. **范围**：编排层（`agent_orchestrator` 包）+ Host 编排服务/诊断/投影 + 前端两个页面 + 对应测试。约 **70 个文件、约 440 行**（含测试；SDK 源码 36 个文件，其中 1 个整删）。
2. **原生运行层 `simple_harness` 包里的金额（价格估算器、硬上限、调用记录里的价格快照、ARP 嵌入回执的 cost_micros）本次不动**，理由见第三节第 5 条。
3. **执行池身份字节基线（`HT/orchestration/pool_identity_baseline.json`）不会因为删金额而变**：基线只取原生执行池的身份（模型、上下文策略、ARP 档案、根标记文件等），不含价格表。删金额只需要把 `HT/orchestration/_pool_identity.py:127` 的 `price_table=None` 实参去掉。基线仍按裁决与阶段 C 提示词升版一起重生成一次。
4. **真正会变的持久身份有三处**（都在编排库里，不在执行池里）：
   - 每个派发意图里冻结的"供应方准入身份"（`provider_admission_fingerprint`，由 `SDK/runtime/provider_budget_guard.py:340-365` 的 `_identity` 算出，里面有 `requires_price`、`prices` 两个键）；
   - 每个任务的执行策略摘要（`SDK/orchestrator/taskgraph_execution_policy.py:96-100` 里的 `price_table`，以及 `RuntimeProfile.to_json()` 的 `priced`、`task.budget.to_json()` 的 `max_cost_micros`）；
   - 任务/步骤/尝试的预算 JSON（`Budget.to_json()` 带 `max_cost_micros`，`Budget.from_json` 遇到未知字段直接拒）。
   **后果：旧 orchestrator.db 整库作废，而且是"启动就失败"，不是"老任务跑不动"。** 见第三节第 2 条、第六节第 1 条，需要定口径。
5. **新迁移编号 38**，删 5 张表共 13 列，另需先删后建 1 个保证通道触发器。

---

## 一、全部出现处（按类）

处理方式缩写：**删** = 整行/整段删除；**改签** = 改函数/类型签名；**改 SQL** = 改 SQL 文字；**改断言** = 测试断言改写；**不动** = 有理由保留。

### 1.1 SDK 契约（类型与对外字段）

| 文件:行 | 是什么 | 怎么处理 |
|---|---|---|
| `SDK/contracts/models.py:162` | `Budget.max_cost_micros` 字段（任务/步骤/尝试预算的金额维度） | 删字段。`to_json/from_json/fits_within` 按字段遍历，自动跟着变 |
| `SDK/contracts/models.py:159` | `Budget` 文档串"a budget is more than money" | 改注释 |
| `SDK/governance/budget_limits.py:20` | 预算继承的维度名单里的 `"max_cost_micros"` | 删这一项 |
| `SDK/api/facade.py:70` | `CLOSED_BUDGET` 里的 `"max_cost_micros"`（对外入口拒绝的预算字段） | 删这一项；`facade.py:12` 文档串里 "money" 一词同步删 |
| `SDK/contracts/obligations.py:308、333` | `ObligationAccountView.consumed_cost_micros` 字段及其 `to_json` 键 | 删字段与键；`:310-311` 注释"Money and tokens are separate axes"改写 |
| `SDK/contracts/obligations.py:363、381` | 内部 `_Account` 的 `__slots__` 项与初值 | 删 |
| `SDK/contracts/obligations.py:518` | 生成视图时带 `consumed_cost_micros` | 删 |
| `SDK/contracts/obligations.py:542-563` | `ObligationLedger.record_spend(cost_micros=…)` 参数、校验与累加 | **改签**：去掉 `cost_micros` 参数，只留 `attempts`、`tokens`；文档串 `:552-553` 改写 |
| `SDK/runtime/connectors.py:63、72` | `OperationSpec.cost_micros_ceiling`（每次外部动作的金额上限）及其 `to_json` 键 | 删字段与键 |
| `SDK/runtime/model_router.py:62、133-134、142` | `RuntimeProfile.price_table`、`unpriced` 属性、`to_json()["priced"]` | 删三处；`:56` 类文档串 "price" 删 |
| `SDK/runtime/assembly.py:46、50-64、581、577` | `CONSUMER_PRICING_KEY`、`PriceTable` 类及其导出 | 删（`PriceTable` 整类；`CONSUMER_PRICING_KEY` 只被它用，一起删） |
| `SDK/runtime/assembly.py:99-100` | `OrchestratorConfig.price_table`、`hard_cap_micros` | 删两个字段 |
| `SDK/runtime/assembly.py:184-186` | `OrchestratorConfig.unpriced` 属性 | 删 |
| `SDK/runtime/assembly.py:211-226` | `OrchestratorConfig.policies()`（有价格表时走 `consumer_supplied` 分支） | 整个方法删（全库无调用方；唯一实际路径是 `_policies_for`） |
| `SDK/runtime/assembly.py:245-252` | `to_json()` 的 `"pricing"` 键 | 删 |
| `SDK/runtime/assembly.py:452-462` | `_policies_for()`：有价格表走付费策略，否则 `local_default()` | 改成直接 `ConsumerRuntimePolicies.local_default()`，函数可内联删掉 |
| `SDK/runtime/assembly.py:477-478、486` | 单配置路径给 `RuntimeProfile` 传 `price_table=config.price_table` | 删实参与文档串 |
| `SDK/runtime/assembly.py:8-10` | 模块文档串"explicit pricing mode … hard cap" | 改写 |
| `SDK/runtime/assembly.py:31` | `from simple_harness.execution.budget import BudgetPolicy, FrozenPriceEstimator` | 删（删完上面后不再用） |
| `SDK/governance/policies.py:287-288` | 部署配置字段名单里的 `"price_table"`、`"hard_cap_micros"` | 删两项 |
| `SDK/governance/promotion.py:60-61` | 同上（策略晋升不可改的字段名单） | 删两项 |
| `SDK/governance/provider_prices.py`（全文 70 行） | `ProviderPrice`：从原生调用记录里读价格快照、算金额、`known_charge` | **整文件删** |

### 1.2 记账账本（预留、结清、用量导入）

| 文件:行 | 是什么 | 怎么处理 |
|---|---|---|
| `SDK/governance/budgets.py:15-17` | 模块文档串"unpriced 模式金额列留 NULL" | 改写为只记 token |
| `SDK/governance/budgets.py:59-60` | `UsageFact.cost_micros`（第 4 个位置参数）与 `unknown` 注释"priced deployment, but the SDK could not price this call" | **改签**：删 `cost_micros`；`unknown` 含义改为"这次调用的 token 用量拿不到" |
| `SDK/governance/budgets.py:75-77、94-97、112-114、121、172-174` | `AccountSnapshot` 的 `reserved_cost_micros/settled_cost_micros/unpriced_settlements`、`remaining_cost_micros()`、`to_json` 对应键、从行读取 | 全删 |
| `SDK/governance/budgets.py:203-280` | `reserve(…, cost_micros, …)`：签名、文档串、金额余量检查 `:230-234`、`_apply(reserved_cost_micros=…)` `:248`、INSERT 列 `:262`、参数 `:271` | **改签 + 改 SQL**：删 `cost_micros` 参数与金额检查 |
| `SDK/governance/budgets.py:297-326` | `grow(subject_id, tokens, cost_micros)`：签名、校验、增量、UPDATE `:325-326` | **改签 + 改 SQL** |
| `SDK/governance/budgets.py:335-358` | `import_usage` 的 INSERT/UPSERT（`cost_micros`、`unpriced` 两列及其计算 `:353-354`） | **改 SQL**：只写 `input_tokens,output_tokens,unknown` |
| `SDK/governance/budgets.py:361-386` | `usage_for/known_usage_for` 返回 `(tokens, cost, unpriced)` | **改签**：只返回 tokens；所有解包处（`:420、:455、:491`、`tail_budget.py:204`）跟着改 |
| `SDK/governance/budgets.py:415-437、450-472、485-509` | `settle / settle_known / settle_at_upper_bound`：`_apply(reserved_cost_micros=…, settled_cost_micros=…, unpriced_settlements=…)` 与 UPDATE 里 `settled_cost_micros=?, unpriced=?` | **改 SQL**，删金额增量；`settle_at_upper_bound` 的 `cost = max(...)` `:493` 删 |
| `SDK/governance/budgets.py:519、576` | `costs_report / usage_flags` 的 SELECT 带 `cost_micros, unpriced` | **改 SQL** |
| `SDK/governance/budgets.py:192-199` | `_apply(**deltas)` 用参数名拼列名 | 不改代码；**风险点**：任何调用方漏删一个 `*_cost_micros=` 实参，运行到才报"no such column" |
| `SDK/governance/tail_budget.py:33、49、58` | `TailReserve.cost_micros`、`TailAllocation.cost_micros`（都是第 2/5 个位置字段） | **改签**：删字段（注意位置参数整体前移） |
| `SDK/governance/tail_budget.py:135、209、263、267、281` | 尾部预留：传 `cost_micros`、合计维度 `("tokens","cost_micros","tool_calls")`、UPDATE `reserved_cost_micros=reserved_cost_micros-?` | **改 SQL** + 删 |
| `SDK/governance/budget_tail_schema.py:8-13` | 迁移 13 的 DDL：给 `provider_token_grants` 加四个金额列 | **不动**（已发布迁移的文字参与校验和，改一字旧库就打不开；由迁移 38 删列） |
| `SDK/storage/store.py:1759-1760` | `budget_usage()` 的 SELECT 带 `reserved_cost_micros, settled_cost_micros, unpriced_settlements` | **改 SQL**（Host 投影和诊断都读它，漏改会在详情页/诊断导出时报库错） |
| `SDK/storage/obligation_store.py:90、144` | 读行列名单与 `consumed_cost_micros=int(row["spent_cost_micros"])` | **改 SQL** + 删 |
| `SDK/storage/obligation_store.py:199-222` | `record_spend(cost_micros=…)` 与 `UPDATE obligations SET spent_cost_micros=…` | **改签 + 改 SQL** |
| `SDK/storage/obligation_store.py:513、524-526` | 重建内存账时 SELECT `spent_cost_micros` 并回放 `record_spend(cost_micros=…)` | **改 SQL** + 删实参 |
| `SDK/storage/obligation_store.py:561、569、591` | UPSERT 列名单、`excluded.spent_cost_micros`、取值 | **改 SQL** |
| `SDK/storage/assurance_source_inventory.py:339` | 迁移 26 保证通道"源列清单"里 `obligations.spent_cost_micros` | 删这一项（清单文档串要求列变更同步改它；全库无运行时读者） |

### 1.3 编排（准入、派发、审阅、恢复、事件）

| 文件:行 | 是什么 | 怎么处理 |
|---|---|---|
| `SDK/orchestrator/commit_service.py:225-228` | `Reservation(tokens, cost_micros, tool_calls=0)`（`cost_micros` 是必填第 2 位） | **改签**：删 `cost_micros` |
| `SDK/orchestrator/commit_service.py:395-405` | 事件 `ReservationHeld` 载荷 `reserved_cost_micros` | 删键 |
| `SDK/orchestrator/commit_service.py:854-860、883-893` | 服务意图预留传 `cost_micros`；事件 `BudgetReserved` 载荷 `cost_micros` | 删实参、删键 |
| `SDK/orchestrator/commit_service.py:1602-1626` | 审阅尾部 `TailReserve(critic_tail.tokens, critic_tail.cost_micros)`；尝试预留 `cost_micros=`；`Attempt.budget_reserved=Budget(max_tokens=…, max_cost_micros=…)` | 删金额 |
| `SDK/orchestrator/commit_service.py:1695-1704` | 尝试的 `BudgetReserved` 载荷 `cost_micros` | 删键 |
| `SDK/orchestrator/commit_service.py:2122-2135` | 事件 `BudgetReleased` 载荷 `settled_cost_micros`、`unpriced` | 删两键（不删会 KeyError，因为结清行里这两列没了） |
| `SDK/orchestrator/event_handler.py:647-653、681-686` | 建 `ProviderBudgetGuard` 时传 `price_tables={…profile.price_table.estimator()…}`（单准入与按池准入两处） | 删实参 |
| `SDK/orchestrator/event_handler.py:4616-4633` | `_reservation()` 按价格表折算金额；`_first_critic_reservation()` 按输入上限×单价算首轮审阅金额 | **准入只看 token**：`_reservation` 返回 `Reservation(tokens)`；`_first_critic_reservation` 返回 `Reservation(budget.minimum_tokens)`（见第三节第 1 条） |
| `SDK/orchestrator/event_handler.py:9826-9835` | 冻结在 worker 意图里的 `first_critic_budget.cost_micros` | 删键 |
| `SDK/orchestrator/assurance_review_runtime.py:551、557` | 首轮审阅：比对冻结的 `cost_micros`；写进意图配置 `provider_first_cost_micros` | 删比对项与键 |
| `SDK/orchestrator/assurance_review_transport.py:234` | 审阅请求校验 `integer(reservation.cost_micros)` | 删 |
| `SDK/orchestrator/assurance_review_transport.py:269` | 审阅请求体 `"reservation": {"tokens", "cost_micros"}` | 删 `cost_micros` 键 |
| `SDK/orchestrator/assurance_review_transport.py:401` | 复核预留行 `reserve["reserved_cost_micros"] != reservation.cost_micros` | 删（不删会 KeyError） |
| `SDK/orchestrator/assurance_review_transport.py:594-597` | 历史请求体回填 `"cost_micros": historical.get("reserved_cost_micros")` | 删键 |
| `SDK/orchestrator/assurance_review_transport.py:924-927` | 重建 `Reservation(tokens=…, cost_micros=…)` | 删实参 |
| `SDK/orchestrator/protected_tail_commits.py:90、140` | 审阅尾部 `TailAllocation(…, reservation.cost_micros, …)`；注释"both money and tokens" | 删位置实参（注意后面的 `tool_calls` 前移） |
| `SDK/orchestrator/action_commits.py:995` | 外部动作预留 `cost_micros=int(spec.cost_micros_ceiling or 0)` | 删实参 |
| `SDK/orchestrator/accounting_recovery.py:16、22` | 引入 `FrozenPriceEstimator`、`ProviderPrice` | 删 |
| `SDK/orchestrator/accounting_recovery.py:144-151` | 晚到用量导入：比对准入行的 `price_digest/price_json` 与调用记录价格（含"旧版零价哨兵"判定） | 整段删 |
| `SDK/orchestrator/accounting_recovery.py:164-172` | 有 `cost_upper_micros` 时用价格算实际金额，算不出就判"不完整" | 删；`UsageFact` 只带 token |
| `SDK/orchestrator/accounting_recovery.py:259-263` | 事件 `ReservationCountedAtUpperBound`（"按上限计入"）载荷 `counted_cost_micros` | 删键（不删会 KeyError） |
| `SDK/orchestrator/assurance_consumers.py:701-705` | 同一事件的另一处发出点 `counted_cost_micros` | 删键 |
| `SDK/orchestrator/taskgraph_runtime_imports.py:25、54、75-83` | 无准入旧调用的用量读取：`bridge.unpriced` 判断、`ProviderPrice.known_charge` | 删金额分支，`UsageFact` 只带 token；文档串"Missing usage or pricing"改为只说用量 |
| `SDK/orchestrator/taskgraph_runtime_imports.py:197-204` | 比对已导入用量 `SELECT …cost_micros,unknown` 与 `fact.cost_micros` | **改 SQL** + 删比对项（元组下标 `imported[5]` 变 `imported[4]`） |
| `SDK/orchestrator/taskgraph_execution_policy.py:96-100` | 执行策略里每个池的 `price_table` 三项 | 删整段（`profiles.append(profile.to_json())`） |
| `SDK/orchestrator/taskgraph_plan_sources.py:205-210` | 需求通道剔除 `consumed_cost_micros` 等结清计数 | 从集合里删 `"consumed_cost_micros"`；注释"monetary/token/attempt"改为"token/attempt" |
| `SDK/orchestrator/planner_views.py:123-124` | 规划包里每个义务的 `budget.spent_cost_micros` | 删键（规划器提示词里没引用这个字段，已核对） |
| `SDK/runtime/agent_worker.py:55-72` | `AgentBridge(unpriced=…)` 参数与 `unpriced` 属性 | **改签**：删参数与属性 |
| `SDK/runtime/agent_worker.py:212-219` | 原生记录状态 unknown 时导入 `UsageFact(ref, 0, 0, None, unknown=True)` | 改为 `UsageFact(ref, 0, 0, unknown=True)`（这是"token 拿不到"的唯一来源，保留） |
| `SDK/runtime/agent_worker.py:252-279` | 有准入且计价时，价格算不出就跳过；`amount`、`unknown = (not unpriced) and amount is None` | 删金额分支；已拿到 token 的记录一律 `unknown=False` |
| `SDK/runtime/provider_budget_guard.py:24、35` | 引入 `FrozenPriceEstimator`、`ProviderPrice` | 删 |
| `SDK/runtime/provider_budget_guard.py:232-240` | 适配器 `grow(required, required_cost_micros)` 与 `reserved_cost_micros` 比较 | **改签**：只比 token |
| `SDK/runtime/provider_budget_guard.py:244` | `supports_priced_budgets = True`（原生端口在付费模式下检查它） | 删（编排一律 `local_default`，原生端不会读到） |
| `SDK/runtime/provider_budget_guard.py:255-271、319-321` | 构造参数 `priced`、`price_tables`，属性 `requires_price`、`price_tables` 及校验 | **改签**：全删 |
| `SDK/runtime/provider_budget_guard.py:340-365` | 准入身份 `_identity` 正文里的 `requires_price`、`prices` | 删两键，**身份值变**（见第三节第 2 条） |
| `SDK/runtime/provider_budget_guard.py:367-373、296-310` | 旧版 v2 身份（把槽数算进去的旧字节序）兼容接受集 | 建议本次一起删（见第三节第 2 条） |
| `SDK/runtime/provider_budget_guard.py:380-413` | 准入取调用记录价格、按池比对冻结价格、"零价哨兵"、`price_mismatch` | 整段删 |
| `SDK/runtime/provider_budget_guard.py:478-483、523-530` | 首轮审阅：比对 `provider_first_cost_micros`；按价格算期望金额 | 删两处比对 |
| `SDK/runtime/provider_budget_guard.py:553-575` | 历史调用价格是否变化（`price_mismatch`）、`known_charge` 决定是否计入前序输出 | 删价格比对；`resolved = actual is not None` |
| `SDK/runtime/provider_budget_guard.py:600-612` | 已释放准入行的身份比对里 `price_json/price_digest/cost_upper_micros` | 删三项 |
| `SDK/runtime/provider_budget_guard.py:672-700` | 读 `actual_cost_micros,cost_upper_micros` 判"计价/未计价历史混用"、按金额 grow、拒绝详情 `request_cost_micros` | 整段改为只按 token grow；`request_cost_micros` 不再传（原生字段保留默认 None） |
| `SDK/runtime/provider_budget_guard.py:706-735` | INSERT 准入行带 `price_json,price_digest,cost_upper_micros` | **改 SQL** |
| `SDK/runtime/provider_budget_guard.py:815-877` | 结清：价格比对、"迁移 11 旧行零价哨兵"兼容、`known_charge`、金额超限判 OVERRUN、写 `actual_cost_micros` | 删价格全部逻辑；OVERRUN 只看 token；`_update` 不再传 `actual_cost_micros`（它若按参数名拼列，同 `_apply` 风险） |

### 1.4 部署与执行池身份

| 文件:行 | 是什么 | 怎么处理 |
|---|---|---|
| `SDK/deployment/native_pools.py:359` | 每个原生池的 `RuntimeProfile(…, price_table=config.price_table, …)` | 删实参 |
| `SDK/runtime/assembly.py:542、560` | 组池时 `_policies_for(...)`、`AgentBridge(runtime, unpriced=profile.unpriced, …)` | 改为 `local_default()`、删 `unpriced` 实参 |
| `SDK/runtime/assembly.py:421-450` | `admission_accepts` / `_check_intent_contexts`：启动组池时把**库里全部派发意图**冻结的准入身份与当前准入比对，不等就抛错 | 代码不改；**这是旧库启动即失败的入口**（第六节第 1 条） |
| `SDK/orchestrator/taskgraph_deployment_manifest.json` | 安装清单：两包全部源文件逐字节哈希（含 `governance/provider_prices.py:118` 行一项） | 发版脚本 `scripts/release_sdk_opt.sh:25` 重生成；删文件后不重生成，`InstalledHtnWiringAcceptance` 会报 `taskgraph_deployed_source_unverified`，编排起不来 |
| `HT/orchestration/_pool_identity.py:127` | 生成执行池身份基线时 `OrchestratorConfig(..., price_table=None)` | 删实参；**基线 JSON 本身不因删金额而变**（已核对：不含任何价格键） |
| `HT/orchestration/pool_identity_baseline.json`、`test_pool_identity_bytes.py` | 执行池身份字节基线 | 本步不动；随阶段 C 提示词升版一起重生成一次 |

### 1.5 观测与指标

| 文件:行 | 是什么 | 怎么处理 |
|---|---|---|
| `SDK/observability/metrics.py:8-9` | 文档串"money only when the deployment is priced" | 改写 |
| `SDK/observability/metrics.py:32` | `metrics(store, mission_id, *, unpriced)` | **改签**：删 `unpriced` 参数 |
| `SDK/observability/metrics.py:48` 及其累加 | SELECT `cost_micros FROM imported_usage` 与 `cost_by_role/cost_by_profile` 累加 | **改 SQL** + 删两个累加器 |
| `SDK/observability/metrics.py:127-131` | `cost_micros_by_role`、`cost_micros_by_profile`、`cost_note` | 删三键，`cost` 段只留 `tokens_by_role/tokens_by_profile` |
| `SDK/observability/traces.py:30` | `UNPRICED_NOTE` 常量 | 删 |
| `SDK/observability/traces.py:33-52` | 桶 `{"tokens","cost_micros","priced","rows"}`、`_add(…cost, unpriced)`、`_money()` 输出 `cost_micros/cost_note` | 桶只留 `tokens,rows`；`_add(bucket, tokens)`；`_money` 改名（如 `_usage`）只出 `tokens,rows` |
| `SDK/observability/traces.py:174-196、241-242` | SELECT `cost_micros, unpriced, unknown` 与逐行 `_add(...)`；合并桶时加 `cost_micros`、与 `priced` | **改 SQL** + 删 |
| `SDK/observability/traces.py:255-256、344-348` | `_money(...)` 调用处 | 跟着改名 |
| `SDK/observability/business_replay_inventory.json:563-565、602-605、936-937、1301、1955-1958` | 业务重放覆盖清单里 5 张表的字段名单（`budget_accounts`、`budget_reservations`、`imported_usage`、`obligations`、`provider_token_grants`） | 删 13 个字段名（顺序须与迁移 38 后 `PRAGMA table_info` 完全一致） |

### 1.6 评测与测试辅助脚本

| 文件:行 | 是什么 | 怎么处理 |
|---|---|---|
| `sdk/simple-harness-sdk/scripts/assurance_seams/root-gate-seam.py:73` | 接缝脚本 `OrchestratorConfig(..., price_table=None)` | 删实参 |
| `SDK/testing/product_world.py:155` | 产品世界测试夹具 `price_table=None` | 删实参 |
| `sdk/simple-harness-sdk/scripts/acceptance/run_operation_audit_consumer.py:22、147` | 原生"操作审计"验收脚本的 `FrozenPriceEstimator` | **不动**（原生层，见第三节第 5 条） |
| `SDK/evaluation/*` | 评测模块 | 已核对无金额引用 |

### 1.7 Host

| 文件:行 | 是什么 | 怎么处理 |
|---|---|---|
| `Host/orchestration/service.py:303` | `OrchestratorConfig(..., price_table=None)` | 删实参与注释 |
| `Host/orchestration/provider.py:12-13` | 模块文档串"No price table is injected, so money is recorded as unpriced" | 删这句 |
| `Host/orchestration/provider.py:49` | `ProviderSnapshot.public()` 的 `"price": "unpriced"` | 删键（前端类型同步删） |
| `Host/orchestration/projection.py:466-467` | 任务详情 `usage.amount_micros: None`、`usage.priced: False` | 删两键 |
| `Host/orchestration/diagnostics.py:63-64` | `_money()` 挑 `tokens,rows,cost_micros,cost_note` | 改名并只挑 `tokens,rows` |
| `Host/orchestration/diagnostics.py:73-82、209` | 归因段各桶调用 `_money` | 跟着改名 |
| `Host/orchestration/diagnostics.py:406-408` | 注释"金额一律记为未定价的 null"与 `metrics(store, mission_id, unpriced=True)` | 删注释、删实参 |
| `Host/orchestration/diagnostics.py:411-413` | `costs.usage` 挑 `reserved_cost_micros, settled_cost_micros, unpriced_settlements` | 删三项 |
| `Host/orchestration/settings.py:51` | 注释 "counted at full price"（说的是 token 按全量计） | **不动**（误报） |
| `Host/orchestration/native_plane.py:48` | `pricing_mode = "NO_PROVIDER_CHARGE"`（ARP 嵌入端口协议值） | **不动**（原生冻结协议） |

### 1.8 前端

| 文件:行 | 是什么 | 怎么处理 |
|---|---|---|
| `FE/stores/missionsStore.ts:66` | `model` 类型里的 `price: string` | 删字段 |
| `FE/views/MissionDiagnostics.tsx:94` | "记录用量：… tokens · 金额：未计价/… 微单位" | 删"· 金额：…"整段 |
| `FE/views/MissionsView.tsx:18` | 文件头注释"金额没有价目时显示「未计价」" | 删这句 |
| `FE/views/MissionsView.tsx:1013` | "Token 已结算 … · 当前预留 … · 金额 未计价" | 删"· 金额 未计价" |

### 1.9 测试（只列要改的；"改断言"多数是删掉金额那一项）

SDK 编排测试：

| 文件:行 | 改什么 |
|---|---|
| `T/step02/test_store_and_budgets.py:5、90、108-175` | `reserve(cost_micros=0)` 五处删实参；`UsageFact(..., None)` 两处删第 4 参；`settled_cost_micros is None`、`unpriced == 1`、`unpriced_settlements == 1`、`report["usage"][0]["unpriced"]` 等断言删；测试名 `test_budget_chain_reserve_settle_and_unpriced` 改名 |
| `T/full_target/test_htn_store.py:855`、`T/full_target/test_planning_decision_store.py:753` | 调用上面那个测试函数名，跟着改名 |
| `T/step06/test_backpressure_state.py:169、179` | `reserve(cost_micros=0)` 删实参 |
| `T/full_target/test_obligation_conservation.py:64-333`（14 处） | `record_spend(cost_micros=…)` 改用 `tokens=`/`attempts=`；`consumed_cost_micros == …` 断言改为 token 断言或删 |
| `T/full_target/test_obligation_store.py:112-526`（20 处） | 同上 |
| `T/full_target/test_readiness_reasons.py:310` | 构造视图删 `consumed_cost_micros=0` |
| `T/full_target/assurance_exec/test_review_turn_retry_e2e.py:125` | `UsageFact(..., None if unknown else 0, unknown=…)` 删第 4 参 |
| `T/full_target/operation_completion/test_completion_data_dispatch.py:74`、`test_scoped_content_commit.py:85`、`T/full_target/scripted_plans.py:216`、`T/full_target/test_h4_retry_runtime_entry.py:57` | `Reservation(tokens=…, cost_micros=0)` 删实参 |
| `T/step02/test_contracts.py:70`、`T/step04/test_step04_review_round1.py:32、37` | `Budget(max_cost_micros=…)` 删参与断言 |
| `T/full_target/test_planner_package_refs_and_fields.py:141` | 期望的预算字典删 `"max_cost_micros": None` |
| `T/host_support/test_facade.py:111` | "对外入口拒绝 `budget.max_cost_micros`"这条用例：改成断言它作为未知字段被拒（消息会变成 `budget has unknown fields`），或直接删 |
| `T/step09/test_policy_library.py:58` | 用 `{"hard_cap_micros": 1}` 做"不可改字段"样例，换成另一个仍存在的部署字段 |
| `T/p33/test_g_critic_dispatch_recovery.py:17`、`T/p35/provider_world.py:57`、`T/product_assembly.py:55` | `price_table=None` 删实参 |
| `T/p35/test_admission_slot_change.py:38` | 写死的 v2 旧身份字符串；若按建议删 v2 兼容，整条用例改为"只接受当前身份"；不删也必须换新哈希 |
| 新增 `T/full_target/test_schema_drop_money_columns.py` | 照 `test_schema_drop_criterion_assessments.py` 的样子：37 版库升到 38 后 13 列不在、触发器仍在且不含金额列、旧数据行保留 |

Host 测试：

| 文件:行 | 改什么 |
|---|---|
| `HT/orchestration/test_mission_diagnostics.py:92` | `cost_micros_by_role is None` 删 |
| `HT/orchestration/test_mission_diagnostics.py:157-161` | 期望的归因桶删 `cost_micros`、`cost_note` |
| `HT/orchestration/test_projection.py:58、81` | `usage["amount_micros"] is None` 删（可改为断言该键不存在） |
| `HT/orchestration/_pool_identity.py:127` | 删 `price_table=None` |

前端测试：

| 文件:行 | 改什么 |
|---|---|
| `FE/views/MissionDiagnostics.test.tsx:32、39、46` | 测试名里 "unpriced" 删；`金额：未计价` 断言删 |
| `FE/views/MissionsView.test.tsx:9、113、469、474、485` | 头注释、`usage.amount_micros: null` 夹具、"金额显示未计价"用例名与断言删 |

### 1.10 明确不动的（附理由）

| 位置 | 理由 |
|---|---|
| `SDK/storage/schema.py:192-222`（迁移 1 的建表文字）、`:500-529`（迁移 11）、`SDK/governance/budget_tail_schema.py`（迁移 13）、`SDK/storage/htn_schema.py:245`（迁移 16）、`SDK/storage/assurance_barrier_v26.sql:1416`（迁移 26） | 已发布迁移的文字参与校验和（`schema.py:41-42`、`store.py:238/292`），改一字所有已有库都打不开；一律由迁移 38 删列 |
| `SDK/planning/htn/method_lifecycle.py:78-137` | `max_cost_multiplier/median_cost` 是做法评估里的相对代价数，与金额无关 |
| `Host/sdk_adapters/context_partitions.py`、`Host/execution/current_tool_pages.py:1088` | 英文动词 "price"（估 token），误报 |
| `gold_price_lookup` 相关（工具清单、策略、测试夹具约 20 处） | 金价查询工具名，误报 |
| `.local-test-evidence/**`、`backend/vendor/**`、`backend/main.py.bak*` | 历史证据快照、发版产物、备份文件；不改（vendor 轮子由发版脚本替换） |
| `SH/**`（原生运行层全部金额概念） | 见第三节第 5 条 |
| Host 主对话计费：`backend/billing/ledger.py`、`backend/llm/pricing.py`、`backend/config.py:418-431`、`config.toml:44-56`、`backend/main.py:433-434、7715-7741、7834-7872`、`Host/sdk_adapters/provider.py:1036-1176、1529-1533`、`Host/sdk_adapters/desktop_runtime.py:288-294`、`Host/sdk_adapters/conformance.py`、`Host/operation_audit/consumer.py:188-189` | 主对话（非编排）的人民币计费与花费上限，属另一子系统，7-1 裁决未覆盖 |
| 深度研究工作流：`Host/workflows/effects.py:577-2860`、`Host/workflows/definitions/deep_research_v5_contracts.py:307-332`、`Host/workflows/adapters/research_runtime.py`、`deep_research_v6_bootstrap.py:183-184`、`backend/main.py:4076-4077` 及其 7 个测试 | 深度研究自己的预算账，属另一子系统，7-1 裁决未覆盖 |

---

## 二、文档（非代码，顺手同步）

- `ARCHITECTURE/AGENT_ORCHESTRATION.md`、`sdk/simple-harness-sdk/docs/api/runtime.md`：提到 `PriceTable`/`price_table`/未计价，改成"只记 token"。

---

## 三、不能简单删、要定口径的点

1. **准入只看 token（按价格估算的分支删掉后）**
   - 普通预留：`_reservation(tokens)` → `Reservation(tokens)`。
   - 首轮审阅：现在冻结"输入上限、输出上限、最低 token、金额"四项，供应方准入再按价格复算金额比对。删后只冻结前三项，准入只比这三项。
   - 供应方准入的 OVERRUN（超支）只看 `total_upper`（总 token）与 `output_ceiling`（输出上限）。
   - "用量未知"只剩一个来源：原生记录状态为 unknown，或已终止但拿不到 token 数。原来"计价部署里价格算不出也记未知"的来源整条删掉。"按上限计入"事件只计 token。
   - 建议口径：**按上述执行，不新增任何 token 以外的维度**。

2. **准入身份变了，旧库的派发意图全部对不上（最关键）**
   - 删掉 `_identity` 里的 `requires_price`、`prices` 后，`provider_admission_fingerprint` 的值一定变。
   - `SDK/runtime/assembly.py:430-450` 启动组池时会扫**库里全部派发意图**（不分状态，已结束的也扫），一个不等就抛 `provider admission identity differs from a persisted intent`，编排服务起不来。
   - 另外 `Budget.from_json` 遇到旧 JSON 里的 `"max_cost_micros": null` 直接拒（`models.py:178-181`），读任何旧任务都会报错。
   - 所以"不兼容旧库"实际意思是：**旧 orchestrator.db 整库不能用，而且是启动就失败**。
   - 需要定（建议）：
     - (a) 身份版本号从 v3 升到 v4，一眼能看出是新身份；
     - (b) 同时删掉 v2 旧字节序兼容（`legacy_fingerprint`、`LEGACY_SLOT_RANGE`、`accepted_fingerprints` 只留当前一个）。它本来就是为旧构建留的兼容，删金额后旧 v2 值也算不对了，留着只是死代码；
     - (c) 数据处理只留一条路：发版说明写明"编排数据目录需新建"。迁移 38 执行前 Store 会自动做 `orchestrator.db.pre-schema-38.backup`（`store.py:300-309`），旧数据有备份。
   - **要问用户的一件事**：用户机器上现有的编排任务记录（不是聊天会话）是否可以随这次一起作废？如果不行，就只能把"金额字段原字节保留"，与裁决冲突，要回到裁决层。

3. **事件载荷字段删了，影不影响编解码清单哈希**
   - 已核对 `sdk/simple-harness-sdk/scripts/build/taskgraph_manifest.py`：它生成的 `taskgraph_deployment_manifest.json` 不是"类型/编解码清单"，而是**两个包（`agent_orchestrator/`、`simple_harness/`）在轮子里的全部源文件逐字节哈希**（排除 `__pycache__`、`.pyc/.pyo` 和清单自身）。
   - 所以任何 SDK 源码改动都要重生成，与删不删事件字段无关；删 `provider_prices.py` 会让清单少一项。发版脚本已包含这一步（`scripts/release_sdk_opt.sh:25`）。
   - 事件载荷（`BudgetReserved/BudgetReleased/ReservationHeld/ReservationCountedAtUpperBound`）没有任何载荷模式或哈希登记；业务重放清单只登记表字段和事件类型名，不登记载荷键。删键不影响任何冻结哈希。
   - 受影响的冻结内容只有上面第 2 条的准入身份和执行策略摘要。

4. **预算合同 `Budget.max_cost_micros` 在不在冻结合同或界面里**
   - 它不在 ARP 冻结合同里（ARP 的 `runtime-plane.schema.json` 里的 `cost_micros` 是嵌入回执，属原生层，另一回事）。
   - 它在三类地方：
     - 任务、步骤、尝试 JSON（库里存的）；
     - 规划包（`planner_views` 输出的 `budget`）；
     - 执行策略摘要（`task.budget.to_json()`）。
   - 对外入口本来就把它列为"不开放字段"（`facade.py:70`）。
   - 界面只显示 `max_tokens`、`max_attempts`（`MissionsView.tsx:1016`）。
   - 结论：可以直接删；代价已计入第 2 条（旧库整库作废）。

5. **原生运行层 `simple_harness` 的 cost/price 属不属于这次范围——建议本次不动**
   - 它是另一套东西：原生运行层的价格估算器、硬上限、每次调用的价格快照、预算花费。
     - 代码位置：`SH/execution/budget.py` 的 `FrozenPriceEstimator`、`BudgetPolicy.hard_cap_micros`、`BudgetCharge.amount_micros`；`SH/execution/provider_invocations.py:544-576` 的 `estimator_snapshot/estimator_digest`；`SH/agents/config.py:66-105` 的 `lifetime_cost_limit_micros`；`SH/runtime/consumer_adapter.py:74-92` 的 `pricing_mode`；`SH/agents/arp/embedding_call.py`、`codec.py:439-440`、`contracts/runtime-plane.schema.json:10296-10534` 里嵌入回执的 `cost_micros/pricing_mode`。
   - Host 别的功能在用它：
     - 主对话用真实单价冻结估算器和硬上限（`backend/main.py:7715-7741、7834-7872`，单价来自 `[billing.pricing]`）；
     - `Host/sdk_adapters/desktop_runtime.py`、`conformance.py`；
     - 操作审计 `Host/operation_audit/consumer.py`。
   - 价格快照写进每个原生执行库的调用记录，参与原生池身份与调用指纹（`SH/execution/dispatch.py:157-191、419-432`）。ARP 合同是冻结协议。动它会波及主对话和全部原生执行池，远超 7-1 的"编排金额"范围。
   - 删完编排这一侧后，编排池一律走 `ConsumerRuntimePolicies.local_default()`（`pricing_mode="unpriced_local"`）。原生层每次调用会冻结默认哨兵 `FrozenPriceEstimator("consumer-v1","consumer",0,0)`（`SH/agents/runtime.py:203-204`），编排侧**不再读它**。这不算"同一件事两条路"：编排只记 token，原生层的金额是主对话那条线的事。
   - 若用户要连主对话计费一起删，另开一条，范围是 Host 计费 + 原生运行层 + ARP 合同升版。

6. **义务账（obligations）的金额轴**
   - `record_spend` 在源码里只有"从库行重建内存账"一个调用方（`obligation_store.py:524`），生产路径从不写金额；`spent_cost_micros` 恒为 0。
   - 照删，不影响行为。

7. **外部动作的金额上限 `OperationSpec.cost_micros_ceiling`**
   - 目前只有 AppWorld 评测连接器声明操作，且都没填这个字段。
   - 删后动作预留只占 1 次工具调用、0 token。行为不变。

---

## 四、迁移

- **现有最新迁移：37**（`SDK/storage/schema.py:713`，`orchestrator-artifacts-barrier-without-offline-relocation`）。
- **新增迁移 38**，建议名 `orchestrator-drop-money-dimension`，`DDL_V38` 放在 `schema.py` 里（与 31-37 的做法一致），并加进 `MIGRATIONS`。
- 本机 Python 的 SQLite 是 3.51，`ALTER TABLE … DROP COLUMN` 需 ≥3.35。Host 虚拟环境的 SQLite 版本动手前再确认一次。
- Store 在一个事务里逐句执行迁移，DROP COLUMN 可在事务内执行。

要删的列：

| 表 | 列 | 来源迁移 | 约束/引用 |
|---|---|---|---|
| `budget_accounts` | `reserved_cost_micros`、`settled_cost_micros`、`unpriced_settlements` | 1 | 无索引、无触发器、无外键 |
| `budget_reservations` | `reserved_cost_micros`、`settled_cost_micros`、`unpriced` | 1 | 同上 |
| `imported_usage` | `cost_micros`、`unpriced` | 1 | 同上（`unknown` 保留） |
| `provider_token_grants` | `price_json`、`price_digest`、`cost_upper_micros`、`actual_cost_micros` | 13（`budget_tail_schema.py`） | 后两列只有自身列级 CHECK，SQLite 允许连同自身 CHECK 一起删；两个索引不含这些列 |
| `obligations` | `spent_cost_micros` | 16（`htn_schema.py:245`） | 自身列级 CHECK（可删）；**被触发器 `assurance_source_obligations_update` 的 WHEN 子句引用**（`assurance_barrier_v26.sql:1416`） |

已用脚本扫过 `storage/*.py`、`storage/*.sql`、`governance/*schema*.py` 里全部 `CREATE TRIGGER/VIEW/INDEX`：引用这 13 列的**只有上面那一个触发器**。没有视图。没有索引。

迁移 38 文字草案（实施者以实际触发器原文为准）：

```sql
ALTER TABLE budget_accounts DROP COLUMN reserved_cost_micros;
ALTER TABLE budget_accounts DROP COLUMN settled_cost_micros;
ALTER TABLE budget_accounts DROP COLUMN unpriced_settlements;
ALTER TABLE budget_reservations DROP COLUMN reserved_cost_micros;
ALTER TABLE budget_reservations DROP COLUMN settled_cost_micros;
ALTER TABLE budget_reservations DROP COLUMN unpriced;
ALTER TABLE imported_usage DROP COLUMN cost_micros;
ALTER TABLE imported_usage DROP COLUMN unpriced;
ALTER TABLE provider_token_grants DROP COLUMN price_json;
ALTER TABLE provider_token_grants DROP COLUMN price_digest;
ALTER TABLE provider_token_grants DROP COLUMN cost_upper_micros;
ALTER TABLE provider_token_grants DROP COLUMN actual_cost_micros;
DROP TRIGGER assurance_source_obligations_update;
ALTER TABLE obligations DROP COLUMN spent_cost_micros;
-- 照 assurance_barrier_v26.sql:1416 起的原文重建，只删 WHEN 里的
--   "OR NEW.spent_cost_micros IS NOT OLD.spent_cost_micros" 一个子句，其余逐字照抄
CREATE TRIGGER assurance_source_obligations_update AFTER UPDATE ON obligations WHEN (...) BEGIN ... END;
```

注意：
- **触发器必须先删后删列**。SQLite 删列后会重新解析整个库结构，残留引用会让整条迁移失败、库停在 37。
- 迁移 26 的屏障文字（`.sql` 文件）不改。这一点和迁移 37 重建 `assurance_source_artifacts_update` 的做法一样。
- 新库也会顺序跑 1→38：先建这些列，再删掉。结果与旧库升级一致，不需要单独的"新库建表"分支。
- `business_replay_inventory.json` 的字段名单要和 38 之后 `PRAGMA table_info` 的顺序**完全一致**（`business_replay.py:71-89` 逐字段比较），`test_business_replay_inventory.py` 会查。

---

## 五、建议的实施顺序

原则：每一步结束时 SDK 能导入、对应测试能跑；先改签名，再改 SQL，最后加迁移。这样不会出现"代码还在读的列已经被删"的中间态。

| 步 | 做什么 | 改哪些文件 | 量级 | 只跑这些测试 |
|---|---|---|---|---|
| 1 | **合同与配置面**：删 `Budget.max_cost_micros`、`PriceTable`/`price_table`/`hard_cap_micros`/`unpriced`、`RuntimeProfile.price_table/priced`、`OrchestratorConfig.policies()`、`_policies_for` 改 `local_default`、`OperationSpec.cost_micros_ceiling`、策略字段名单两处；所有 `price_table=None` 实参 | `SDK/contracts/models.py`、`SDK/governance/budget_limits.py`、`SDK/api/facade.py`、`SDK/runtime/assembly.py`、`SDK/runtime/model_router.py`、`SDK/runtime/connectors.py`、`SDK/governance/policies.py`、`SDK/governance/promotion.py`、`SDK/deployment/native_pools.py`、`SDK/orchestrator/taskgraph_execution_policy.py`、`SDK/testing/product_world.py`、`scripts/assurance_seams/root-gate-seam.py`、`Host/orchestration/service.py`、`HT/orchestration/_pool_identity.py` | 14 文件 / 约 60 行 | `T/step02/test_contracts.py`、`T/step04/test_step04_review_round1.py`、`T/host_support/test_facade.py`、`T/step09/test_policy_library.py`、`T/full_target/test_planner_package_refs_and_fields.py`、`T/p33/test_g_critic_dispatch_recovery.py`、`HT/orchestration/test_pool_identity_bytes.py`（应仍全绿，证明基线不受影响） |
| 2 | **供应方准入去价格**：删 `provider_prices.py`；守卫去 `priced/price_tables/requires_price/supports_priced_budgets`、价格比对、哨兵兼容、首轮金额比对、金额 grow 与 OVERRUN；身份升 v4 并删 v2 兼容（若第三节第 2 条定了）；`event_handler` 两处 `price_tables=` 实参 | `SDK/governance/provider_prices.py`（删）、`SDK/runtime/provider_budget_guard.py`、`SDK/orchestrator/event_handler.py`（647-686） | 3 文件 / 约 150 行 | `T/p35/test_admission_slot_change.py`、`T/p35/test_first_request_budget.py`、`T/p35/test_provider_accounting_loop.py`、`T/p35/test_multi_profile_load.py`、`HT/orchestration/test_concurrency_raise_host.py` |
| 3 | **记账去金额（代码与 SQL 一起改）**：`UsageFact`、`Reservation`、`TailReserve/TailAllocation`、`AccountSnapshot`、`reserve/grow/import_usage/usage_for/settle*/costs_report/usage_flags`、`store.budget_usage`、`AgentBridge.unpriced`、晚到导入、旧调用导入、审阅请求与复核、三个事件载荷、"按上限计入"两处、首轮审阅冻结金额 | `SDK/governance/budgets.py`、`SDK/governance/tail_budget.py`、`SDK/storage/store.py`、`SDK/orchestrator/commit_service.py`、`SDK/orchestrator/event_handler.py`（4616-4633、9834）、`SDK/orchestrator/assurance_review_runtime.py`、`SDK/orchestrator/assurance_review_transport.py`、`SDK/orchestrator/protected_tail_commits.py`、`SDK/orchestrator/action_commits.py`、`SDK/orchestrator/accounting_recovery.py`、`SDK/orchestrator/assurance_consumers.py`、`SDK/orchestrator/taskgraph_runtime_imports.py`、`SDK/runtime/agent_worker.py`、`SDK/runtime/assembly.py`（560） | 14 文件 / 约 130 行 | `T/step02/test_store_and_budgets.py`、`T/step06/test_backpressure_state.py`、`T/full_target/test_htn_store.py`、`T/full_target/test_planning_decision_store.py`、`T/full_target/assurance_exec/test_review_turn_retry_e2e.py`、`T/full_target/assurance_exec/test_settlement_held_not_fatal.py`、`T/full_target/assurance_exec/test_assurance_critic_owns_first_tail.py`、`T/full_target/taskgraph_exec/test_late_accounting_quiet.py`、`T/full_target/test_terminal_unknown_release.py`、`T/full_target/test_service_intent_provider_blocker.py`、`T/full_target/operation_completion/test_completion_data_dispatch.py`、`T/full_target/operation_completion/test_scoped_content_commit.py`、`T/full_target/test_h4_retry_runtime_entry.py`、`T/p35/test_provider_accounting_loop.py`、`T/p35/test_provider_accounting_restart.py`、`T/p35/test_first_request_budget.py`、`T/test_critic_test_evidence_order.py` |
| 4 | **义务账去金额** | `SDK/contracts/obligations.py`、`SDK/storage/obligation_store.py`、`SDK/orchestrator/planner_views.py`、`SDK/orchestrator/taskgraph_plan_sources.py`、`SDK/storage/assurance_source_inventory.py` | 5 文件 / 约 30 行 | `T/full_target/test_obligation_conservation.py`、`T/full_target/test_obligation_store.py`、`T/full_target/test_readiness_reasons.py`、`T/full_target/test_htn_recursion_fuel.py`、`T/full_target/test_planner_package_refs_and_fields.py` |
| 5 | **观测去金额** | `SDK/observability/metrics.py`、`SDK/observability/traces.py`、`Host/orchestration/diagnostics.py`、`Host/orchestration/projection.py`、`Host/orchestration/provider.py` | 5 文件 / 约 50 行 | `HT/orchestration/test_mission_diagnostics.py`、`HT/orchestration/test_projection.py`、`T/full_target/test_versioning_v2_manifest.py` |
| 6 | **迁移 38 + 清单**：`DDL_V38`、业务重放清单 13 个字段、新增删列测试 | `SDK/storage/schema.py`、`SDK/observability/business_replay_inventory.json`、新 `T/full_target/test_schema_drop_money_columns.py` | 3 文件 / 约 60 行 | 新测试、`T/full_target/test_business_replay_inventory.py`、`T/full_target/test_schema_drop_criterion_assessments.py`（确认 37→38 不破坏既有删表用例） |
| 7 | **前端** | `FE/stores/missionsStore.ts`、`FE/views/MissionDiagnostics.tsx`、`FE/views/MissionsView.tsx` 及两个测试 | 5 文件 / 约 15 行 | `MissionDiagnostics.test.tsx`、`MissionsView.test.tsx` |
| 8 | **收尾**：全库再 `git grep -E "price_table\|PriceTable\|cost_micros\|unpriced\|ProviderPrice\|price_json\|price_digest\|cost_upper\|hard_cap_micros\|未计价"`，`SDK/`、`Host/orchestration`、`FE/` 下应只剩第 1.10 节列出的"不动"项；同步两份文档；安装清单留给发版脚本重生成；执行池身份基线与阶段 C 提示词升版一起重生成 | — | — | — |

第 3 步最大，可按"账本+提交服务"和"恢复+审阅+工人桥"拆成两次提交。但两次之间 SDK 必须能导入：先改 `Reservation/UsageFact` 的定义和全部构造处，再改 SQL。

---

## 六、风险

1. **会让服务起不来的地方**
   - **旧库启动即失败**：准入身份变（`assembly.py:430-450` 扫全部派发意图），加上 `Budget.from_json` 拒旧字段。发版前的"开发数据副本启动检查"按现流程一定失败，要么按第三节第 2 条换新数据目录，要么改流程。**这一条必须先和用户定。**
   - **安装清单没重生成**：删了 `provider_prices.py` 却没重生成，`InstalledHtnWiringAcceptance` 报 `taskgraph_deployed_source_unverified`，编排部署装不上。本地联调时同样要先重生成，或用测试替身。
   - **迁移 38 失败**：触发器没先删、或照抄触发器时多删少删一个字符，迁移整体回滚、库停在 37。代码已不认识 37 的列，启动失败。
   - **漏改 SQL**：`store.budget_usage()`（`store.py:1759`）漏改，Host 任务详情和诊断导出一调就报 "no such column"。`budgets._apply(**deltas)`、守卫 `_update(**…)` 按参数名拼列名，漏删一个 `*_cost_micros=` 实参只在运行到那一行才报错，静态检查查不出。
2. **最容易漏的地方**
   - **位置参数**：`UsageFact(ref, in, out, cost)`、`Reservation(tokens, cost)`、`TailReserve(tokens, cost)`、`TailAllocation(..., tokens, cost, tool_calls)`。删字段后旧的位置写法不一定报错，而是把下一个值错位塞进去。例如 `TailAllocation(..., reservation.cost_micros, reservation.tool_calls)` 删一个后，`tool_calls` 会落到 `cost_micros` 原来的位置。要逐处改成关键字参数。
   - **行字典取键**：`settled["settled_cost_micros"]`（`commit_service.py:2129`、`accounting_recovery.py:263`、`assurance_consumers.py:704`）、`reserve["reserved_cost_micros"]`（`assurance_review_transport.py:401`、`provider_budget_guard.py:234`）、`row["price_digest"]`（`accounting_recovery.py:145`、`provider_budget_guard.py:819`）。列删了就是 KeyError/IndexError，而且都在恢复、审阅这类不常走的路径上。
   - **元组下标**：`taskgraph_runtime_imports.py:203-206` 比对 `imported` 元组并取 `imported[5]`，删一列后要改成 `[4]`，否则判断失真。
   - **业务重放清单的字段顺序**：必须和删列后的实际顺序一致，不只是集合相同。
   - **测试里的间接调用**：`test_htn_store.py:855`、`test_planning_decision_store.py:753` 按函数名调用 `test_budget_chain_reserve_settle_and_unpriced`，改名要同步。
3. **语义风险**
   - "用量未知"的来源少了一个，在计价部署才会发生；桌面部署从未计价，行为不变。
   - "按上限计入"只计 token，与用户 09-26 的"未知用量按上限结清"决定一致。
4. **范围误伤**
   - 不要顺手动 `SH/`、Host 主对话计费、深度研究预算（第 1.10 节）。这些地方的 `cost_micros` 与编排无关，一动就会波及主对话和原生执行池身份。

---

## 七、总量

编排一侧约 **70 个文件、约 440 行**要改：
- SDK 源码 36 个（删 1 个整文件，加 1 个迁移）；
- Host 4 个，前端 3 个；
- 测试约 27 个（SDK 22、Host 3、前端 2），另新增 1 个删列测试。

原生运行层与 Host 主对话计费、深度研究预算明确不在本次范围内。
