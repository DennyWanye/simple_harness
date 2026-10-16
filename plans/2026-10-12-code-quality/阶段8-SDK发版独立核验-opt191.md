# 阶段 8 SDK 发版独立核验（opt.191）

日期：2026-10-17
核验员：独立子代理（只读，模型 Opus 5.5）
口径：只报阻断级问题（会改变运行行为却没说明、会让产品或测试坏掉、不该发版）。

## 核验范围

- 工作树 `simple_harness-cq`，分支 `cq-phase8`，HEAD 8d47009a4。
- 基准：opt.190 发版提交 52feb9ba8（Host 钉版 6c0906427）。
- 被核内容：`git diff 52feb9ba8 HEAD -- sdk/simple-harness-sdk`，共 78 个文件，+402/−1369 行。来源是阶段 7（宽捕获补日志、写原因）和阶段 8（8C-1、8C-2、8C-3、8B、F-2、F-3）。
- 方法：git、读文件、AST 计算、导入解析、`ruff check`（只读）、`manifest_fresh.py`、`sdk_boundary.py`。没有跑测试套件。没有改源码，没有提交。

## 1. 删除的定义没有调用方，也不在公开接口里 —— 通过

- 用 AST 对比基准与 HEAD 的 SDK 源码。删掉的内容有：2 个模块（`workflow/adapters.py`；`workflows/_registration.py` 改名挪走），以及 8C-1、8C-2、8C-3 清单里的函数、类、方法（共 33 个文件）。删除清单与实际删除逐项对得上。
- 按词边界搜索 SDK src、SDK tests、`backend/`、仓库根 `scripts/`：
  - 34 个被删名字的引用都是 0。
  - 剩下的引用都是同名的别的东西：`report_for` 是测试辅助函数（test_readiness_reasons.py 定义）；`_rebound` 是测试内函数；`_child_review` 只出现在测试文档串；`bind_execution_adapter` 只在 SDK 测试替身 `tests/workflow/_fakes.py:74`（替身多一个方法不影响协议）；`_registration` 是测试辅助函数和 Host 自己的函数。
- 被删方法在别处没有同名定义。所以没有"删掉子类覆盖、退回基类实现"的情况。
- 动态取名：SDK 里唯一的 `getattr(..., f"_{name}")` 只取 `model`、`base_url`，与被删名字无关。
- 导入解析（Host 侧）：用 `backend/.venv` 解释器，把 HEAD 的 SDK 源码放在路径最前，逐个解析 `backend/` 与 `scripts/` 里 1516 条 `from simple_harness…/agent_orchestrator… import …`。只有 1 条失败：`backend/tests/sdk_adapters/test_post_turn_invoker.py:333` 的 `simple_harness.providers.secrets`。这个模块在 opt.190 里也不存在，而且这条导入在 try 里做回退。与本次无关。
- 导入解析（SDK 侧）：SDK src、tests、scripts 共 10262 条导入。只有 1 条失败：`tests/orchestrator/full_target/scripted_plans.py:118` 导入 `root_review.root_requirements`。基准里这个名字也不存在，本次没有改这个文件。与本次无关。
- 公开接口：
  - `simple_harness/public_api.py`、`agent_orchestrator/public_api.py` 本次没有改。
  - 被删的顶层名字都不在任何 `__all__` 或惰性导出表里（`await_tool_result` 不在 `tools/contracts.py` 的 `__all__` 里；`WorkflowProviderAdapter`、`WorkflowEffectAdapter` 只在被删模块自己的 `__all__` 里）。
  - 导入 SDK 全部模块，逐个取 `__all__` 里的名字（惰性导出表也由此触发）：0 个失败。
  - `scripts/code_quality/sdk_boundary.py`：公开模块 83 个，通过。
- 说明（不阻断）：公开模块里有几个类删了方法（`ArpError.to_error_json`、`RuntimeToolCatalog.audit_summary`、`WorkflowRegistry.bind_transaction_owner`、`ObjectSchema.field_named`、`DeploymentPlanningWorld.type_for`）。Host 不用这些方法。Host 是唯一使用方，所以钉 opt.191 不会崩。

## 2. 8B 两处合并，行为不变 —— 通过

### 2.1 工作流登记构造

- `workflows/_registration.py` → `workflow/registration.py`：git 判为 95% 相似。唯一改动是模块文档串。导入全是绝对导入，挪目录不改变解析结果。
- 惰性导出 `"build_registration": (".registration", "build_registration")`。`__getattr__` 对点开头的路径用 `import_module(module_name, __name__)`。在新解释器里核对：`simple_harness.workflow.build_registration` 与 `simple_harness.workflow.registration.build_registration` 是同一个对象。
- 三个官方工作流工厂（capability_build、durable_task、personal_v1）只改了导入行。在新解释器里分别单独导入，都成功，没有循环导入。
- 工作流身份不变：实现身份哈希只算定义、状态、提示词、工具、策略、节点与路由函数源码、依赖锁（`workflow/definition.py` 1401～1416 行）。工厂和登记模块不在其中。
- 说明（不阻断）：Host 的 `backend/deskpet/sdk_adapters/workflows.py:63` 仍有自己的 `_registration` 副本。实施记录第 44 行写明钉版后再改。

### 2.2 两份保证审阅抽出的共用函数

逐句对比 `assurance_review_pins.read_review_materials`、`review_recheck` 与两处原代码：

- 材料读取：
  - 非原始字节项按 `item.body_json.encode()` 放入，过滤条件 `item.ref not in blob_refs` 相同。
  - 原始字节项按 `ref.key` 排序后逐个读。`maximum_bytes=remaining` 在读之前传入，读完再 `remaining -= len(blob.data)`。扣减顺序相同。
  - 授权回调：原来是 `lambda r: _permission(authority, identity, r, int(store.now * 1000)).access`，现在是 `lambda r: _permission(authority, identity, r, now_ms()).access`。`now_ms` 由调用方传入 `lambda: int(store.now * 1000)`。两者都在每次回调时取时间。时机相同。
  - `now_ms=` 参数原来就是同一个 lambda。
- 提交前复查：
  - 闭包体与原来逐字相同：先取 `current_ms`，再依次核根权威、`require_epochs_locked`、逐项元数据、`_require_same_permission`、逐个原始字节 `require_current_locked`。错误码都是 `RECHECK_REQUIRED`，没有改。
  - 取值时机：原闭包晚绑定外层变量，新闭包在调用 `review_recheck` 时绑定。两处函数里，从定义闭包到 `commit.ensure_assurance_review_invocation(...)` 之间没有对 store、gate、root、mission_id、epochs、metadata、reader、permissions、authority、identity、blobs 的重新赋值。`metadata`、`permissions` 都是 tuple，可以多次遍历。`store.connection`、`store.now` 仍在闭包执行时读取。所以行为相同。
  - 两处调用都仍在原来的 `try … except Exception: release_failed_preparation(...); raise` 里面。
- 源码文字测试：
  - `test_no_barrier_compares_the_whole_permission` 扫 `assurance*.py` 里迭代 `.permissions` 属性的循环。新函数迭代的是名字 `permissions`，不会被判违规。
  - `test_a15_binding_writers_take_the_round_from_the_counter` 要求的三段文字仍在两份审阅文件里。
  - 改坏清单 `mutations.json` 涉及本次改动文件的 3 条（TG4-08、AS-M13、A11-01）：原文片段在 HEAD 里都只出现 1 次。
  - SDK 与 Host 测试里没有对这三个模块的打桩。

## 3. F-3：accept 删掉 producer_agent_ids 参数 —— 通过

- 基准里这个参数在函数内第 425 行被 `producer_agent_ids = (projection.producer_agent_id,)` 覆盖，覆盖前没有读过。HEAD 里这一行仍在（第 424 行），后面的用法（第 436、488、604 行）不变。所以作者仍以投影为准。
- 用 AST 搜索 SDK src、SDK tests、`backend/`、`scripts/` 里所有 `.accept(... result_id=...)` 调用：只有 `commit_service_domains/results.py` 的 2 处（第 726、904 行）。两处都已去掉这个参数。没有用 `**kwargs` 传参的调用。

## 4. 阶段 7 的 SDK 改动没有改变控制流 —— 通过

- 列出 SDK 里所有改动过的 `except`、`raise`、`return`、`pass`、`continue`、`break` 行：
  - 保留下来的 `except` 子句，捕获的类型都没变。只改了行尾注释（英文原因改成中文原因）。
  - `pass` 都换成了 `logger.debug(..., exc_info=True)`。没有新增 `return`、`continue`。没有把 `raise` 改成记日志。
  - 其余删掉的控制流行都属于被删的函数（drone_sim、appworld_resume、root_review、plan_commits、taskgraph_review_evidence、operation_profiles 等），或第 2 节的合并代码。
- 新增的日志都是标准库 logger（模块内 `logging.getLogger(__name__)`，或从 `kernel_domains/support.py`、`orchestrator_domains/support.py` 导入的标准库 logger）。只传事件名与 `exc_info=True`。标准库日志出错时只调 `handleError`，不会抛出。
- `pumps.py` 两处原有的 `logger.warning(..., extra=...)` 只是补了 `exc_info=True`。
- `ruff check --select F821,F811,F841,F401,F823`，对全部改动的 SDK 文件运行：全部通过（没有未定义的 logger，也没有删出来的无用导入）。
- `print` 改动只加了 `# noqa: T201` 注释。`testing/cli.py` 在原有 print 前加了一条 debug 日志，返回值不变。
- 带日志断言的 SDK 测试（observability、memory_outbox、runtime 日志）都在 INFO 级或指定 logger 上取记录。新日志是 DEBUG 级，不会进入这些记录。

## 5. 部署清单与源码字节一致 —— 通过

- `python3 scripts/code_quality/manifest_fresh.py` 输出"部署清单：最新"，返回码 0。
- 清单的源文件表随改动更新：去掉 `workflows/_registration.py`，加入 `workflow/registration.py`；三个工厂的哈希更新；`deployment_id` 重算。
- 其它带文件哈希的清单（`graph/network_codec_manifest_v1～v5.json`、Host 的 `pool_identity_baseline.json`、`execution_build_sources.json`）都不含本次改动的文件。
- 验收状态现为 `taskgraph_acceptance: "NOT_RUN"`、`acceptance_evidence: null`。这是重生成后的预期状态，与 opt.190 发版前一样。见下面的发版前提。

## 发版前提（不是阻断，必须照做）

1. `src/simple_harness/version.py` 仍是 `0.13.0.dev20260925+opt.190`。顺序必须是：改版本号为 opt.191 → 重生成部署清单 → 跑 `scripts/acceptance/taskgraph_gate.py` → 用 `taskgraph_manifest.py validate` 写回 `VALIDATED` → 打包。
2. 原因：Host 的编排服务在清单不是 `VALIDATED` 时拒绝启动（`taskgraph_acceptance_not_validated`）。带着 `NOT_RUN` 打包，产品里编排起不来。
3. 打包后再跑一次 `manifest_fresh.py`。
4. 钉版后再把 Host 的 `sdk_adapters/workflows.py` 改用 `simple_harness.workflow.build_registration`，删掉自己的副本（8B 己组合并的后一半）。
5. `plans/2026-10-12-code-quality/阶段8-实施记录.md` 目前没有入库（git status 显示未跟踪）。提交时一起加上。

## 必须处理

无。

## 总结论

PASS
