# Procedure Scope 实际接线候选

2026-09-06。自有树 `/Users/denny/projects/simple_harness-corpus-clock`，`feat/procedure-scope-observation`，基线 `1491309f`。本批业务固定356cbdc3／SDK f82c2b8；SDK六项、Host schema/timer/audit三项及实际三Scope一项通过，四项后继边界控未跑。尚未独审接受或合入主候选。[结果与原红](SCOPE-RESULTS.md)。没有改v3/v4分类协议，v5默认由主合并；M618 wheel和旧证据不变。

## 实际路径

`main._build_product_sdk_runtime_stack` → 显式53初始化 → `procedure_use` 注册 → `ProcedureRuntime`／`ProcedureUseStore`。工具可由模型提交精确memory_id/revision与有序原步骤、工具和参数；这些只是使用提议，不含authority、success或effect许可。SDK公开读取精确owner/current revision，返回步骤hash、风险、qualification epoch等；Host核原文步骤hash、实际冻结工具catalog/当前handler/schema及真实workspace身份。

primary Run初始authority保持projectless。实际Scope与环境来自该次ToolContext中的route receipt，通过既有ContextRouteLedger和WorkspaceBindingAuthorityStore回读exact binding/current head/唯一root、真实文件系统身份与当前Scope状态。没有把初始projectless authority重写成project authority。环境fingerprint不纳Scope局部root ID或binding计数，保留实际canonical path及filesystem identity；工具fingerprint纳有序工具、版本、schema、handler identity与effect class。

`ProductEffectExecutor`保留原EffectGate、foreground admission和evidence reservation，在最终execution scope内核SDK当前目标和真实route并预约下一步；并发调用不能同时取得一个步骤。SDK实际settled effect匹配call/run/参数后才登记步骤结果，旧终态effect replay不再次做物理准入。普通门拒绝发生在步骤预约之前。预约本身不表示执行成功，也不代替原SDK授权。

后台现有short worker先公开注册真实完整conversation group，再调用Procedure观察；cache命中仍回读观察以恢复丢ACK。resolver再次验证Host真实COMPLETED terminal、整个消息组、逐步实际Tool causal parent/call/run/Scope、真实effect state与最后一步terminal receipt。仅所有绑定步骤真实成功才能向SDK申请预备并签发来源authority；助手说成功、少一步、错Scope、工具失败都不能增加成功数。

SDK预备通过共同资格算法得出当前transition，Host不得猜。消费重新计算；同一程序qualification epoch/applicability的三独立Scope、90天、LOW且可逆规则沿用SDK。观察会生成新物理revision，但不改程序的qualification epoch；高风险不会因三次工具成功自动激活。实际使用仍需要原工具权限，ACTIVE记忆不签effect。

## 持久边界与兼容

新增53仅含 `procedure_uses`、`procedure_use_reservations`、`procedure_use_effects`、`procedure_observation_journal` 四个追加事实表，旧DDL/行/receipt不改。同事务发布新DDL、恢复registry/fence、migration hash和user_version。普通startup最大已知组合显式到53；老timer事务接受的是完整验证53，不是任意更大整数。S5cStore在52/53都使用真实v52 cursor；旧cursor继续封存。53缺表/丢fence/未知版本必须拒绝。主正在处理的source/input permission和v5分类无此处改动。

Host生产运行需要同次整合的SDK successor：`prepare_procedure_observation`返回 `PreparedProcedureObservation.intent`；`read_procedure_use_target`返回 `ProcedureUseTarget`；`record_procedure_observation`保持原消费结果wire/hash。main在升级53之前检查公共方法能力，不能将本叶直接装到旧M618环境后宣称可用。版本和制品由主统一整合Hegel input_visibility后分配，不各建同版本不同wheel。

## 操作审计

上述三个SDK public调用均由 `ProcedureOperationJournal`直接调用，捕获公开 `ProcedureOperationObservationV1`，核operation/request/owner/实际返回intent、target或消费result commitment；成功、拒绝、错误和取消写已有 `operation-audit.db` 的 `memory_call_attempts`，不另造业务真相表。业务观察journal仅持久原authority/ref和实际消费结果，不承担SDK审计权限。

`persistence_status=host_persistence_unverified`原值保留；sidecar证据是Host直接捕获的调用证据。旧OA1 sealed public page仍未枚举Procedure历史消费family，不能声称旧历史全覆盖或将一次sink事件视作sealed receipt。sidecar写失败保留实际SDK成功/异常，并报告审计缺口；不为了补日志重发业务操作。

## 必要控及仍未闭合项

- SDK：精确读取／异主体／stale／typed正文篡改；prepare三Scope共享决策无提前消费；真实消费与重放的观察、错误及取消。只测后继六项，不跑旧SDK suite。
- Host：真实52非空cursor→53故障回滚／重开／新cursor续写；真实已prepared timer在53消费；真实primary route→两次物理file effect→完整source group→三Scope公开资格；操作audit绑定与operation替换反控。测试文件位于 `backend/tests/memory/test_procedure_{schema,scope_runtime,operation_audit}.py`；实际执行结果和四项新增未跑控见SCOPE-RESULTS。
- 执行前schema／workspace漂移、同Scope再次执行、新旧revision交错及前置门拒绝的物理0控制还需补齐；不将未跑的测试记作绿。
- 当前prepared authority超时且尚未被SDK消费时仍缺可持久续签恢复；不能改旧ref或凭本地ACK猜已消费。并发Scope绑定旧revision后被另一个观察推进head时，当前保守拒绝stale；未实现跨revision重基。两点尚未闭合，整叶不能以三Scope正控替代完整恢复验收。
- 当前只对全部成功的已绑定步骤归因；工具错误并不自动证明Procedure缺陷。自动失败修订和首次UNBOUND草稿的产品发现链仍需后继真实来源接线，不以recall命中当全部TC-HM04完成。

资源：仅必要新/红控使用145默认锁，末批PG11070已退出且remaining=[]。共享槽留主native，继续代码/独审准备。不运行模型、native、build或安装；原vendor临时链接已恢复。F01明确延期。
