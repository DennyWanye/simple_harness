# N1 同机共享模型容量接纳

最后更新：2026-09-15 05:53 CST。SDK完整回归2302PASS/32条件SKIP（669.22秒），620源/测试hash不变。原生v55发现前台None输出参数被误拒绝，0物理调用；修复后Host12定向PASS/0.13秒，新原生复验与真实多进程待验。N1–N8整体仍OPEN，Flash0。

同一个规范化local profile文件的相邻capacity-v1.sqlite3为共同账本，物理endpoint作为池身份（模型别名不另开容量），上限2槽/393216在途tokens。配置冲突拒绝，重复同配置绑定幂等，冲突嵌套绑定在出站前拒绝。主对话保守预留整个262144服务窗口，编排/实验使用已绑定tokenizer的最终wire输入加输出上限。两个执行器内部2槽不再等于两份独立总容量。

SDK ProviderInvocationCoordinator在记录handoff之前等待共享容量；BaseAgent恢复tool_calls之后计数。CapacityProvider和MeteredProvider的同task/同请求嵌套共用一次grant，评测等待取消计为0物理调用，既有Task/Mission预算身份及冻结旧请求不改。排队被标为非计费slot wait，保留原有stall与取消纪律。

SQLite FIFO/短事务保存WAITING、RESERVED、HANDED_OFF、UNKNOWN和终态；随机owner epoch防陈旧回调。进程终止或PID复用只释放前置状态，出站状态转UNKNOWN继续占槽与tokens，绝不靠TTL放行。PID+OS进程创建时间核验依赖SDK新local-capacity可选extra psutil（本机7.2.2，uv.lock已锁定）；身份不可读时保持保守，旧缺创建时间条目也不猜测释放。

可信恢复根据同一SDK数据库namespace、invocation和handoff ordinal核对：存在完整terminal usage、明确CONFIRMED_NOT_STARTED，或容量标记后SDK handoff未提交的CLAIMED记录才可清理；对账proof只存opaque identity/version。复制库到另一目录不能释放原库容量。该对账接口不是模型工具。

范围：只约束采用同一profile/账本的同机客户端；直接HTTP、另一个profile文件或其他电脑不受它约束。当前不改DGX服务配置、不声称跨机器全局限流。源码UI仍由完整launcher管理唯一Vite/backend，不打包。

|定向检查|结果|实测时间|
|---|---|---|
|SDK原语/出站/Orchestrator/计量/恢复/历史结果合同|40PASS；含进程杀死、PID复用、取消、缺usage未知保持、同DB可信对账、错DB拒绝、实际runtime嵌套一次占用|2.59秒|
|Host本地profile/主对话与编排共池/绑定幂等及原LAN请求|15PASS，配置不符拒绝、原provider身份保持|0.25秒|
|静态检查|5个SDK源mypy通过；Ruff通过|工程未独立计时|

保留初始失败：父级把Agent.run_id(str)当RunId.value导致真实runtime用例失败，已修正；Host测试夹具空messages先于输出cap校验失败，已补真实消息。独立审查发现重复绑定自锁和PID复用陈旧队列，已新增回归与修复，未通过放宽断言隐藏。

真实协议预声明：Qwen256K；三个160K输入进程（最多2并行），两个210K输入进程（加权后串行），每请求输出上限4096、超时600秒；先查服务idle，未知用量停止后继接纳。具体任务文本、源hash和实际server usage将在运行前后存本机ignored证据，不把预声明当已执行。

前台真实缺口：ProviderRequest.max_output_tokens=None表示下游绑定adapter管理输出，并非没有服务总窗口。容量层仅在前台整窗262144预留分支接受None；精确tokenizer分支仍必须有明确正整数输出上限。原生v55拒绝前handoff=0、provider_calls=0保留；新反例先FAIL，再12PASS。

## 实机发现的校时问题

最后更新：2026-09-15 06:01 CST。N1真实三进程v1为FAIL：两次物理调用321104tokens、0新增抢占，第三路前置排队被错误释放（已知0出站）。系统校时影响psutil.create_time造成身份误判，旧源3反例FAIL；改为psutil稳定process hash（>=7.2.2），新25PASS/1.85秒，完整及实机后继待验。N1–N8仍OPEN，Flash0。

稳定身份使用psutil公开Process hash；macOS/Linux由内核单调创建身份组成，Windows使用创建身份。unsupported平台/旧缺identity记录保守保持，只在PID确定不存在时按原状态恢复；新列process_identity与旧process_started分开，禁止跨算法比较。散列碰撞只会保守保留，不会误释放。真实v1原始结果不改写；第三路无usage是出站前拒绝，不是物理调用未知，修正审计另存parent-audit.json。


## 当前真实验收与后继（2026-09-15）

真实五调用 v2 已通过：三个约160K输入进程按FIFO最多双路，两个约210K输入按加权容量串行；5物理调用904061 tokens，模型阶段428.455秒（含准备外层499.215秒），峰值2槽/328184 tokens，服务抢占增量0，全部结算，最终服务与容量空闲。源码SDK e80cfa8、Host a7518d19；后继SDK修改未重跑该五调用，不能错标为最新源码测得。

原生v57暴露本地长响应超过180秒被误当executor_stalled；保留取消FAIL，9次物理调用56000 tokens全结算。SDK8b5cbc1为实际provider await增加有界600秒响应等待，并以真实在途状态标记billable blocker，不伪造progress、不改变全局stall规则；超时仍保持未知占用。新27定向PASS，旧Bridge反例FAIL。完整编排＋共享容量/出站专项2308PASS/32条件SKIP，668.54秒，620输入hash不变（不是全SDK测试）。

原生v58（同原题/预算/成功条件）没有再次停滞：Task COMPLETED、code_test真实83PASS，三产物VERIFIED；Mission FAILED/mission_criteria_unmet。7次Mission＋1次前台物理调用50661 tokens，全部已知；另一个最终Critic前置拒绝0出站。阶段463.944秒；共享峰值2槽/271581 tokens，0抢占、最终归零。观察器all_grants_settled=false来自该合法RELEASED前置拒绝，不是未知用量。累计本轮续作285本地调用、6762328已知tokens下限、早先1未知保持，Flash0。

最终Critic未运行的根因：预留6000不足实际9894请求，扩展时误进入Task保护尾额度路径、要求不存在的Attempt。SDK f122b8c限定合法Mission judge和同Mission账户后回到普通额度增长；不改变总预算、不放开Task或外来账户。旧源1FAIL、新关联47PASS/5.05秒、ruff/mypy通过。完整v4及同题源码原生v59正在运行，未计完成。

测试快照重复源码采用APFS克隆去重，逐文件SHA256核对不变，没有删除原始证据；空间从2601754624恢复8203677696 bytes，8.725秒。新版快照复用不变Host、单独SDK和Python环境；UI由源码launcher启动，持续防熄屏。

|当前部分|状态|实测时间|
|---|---|---|
|跨进程容量五调用v2|PASS；2路与大输入串行，0抢占|428.455秒模型阶段|
|前台None输出修复|真实回复PASS；计量使用SDK账本|12定向测试0.13秒|
|校时导致错误进程身份|旧3反例FAIL、新25PASS；真实v2PASS|定向1.85秒|
|长响应误停滞|旧原生FAIL保留、v58无停滞|27定向2.58秒；原生463.944秒|
|最终Mission评审额度|新关联47PASS；新原生待|5.05秒；实现未独立计时|
|完整编排＋容量/出站专项v3|2308PASS、32条件SKIP；新v4待|668.54秒|

所有测试时长与工程时长分列，重叠运行不相加。额外旧SDK dispatch专项78PASS/3.58秒；2个Memory互操作文件因缺兼容依赖未收集，不计PASS，artifact wheel项按不打包范围排除。

|本轮增量|结果|时间|
|---|---|---|
|共享容量与实机问题修复|四个实机暴露点分别保留失败和后继测试|工程未独立计时|
|独立子代理25–27|均已收回；审查结果/返工见私有模型报告|598.49/208.60/418.58秒，存在重叠|
|当前UI同题复验|v57取消、v58评审前拒绝，v59待终态|v58阶段463.944秒|
|源码和证据核对|v3回归620hash不变；原始证据本机保存|未独立计时|

整体八包及剩余时间沿用[后续路线](testPhase1-remaining-roadmap-2026-09-14.md)和[两轮执行](testPhase1-two-wave-execution-2026-09-14.md)。N1–N8尚无整个工作包关闭，A96/B96未开始；本次容量功能局部成功不等于正式评测完成。

### 证据索引（均相对本机项目根）

- `.local-test-evidence/2026-09-15/shared-capacity/real-process-v2/summary.json` — SHA256 `a007f6992b0079a6ddd487fa9ecedaaa6b9702da75561506cceb2d6e7f18c7d8`
- `.local-test-evidence/2026-09-15/shared-capacity/full-v3/command.log` — SHA256 `a8bdd5a526d29099446a3bf9d8d20b002d5ff7f2108d99687eb7ceb1068a904f`
- `.local-test-evidence/2026-09-15/shared-capacity/source-after-full-v3.json` — SHA256 `7f9281fa7ed1405b01f4c15b40236e341a432f8fbba45868edd97fcdf3d40bd4`
- `.local-test-evidence/2026-09-15/shared-capacity/native-wave-a-context-v57/failed-mixed-audit.json` — SHA256 `5b78274db4b11b1e0efc3567afde9f34b3a7ca3702f0e5a4186abdc0a318a989`
- `.local-test-evidence/2026-09-15/shared-capacity/native-wave-a-context-v58/failed-judge-audit.json` — SHA256 `f4674f008b4247e81c12079fc8bd6513fd95f32edf467b7f418dd1a97b0ca8d6`
- `.local-test-evidence/2026-09-15/shared-capacity/native-wave-a-context-v58/mission-criteria-failed.png` — SHA256 `0fce3b4b9bdbba53b24d81b61f3d47a0ce8f8a930439af43e3fd301ca45be0a3`
- `.local-test-evidence/2026-09-15/shared-capacity/judge-growth-old.log` — SHA256 `d0fd64c7e3f0c43fddbe7af65dc7135530bf69abe8e444273b0c42312b9276d9`
- `.local-test-evidence/2026-09-15/shared-capacity/judge-growth-v2.log` — SHA256 `5452822f184c9827778c0d3bab091730b2fb6f6cbb1a07cfb5b325d69534c0d3`
- `.local-test-evidence/2026-09-15/shared-capacity/derived-dedupe.json` — SHA256 `75f4cceab3e5bfb52d50a65812a4e9acf636aa1805d8d0ec7a29f450b0850c5e`


### N2 v4 现有记录只读复核（2026-09-15，无新增模型调用）

精确额度链已补齐：Planner为三Task分配1.2M/1.5M/1.3M。Task1实际903889 tokens；Worker当前预留910877、首个Critic受保护预留261120，所以当时可扩额度=1200000−910877−261120=28003。下一请求上界69977需要增量62989，被原Task额度合法拒绝；终态总Mission结算912645，预留归零。不是简单“实际消耗未达1.2M却误拒绝”，也没有提高总预算的理由。

SDK实际工具：38次appworld_execute、workspace_list和workspace_write_file各1；知识表0条，没有独立knowledge_list/read调用，Python调用AST也没有知识读写。Task2/3未启动，因此尚无可复用知识或消费收益证据。发现多次交易/联系人探索，但仅凭调用次数不能认定重复调用都无必要。暂无已证实产品接线缺陷；保留模型/规划失败，不为获得PASS盲目重跑或放宽题目/预算。下一有价值门槛仍是可验证的知识产生与跨Task消费，A96/B96未开始。

本地只读审计0.013秒（非工程时间），不执行保存的Python代码、无网络/模型调用。Host证据`.local-test-evidence/2026-09-15/shared-capacity/n2-v4-retrospective/summary.json`，SHA256 `d9d67e96c5e6732cad763c02f4a6760529626e95b52327c1e5e4a6efb1e9573d`。


### 原生在途状态展示修复

v59实机仍有旧Host投影缺陷：SDK真实provider_response_wait（billable=true,bounded=true）被通用blocked分支显示为“进程退出/UNKNOWN”，但模型持续返回、进程存活。这是展示误判，未触发接管或重试。Host仅排除SDK明确的有界在途等待及原排队等待；真实未知、缺bounded和未识别blocker仍显示UNKNOWN。原定向1FAIL/2PASS，新Host投影36PASS/8.39秒，ruff通过；新Host源码实机复验待。旧证据v59/live-wait-projection-before.png和ui-wait-old.log保留。
