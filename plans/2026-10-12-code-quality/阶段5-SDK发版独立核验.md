# 阶段 5 SDK 发版独立核验（5G 解环、5E 类型清零、opt.189 发版准备）

日期：2026-10-16
核验员：独立子代理（只读，模型 Opus 5.5）
口径：只报阻断级问题（会改变运行行为、会让产品或测试坏掉、不该发版）。

## 核验范围

- 工作树 `simple_harness-cq`，分支 `cq-phase5`，HEAD 4589e5630。main（c1f314c95）是 HEAD 的祖先。
- 5G：`9bf8fa385^..d344f27f7`（三批）加记录 98ce1079a、测试修正 a2d138024。SDK 源码 86 个文件。
- 5E：`4589e5630^..4589e5630`。SDK 源码 121 个文件（另有 pyproject 与工具脚本）。
- 5A～5D 不在本次范围。
- 按要求没有跑 pytest、mypy、ruff，也没有导入任何模块。只用 git、读文件、纯 AST 分析，外加只读的 `ast_equiv.py` 与 `manifest_fresh.py`。
- 辅助脚本都放在会话临时目录 `scratchpad/verify5/`（另有几个早期脚本在临时目录根下），没有写进仓库。

## 一、5G 解环

### 1.1 搬家后没有名字丢失 —— 通过

- 把 HEAD 的 SDK 全部 744 个模块做静态解析：每条绝对、相对、顶层、函数内的 `from X import a`，都核对 X 存在，且 a 是 X 的顶层名字或子模块。跳过 `if TYPE_CHECKING:` 块，只认运行期名字。结果 0 处缺失。脚本先用一条人为写坏的导入（从 taskgraph_store 取 TaskGraphBindingError）验证过，能报出来。
- 5G 从原模块删掉的名字：NotBoundError、require_bound、KERNEL_VERSION、KernelUnsupportedError、CompositeSink、CORE_SOURCES、RUNTIME_BOUNDARIES、_LIFECYCLE_NEXT、read_binding_lifecycle、set_lifecycle、start_input_for、managed_elsewhere、read_blob_metadata、DDL、ACTION_PREFIX、BOUND_ARTIFACT_FIELDS、CANDIDATE_FIELDS、parse_action_criterion、两个叶子判据常量等。全仓库（SDK 源码与测试、backend、scripts）找 `旧模块.名字` 的属性访问、字符串路径、打桩字符串：0 处。
- 对 SDK 测试、SDK 脚本、backend、scripts 共 2534 个文件做同样的解析，外加 `"agent_orchestrator.x.y"` 形式的字符串（打桩路径、import_module）和模块别名上的 setattr 与属性读取。没有 5G 引起的问题。查出 3 处旧问题，都与阶段 5 无关：
  - `tests/orchestrator/full_target/scripted_plans.py:118` 函数内导入 `root_review.root_requirements`。该函数在阶段 5 开始前（eb7491046）就已删除，是旧的死代码。
  - `tests/orchestrator/full_target/test_role_prompts_single_copy.py:30` 断言 `planning.htn.synthesis` 不存在，本来就是"必须不存在"。
  - `backend/tests/sdk_adapters/test_post_turn_invoker.py:333` 的 `providers.secrets` 在 `except ImportError` 回落分支里，不会走到。
- 各模块 `__all__` 列的名字都在运行期存在（全部 744 个模块，0 处缺失）。
- 唯一一个改路径的模块 `execution/sqlite/context_use_requirements.py → execution/context_use_requirements.py`：内容不变（R100），源码与 backend 里没有旧路径的引用。只有工具基线 `scripts/code_quality/startup_profile_baseline.json` 还写着旧模块名，只给启动耗时工具用，不影响产品与测试。

### 1.2 下沉后的模块级副作用不变 —— 通过

- 搬走的 21 个定义逐个比对语法树：20 个与原定义一致。CompositeSink 只少了一行函数内导入（`from .runtime import SafeEmitter`），因为它现在和 SafeEmitter 在同一个模块里。
- 没有一处定义被复制成两份。旧模块都改成从新位置导入，所以拿到的是同一个对象（类、常量、字典）。
- 日志记录器：5G 范围内唯一一处 `getLogger` 改动是运行内核写死旧名字 `simple_harness.runtime.kernel`，这是修 5C/5D 核验阻断项，方向正确。接收下沉定义的模块（taskgraph_binding、action_schema、contracts/operation_completion、assurance_reads、execution/audit、base_agent/turns、agents/config、arp/skills、observability/runtime）都不用日志记录器，所以搬过来的代码也不打日志。
- 新增的顶层导入：只有一条落在已有的强连通分量里，就是 `tools.sidecar → tools.contracts`（工具合同与旁路换边）。contracts 的顶层导入只剩 `simple_harness.contracts` 与 `.schema`，不会回到 sidecar，所以先导入哪一个都不会撞上"模块只初始化了一半"。d344f27f7 到 HEAD 之间，强连通分量的成员和分量内的边都没有增加。
- 写死模块名、按模块名比对的运行期代码（`workflow/definition.py:346` 用节点函数的 `__module__` 算清单哈希）：5G 没有移动工作流节点函数，不受影响。

### 1.3 人工问答建表语句搬家逐字节相同 —— 通过

- 用 `ast.literal_eval` 取出 9bf8fa385^ 的 `planning_human_store.DDL`，与 112c52895 及 HEAD 的 `planning_human_schema.DDL` 比较：字符串完全相同，长度 478。
- `storage/schema.py:26` 改为 `from .planning_human_schema import DDL as DDL_V24`，迁移拿到的是同一个字符串。

## 二、5E 类型清零

### 2.1 `--typing-only` 复核 —— 通过

- 工作区与 HEAD 的差别只有 3 个 backend 测试（已知的三处修补，未提交），所以 `ast_equiv.py --ref 4589e5630^` 比较的就是 5E 这次提交。
- 结果：SDK 源码 18 个文件不等价，其余等价。另外 5 个不等价文件是工具脚本和 backend 测试，不在 SDK 范围。记录写的是"19 个"，这里实际是 18 个，清单与记录列出的文件一致。

### 2.2 人工确认文件：局部变量改名 —— 通过

逐个函数把改前、改后的语法树并排比对：除了变量名，结构完全相同。新名字在原函数里都没出现过，不会撞名，也不会遮住同名的模块级名字或内置名字。原名字在改后仍出现的位置，逐个看了数据流：

- certificates `__post_init__`：两个独立循环，后一个的 value 改成 number。前一个循环不受影响。
- assurance_review_consumer `prepare`：第 385、400 行的 prepared 改成 check_use；第 401 行重新赋值的 prepared 和之后第 413、418 行的读取不变。中间没有能跳过第 401 行的路径。
- assurance_review_import `_interpret_reply`：只改了 methods 那个循环（第 552～562 行）。之后对 item 的读取都在推导式里，用的是推导式自己的变量。
- assurance_tick `tick`：settle_ms 只出现在第二段循环（第 267～297 行）里。now_ms 最后一次被读是第 230 行，在第一段循环里，两段循环不嵌套。
- resolution_commits `accept_review`：subject_ref 只出现在第 661～665 行。subject 在第 705 行无条件赋值，在第 735 行被读，中间不经过改名的那段。
- taskgraph_sources `read_seed_structure`：for 循环的 binding 不变；之后的 policy_binding 是新名字，后面没有再读 binding。
- htn/world `build_planning_world`：参数 predicates 在第 448 行先读一次（`declared = tuple(predicates)`），之后改用 predicate_registry。改前第 449 行就是用注册表覆盖了参数，之后的读取全都改了名。
- arp/catalogue `usable`：两个独立的 if 块，ok/why 与 admitted/reason 各管一块。
- workflow/definition 的两个函数：conditional 只用于遍历 conditional_edges 的几个循环。edge 留在遍历 edges 的循环里，都只在各自循环里读。
- durable_task/nodes `tool_execution_handler`：matched 只出现在第 921～937 行。之后的 call 都是推导式变量。前面没有闭包捕获 call。

### 2.3 显式判空 9 处 —— 通过

- 错误码都是已有的：SOURCE_UNAVAILABLE、REVIEW_REPAIR_SOURCE_INVALID、OP_COMPLETION_INPUTS_UNAVAILABLE、CATALOGUE_STALE、WorkspaceError、ContractError、`_require_mission`，在本模块里原来就有。REVIEW_INTENT_UNBOUND 在 assurance_review_import 里是第一次用，但它是保证审阅通道已有的错误码（`assurance_review_transport.py:372/489`），不是新造的。全库没有新增错误码。
- 上游怎么接，与实施记录的表一致：
  - tool_gateway：`_attempt_workspace` 抛 WorkspaceError，会被本函数的 `except WorkspaceError` 接住，转成拒绝回执。表里写了。
  - completion_inputs：同一个函数里已经有 16 处用同一个错误码拒绝，新加的这处与它们走同一条上游路径。
- 表里第 9 行（意图没记执行者编号）是唯一一处把原来不崩的路径改成报错。按代码看，实际走不到：意图的 agent_id 为空时，原代码在下一条判断 `payload.get("agent_id") != intent.agent_id` 处就会抛 REVIEW_TURN_SOURCE_MISMATCH，除非回合记录里的 agent_id 也是空（真实审阅回合做不到）。两个错误码都不在可修复集合里，上游处理相同。不阻断。
- 协议补存根 `WorkflowUnitOfWork.read_decision`：这是普通 Protocol，没有 `@runtime_checkable`，全库没有类继承它，所以不影响 isinstance，也不会盖住哪个实现。
- 类体注解只改了一处 `AssuranceDeploymentPorts.check_policy_projector`（`Callable[[str], None]` 改成 `Callable[[str], object]`），没有涉及 ClassVar 或 InitVar，数据类字段不变。5E 没有在任何类里加出运行期可见的成员（存根都在 `if TYPE_CHECKING:` 里）。

### 2.4 `cast` 第一个参数与运行期未定义名字 —— 通过

- 对 `9bf8fa385^..HEAD` 改过的全部 172 个 SDK 源文件（含 5E 的 116 个），做了一遍"运行期名字解析"：
  - 读到的每个名字都要在当前作用域、外层函数、模块运行期顶层或内置名字里找得到；
  - `if TYPE_CHECKING:` 里导入的名字不算；
  - 有 `from __future__ import annotations` 的文件不检查注解；
  - cast 的第一个参数是普通实参，照常检查。
- 结果：只报出 9 处，都是推导式里海象运算符绑定的名字，属于检查脚本误报。没有一处是 cast 用了只在 TYPE_CHECKING 里导入的裸名字。脚本先拿一个样例验证过：`cast(Bar, x)` 与 `cast(dict[str, Bar], x)` 能报出来，`cast("Bar", x)` 不报。
- 没有 `from __future__ import annotations` 的 5 个改动文件，5G/5E 只改了导入行，不涉及注解求值。

### 2.5 新增运行期导入 —— 通过

- 5E 没有删掉任何运行期导入，也没有新增指向新模块的运行期导入。顶层新加的名字只有几个：execution/audit 加 JsonValue，tools/executor 加 JsonObject，tools/sidecar 加 FrozenJsonValue，workflow/runner 加 DecisionRecord。都来自本文件原来就在导入的模块。
- 4589e5630^ 与 HEAD 的顶层导入强连通分量：成员相同，分量内的边相同。

### 2.6 工作流节点三个文件只改注解、cast、改名 —— 通过

- personal_v1/definition.py、capability_build/factory.py、durable_task/state.py、durable_task/output_contract.py：`--typing-only` 等价。
- durable_task/nodes.py：只有 2.2 里的 call→matched 改名。两处缺属性（effect_type、args_hash）只加了行尾 `# type: ignore[attr-defined]` 和注释，运行行为与改前相同（与改前一样会崩，已按裁决转交）。
- 这三个工作流的清单哈希会变（源码文本变了）。偏差单第四件已裁决接受；backend 和 SDK 测试里没有写死这几个工作流的清单哈希（backend 的 `test_tool_catalog.py` 钉的是工具清单，与此无关）。

## 三、发版准备

### 3.1 执行图部署清单与源码字节一致 —— 通过

- `scripts/code_quality/manifest_fresh.py`：输出"部署清单：最新"，退出码 0。
- 发版脚本 `release_sdk_opt.sh` 改版本号后会重新生成清单、跑验收门。两个 version.py 现在都是 opt.188，与 Host `sdk_candidate.py` 一致，可以按 188→189 发。

### 3.2 src 下没有杂物 —— 通过

- `find` 查 `.ruff_cache`、`.mypy_cache`、`.pytest_cache`、`*.orig`、`*.rej`、`.DS_Store`、`*.bak`：没有。
- `git status --ignored --untracked-files=all sdk/simple-harness-sdk`：除了 `__pycache__` 以外，没有未跟踪或被忽略的文件。`__pycache__` 会被清单工具排除，发版脚本也会清掉。

### 3.3 backend 对阶段 5 搬走或改名的 SDK 内部名字的引用 —— 通过（已知三处以外没有）

- 阶段 5（main..HEAD）SDK 里消失的私有属性、方法共 50 个（如 `_assurance_reviews`、`_recovery_isolated`、`_service_blocked_since`、`_taskgraph_operator`、`_routers`）。没有消失的公开属性。在 backend（产品代码与测试）里查 `.名字` 和字符串 `"名字"`，只命中两类，都与 SDK 无关：
  - `assembler._policies`：Host 自己的类；
  - `service._recovery_isolated()`：Host 自己的方法，内部读的是 SDK 公开的 `recovery_status()`。
- backend 的 24 个打桩目标（`patch("…")`、`monkeypatch.setattr(模块, "名字")`），逐个核对目标模块里这个名字是自己定义的还是转手导入的，模块自己用不用它，有哪些别的模块导入它，并与 main 对比：
  - 没有"只转手导入、本模块不用"的无效打桩。
  - `event_handler.Orchestrator` 在 main 之后多了导入方，但 Host 是通过 `event_handler.Orchestrator(...)` 构造的（`service.py:456/972`），打桩仍然有效。
  - 打在类上的 11 处（Orchestrator.recovery_status、MissionControlV1.assurance_root_diagnostic、SeatbeltExecutor.for_interpreter、ProviderInvocationCoordinator.invoke、EffectExecutor.execute）：方法都在，会顺着继承链找到，打桩有效。
- backend 读 SDK 源码文件路径或调 `inspect.getsource` 的地方：没有指向阶段 5 改过的文件。
- Host 用 getattr 读编排器的属性（durable_watermark、store、taskgraph_read_api、recovery_status）都还在。
- 已知三处，工作区里已经改好（还没提交），改法核对正确：
  - `test_layered_scripted_lane.py` 两处改打 `service_turns.MAX_SERVICE_BLOCKER_SECONDS`，常量定义在 `service_turns.py:42`，第 115 行在用；
  - `test_recovery_isolated_host.py` 改用 `_rounds.recovery_isolated`（`MissionRoundState`，state.py:122）；
  - `test_assurance_host_api.py` 改用 `_wiring.assurance_reviews`（`DeploymentWiring`，state.py:197）。

## 不阻断的提醒

1. 已知三处 backend 测试修补现在只在工作区里，HEAD 上还是旧写法。合并前要提交，否则 Host 钉版后这三个测试会红。
2. `release_sdk_opt.sh` 规定只在主检出 main 上运行。`cq-phase5` 要先合进 main（main 是 HEAD 的祖先，可以快进）。
3. 实施记录写"19 个需人工确认"，`ast_equiv.py` 实际报的是 SDK 18 个，文件清单一致。改记录时顺手订正就行。
4. `scripts/code_quality/startup_profile_baseline.json` 还写着旧模块名 `simple_harness.execution.sqlite.context_use_requirements`。只影响启动耗时工具的对比输出。
5. 显式判空表第 9 行（意图没记执行者）把原来不崩的路径改成报错。按 2.3 的分析实际走不到；如果要严格遵守"只改本来就会崩的路径"，可以只保留 `intent is None` 那一半。

## 必须处理

无

## 结论

PASS
