# 阶段 4 SDK 发版独立核验

日期：2026-10-15
核验员：独立子代理（只读，模型 Opus 5.5）
口径：只报阻断级问题（会改变运行行为、会让产品或测试坏掉、不该发版）。

## 核验范围

- 工作树 `simple_harness-cq`，分支 `cq-phase4`，HEAD ec620d38e。
- 比较：上一版 opt.187 的 SDK 源码 b5556afaf 到 HEAD，范围 `sdk/simple-harness-sdk`（242 个文件，+2129 / −2502）。
- 4.1：两份公开模块清单（`simple_harness/public_api.py`、`agent_orchestrator/public_api.py`），5 个模块新加 `__all__`，3 个模块 `__all__` 补名字，SDK 测试 `tests/test_public_api.py`。
- 阶段 3：抽查两处下沉、uow 的函数内导入上提、审计分页改写、commit_service 的上提。
- 依据：`阶段4-切片设计.md`（含裁决修订）、`阶段3-实施记录.md`（独立核验一节）。

## 逐项结论

### 1. 新加的 `__all__` 只列模块自己的公开名字，不改运行行为 —— 通过

- 用 AST 逐个查了 8 个改动模块的 `__all__`：policies、event_handler、planning_selection、taskgraph_deployment、planning_human_store、execution/audit、providers/errors、tools/runtime_catalog。
- 新加的名字都是模块顶层的 def / class / 赋值。没有重复，没有不存在的名字。
- 只有 event_handler 的 `InjectedCrash` 是导入来的。它在 opt.187 的 `__all__` 里就有，不是这次加的。
- 全仓库（SDK、backend、scripts）只有 2 处星号导入，都是后端内部模块之间。没有星号导入这 8 个模块。
- SDK 源码里没有代码在运行时读这些模块的 `__all__`，也没有代码枚举包内模块（walk_packages / iter_modules / rglob *.py 都没有）。读 `__all__` 的只有测试与 Host 边界检查。
- 结论：`__all__` 改动只影响星号导入。运行行为不变。

### 2. 公开清单里没有内部模块 —— 通过

- 两份清单共 83 个模块。没有 `testing`，没有下划线模块，没有 sqlite 子模块。
- `simple_harness.execution.sqlite` 包根在清单里。这与裁决第 2 条一致（包根公开，子模块不公开）。
- 两份清单各列本包。`simple_harness` 的清单不引用 `agent_orchestrator`，依赖方向没有倒过来。
- SDK 测试 `test_internal_modules_are_never_public` 卡住这三类内部模块。

### 3. 执行图部署清单与源码字节一致 —— 通过

- 跑 `tests/test_public_api.py` 与 `tests/agents/arp/test_arp_assurance_acceptance.py`：90 条通过，29 秒。
- 另用 SDK 自己的读取方 `InstalledHtnWiringAcceptance()._read()` 对 src 目录直接核对：文件清单相同、每个文件哈希相同、`deployment_id` 正确，读取成功。
- 当前状态是 `NOT_RUN`，没有验收证据。这是预期状态：按 4.6，发版时跑执行图验收门再写回 `VALIDATED`。
- 补跑 Host 侧 `backend/tests/test_sdk_public_boundary.py`（工作树里未跟踪，指向 SDK src）：1 条通过。后端产品代码对 SDK 的导入都在公开清单与 `__all__` 里。

### 4. 阶段 3 抽查 —— 通过

- `internal_effect_identity` 下沉到 `execution/effects.py`：函数体逐字相同（协议字符串、字段、`call-` / `effect-` 前缀都没变）。所需的 hashlib、canonical_json、CallId、EffectId、RunId 在 effects 顶部都已导入。旧名字 `_internal_effect_identity` 在代码里已无引用。react_loop、sqlite/audit、sqlite/audit_coverage 都改从 effects 导入。后端只有 3 处注释还写旧路径，不影响运行。
- 预算账户下沉到 `orchestrator/budget_accounts.py`：Reservation、mission_account、GLOBAL_ACCOUNT_PREFIX、global_account_id、is_global_account、task_account 逐字相同。commit_service 从 budget_accounts 重新导入这些名字，并且仍列在它的 `__all__` 里。Host 的 `deskpet/orchestration/service.py` 与一个测试照旧从 commit_service 导入，能用。
- `sqlite/uow.py` 上提 audit、audit_pages、decision_terminal 的函数：只是把导入从函数里挪到文件顶部，调用不变。
- 审计分页改写（`sqlite/audit_pages.py`）：原写法先导入 command_audit 的三个函数，stage 查询再用 stage_audit 的同名别名覆盖。新写法用 if / else 选模块属性。两种查询选到的函数相同，等价。
- 新进程逐个导入 SDK 全部 642 个模块（8 路并行，30 秒）：641 个成功。唯一失败的 `simple_harness.runtime.recall_protocol_v4` 单独导入会撞到循环导入；在 b5556afaf 上同样失败，是旧问题，不是本次引入。经包入口正常导入时不受影响。

## 不阻断的提醒

1. 4.6 写着"Host 两处预算账户名字改从 budget_accounts 导入"。但 `agent_orchestrator.orchestrator.budget_accounts` 不在公开清单里，也没有 `__all__`。Host 要是照做，Host 边界检查（`test_sdk_public_boundary.py`、`sdk_boundary.py`）会报错，还得再发一版 SDK。二选一，发版前定下来：
   - Host 保持从 commit_service 导入（现在就能用，commit_service 的 `__all__` 列着这些名字）；或者
   - 发版前把 budget_accounts 加进 `agent_orchestrator/public_api.py`，并给它加 `__all__`，然后重生成部署清单。
2. `src/simple_harness/version.py` 仍是 `opt.187`。发版改版本号会改字节。顺序必须是：改版本号 → 重生成部署清单 → 跑执行图验收门写回 `VALIDATED`。
3. `backend/tests/test_sdk_public_boundary.py` 在工作树里还是未跟踪文件，随 Host 提交时别漏。

## 必须处理

无

## 结论

PASS
