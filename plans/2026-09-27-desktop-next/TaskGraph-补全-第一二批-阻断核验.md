# TaskGraph 补全第一、二批 — 阻断核验

范围：`git diff e2d869a3..HEAD -- sdk backend tauri-app`（提交 c3a5ad8d、f62487d3）。只看阻断级。没改代码，没跑用例（读代码已能下结论）；只做了一次只读检查：部署清单里登记的源码字节与当前源码一致（`InstalledHtnWiringAcceptance()._read()` 通过）。

## 结论：无阻断

## 逐项核对

### 1. 一轮故障只看类型码

- **原来按文字或 isinstance 判"损坏"的，现在都带上了登记过的码。** 全 SDK 里抛这些异常的地方逐个查过：
  - 历史完整性：只有 `taskgraph_store._fail`，抛 `GraphIntegrityError`，带码。
  - 尝试输入完整性：只有 `taskgraph_attempt_inputs._fail`，抛 `AttemptInputIntegrityError`，带码。
  - 来源完整性：只有 `taskgraph_history_sources._invalid`，抛新的 `SourceIntegrityError`，带码。
  - 存的结果解不开：只有 `store._stored_result`，抛新的 `StoredResultCorrupt`，带码。
  - 审阅回合身份不符：`_bind_startup_tools` 两处，都改成了 `ServiceTurnIdentityMismatch`。
  - 执行投影排不出先后：`GraphIntegrityError`，带码。
  - 计划意思读不全：`PlanIntegrityError` 只有两处构造（`semantic_binding_missing`、`root_not_identified`），两个码都登记了，构造时不会报错。
- **会不会有原来当轮停、现在变成原地重试的？** 旧规则还认一种情况：异常被别的类型包了一层，但文字里还留着损坏码。查了所有"接住后把原异常文字带进新异常"的地方。能接住完整性错误、又会冲到一轮边界的，一处也没有：
  - `create_attempt` 里把错误包成 `CommitRejected` 的地方，调用方自己接住了，只记一句话。
  - `commit_service._judgment_network` 包的是执行投影错误，它的文字里本来就没有旧的损坏码，改之前也是原地重试。
  - `taskgraph_dispatch` 的几处包装没带原文字，改之前也是原地重试。
- **会不会有原来原地重试、现在被误停的？** 新算作损坏的只有两种：没绑定（`NotBoundError`）、内核版本不认（`KernelUnsupportedError`）。
  - 所有 `require_bound` 调用传的都是本任务自己的 id，没有拿 A 任务的一轮去读 B 任务的情况，不会连坐别的任务。
  - 用户任务在创建的同一个事务里绑定（`assembly._complete`），正常任务碰不到这两个错误。
  - 主循环每轮最先跑关口（`contract_check`），没绑定的老任务先被关口按名停掉。
  - 这两个类仍是 `StoreError` 的子类。各处 `except (... StoreError)` 接住后的行为与改之前一样，因为原来抛的也是 `StoreError`。
- 分类函数、事件里的 `code` 字段、`record_integrity_failure` 取 `str(error.code)`：StrEnum 转出来的字面不变，写 JSON 没问题。多重继承的 MRO 合法，模块能正常导入。

### 2. `require_bound` 与 7 处调用

- 7 处逐个对过（读页面、保证通道审批扫描、补记账扫描、结清动作、启动跳过、停任务时的预留处理、关口）：没绑定时的处理与原来 `taskgraph_enabled` 返回 False 时一致。
- 内核版本不认时，原来 `taskgraph_enabled` 抛 `StoreError`，现在抛 `KernelUnsupportedError`（也是 `StoreError`）。只接 `NotBoundError` 的调用方照样让它冲出去，与改之前一样。`duties.py` 用 `except Exception` 兜住，结果也一样。
- `taskgraph_action_settlement` 改成逐任务包进一轮边界：
  - 每个动作仍然各开一个事务，边界出错回滚只回滚当前那一个。
  - 原来某个任务出错会从整个补记账函数冲出去，现在只算这个任务自己的一轮故障，比原来更稳。
  - 启动时（`__aenter__` 里的补记账）用的也是同一种边界（补记账循环原来就这样用），存下的故障在第一轮处理，与现状一致。

### 3. `_plan_integrity_stop` 先让"已创建"的任务进入规划

- `begin_planning` 自带事务，从"已创建"进"规划中"是合法转换，与关口的做法一样。之后 `_commit_fail_planning` 另开事务。
- 如果第二步没写成功，任务停在"规划中"，下一轮同一个错误还是判损坏，会走"规划中"那条分支再停一次。不会卡住，也不会每轮重复写库：`MissionPlanning` 只写一次，一轮故障事件只在第 1 次写。

### 4. 删掉的接口有没有残留读方

`suggest_for`、`suggested_method_refs`、`MethodSuggestion`、`SuggestionReason`、`trial_uses`、`note_trial_use`、`read_active_consumers`、`taskgraph_enabled`：在 SDK 源码、SDK 测试、Host backend、tauri-app 里都搜不到读方。只剩 `seams_current.json` 里一段说明文字，不是代码。提示词里也没有引用这些字段。

## 顺带记录（不是本批引入，不算阻断）

内核版本不认的老任务（开发库里才有）其实停不下来。关口抛出 `KernelUnsupportedError` 后，按损坏去停；但停的过程会走 `_prepare_terminal_ledger`，它再次调用 `require_bound`，又抛同一个错，于是这次停止没写成，每轮重试一次（只记一句话，不写库，主循环不崩）。改之前也一样停不下来：老版本是到上限后按库读写故障停，走的是同一个函数。现在只有 `taskgraph-exec-v2` 一个内核版本，产品库里没有这种任务。以后升内核版本时再处理：让 `_prepare_terminal_ledger` 把"内核不认"当作"没绑定"，保留预留不结清。
